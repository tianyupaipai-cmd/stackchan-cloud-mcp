#!/usr/bin/env python3
"""MCP OAuth 2.1 gateway / MCP OAuth 2.1 网关.

claude.ai custom connectors only accept OAuth 2.1 (DCR + Authorization Code +
PKCE); a static bearer token is not enough. This service implements a minimal
but complete OAuth 2.1 authorization server plus a protected-resource proxy in
front of a local MCP gateway:

  /.well-known/oauth-authorization-server   authorization server metadata
  /.well-known/oauth-protected-resource     protected resource metadata
  /register    dynamic client registration (DCR, RFC 7591) - accepts any client
  /authorize   consent page - checks the gate key, then issues a code (PKCE)
  /token       exchange authorization code + code_verifier for an access_token
  /mcp         verify the bearer access_token, then stream-proxy upstream

Security model: single tenant. /authorize is guarded by one shared gate key
(MCP_GATE_KEY); only someone who knows the key can complete the consent page and
obtain an access_token. Access tokens are kept in memory and are therefore
invalidated by a restart.

Environment variables
---------------------
MCP_GATE_KEY               REQUIRED. Gate key checked on the consent page. The
                           service refuses to start without it.
MCP_ISSUER                 Optional. Public HTTPS origin of this service,
                           default https://mcp.example.com
STACKCHAN_GATEWAY_TOKEN    Optional. Bearer token of the upstream MCP gateway.
                           The upstream enforces its own bearer check, so the
                           proxy rewrites Authorization on the way out; without
                           it the upstream answers 401.
MCP_UPSTREAM               Optional. Upstream MCP gateway origin,
                           default http://127.0.0.1:8767

Requires a Python environment with aiohttp. Listens on 127.0.0.1:8770 (put a
TLS-terminating reverse proxy in front of it). Deploy as a systemd unit named
`stackchan-oauth`.
"""
import os, time, base64, hashlib, secrets, urllib.parse
from aiohttp import web, ClientSession, ClientTimeout

# --------------------------------------------------------------------------
# Configuration / 配置
# --------------------------------------------------------------------------
GATE_KEY = os.environ.get("MCP_GATE_KEY", "")   # consent-page gate key
ISSUER   = os.environ.get("MCP_ISSUER", "https://mcp.example.com")
UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://127.0.0.1:8767")
GW_TOKEN = os.environ.get("STACKCHAN_GATEWAY_TOKEN", "")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8770
MAX_BODY = 64 * 1024 * 1024   # bytes; large enough for avatar archive uploads
UPSTREAM_READ_TIMEOUT = 300   # s; no total timeout, SSE streams stay open

# Hop-by-hop headers that must not be forwarded in either direction.
HOP = {"connection","keep-alive","proxy-authenticate","proxy-authorization",
       "te","trailers","transfer-encoding","upgrade","host","content-length"}

_codes = {}    # auth_code    -> {challenge, redirect_uri, t}
_tokens = {}   # access_token -> issued_at
CODE_TTL = 300                 # s   authorization code lifetime
TOKEN_TTL = 60 * 60 * 24 * 30  # s   access token lifetime (30 days)


def _b64url(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ---------- metadata ----------
async def meta_as(request):
    return web.json_response({
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    })


async def meta_pr(request):
    return web.json_response({
        "resource": ISSUER,
        "authorization_servers": [ISSUER],
    })


# ---------- dynamic client registration ----------
async def register(request):
    """Accept any DCR request and hand back a freshly minted client_id.

    Single-tenant service: the real access control is the gate key on the
    consent page, so registration itself is intentionally permissive.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    client_id = "stackchan-" + secrets.token_hex(8)
    resp = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "redirect_uris": body.get("redirect_uris", []),
    }
    return web.json_response(resp, status=201)


# ---------- consent page ----------
_FORM = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Connect Stack-chan</title><style>
body{{font-family:-apple-system,sans-serif;background:#17130E;color:#F5F2EA;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.c{{background:#241d15;padding:32px;border-radius:16px;max-width:320px;width:86%}}
h1{{font-size:20px;font-weight:500}}input{{width:100%;box-sizing:border-box;padding:12px;
border-radius:10px;border:1px solid #4a3f30;background:#1a1510;color:#F5F2EA;font-size:16px;margin:12px 0}}
button{{width:100%;padding:12px;border:0;border-radius:10px;background:#DD7E57;color:#fff;font-size:16px}}
p{{color:#B4B2A9;font-size:13px}}</style></head><body><div class=c>
<h1>🦀 Connect Stack-chan</h1><p>Enter the access key to authorize this device.</p>
<form method=post action="/authorize"><input type=hidden name=q value="{q}">
<input type=password name=key placeholder="Access key" autofocus>
<button type=submit>Authorize</button></form></div></body></html>"""


async def authorize_get(request):
    q = urllib.parse.urlencode(dict(request.rel_url.query))
    return web.Response(text=_FORM.format(q=q), content_type="text/html")


async def authorize_post(request):
    data = await request.post()
    if data.get("key", "") != GATE_KEY:
        return web.Response(text=_FORM.format(q=data.get("q","")) +
            "<script>alert('Invalid access key')</script>",
            content_type="text/html", status=401)
    q = dict(urllib.parse.parse_qsl(data.get("q","")))
    redirect_uri = q.get("redirect_uri", "")
    state = q.get("state", "")
    challenge = q.get("code_challenge", "")
    code = secrets.token_urlsafe(24)
    _codes[code] = {"challenge": challenge, "redirect_uri": redirect_uri, "t": time.time()}
    sep = "&" if "?" in redirect_uri else "?"
    loc = f"{redirect_uri}{sep}code={code}"
    if state:
        loc += f"&state={urllib.parse.quote(state)}"
    raise web.HTTPFound(loc)


# ---------- token endpoint ----------
async def token(request):
    data = await request.post()
    code = data.get("code", "")
    verifier = data.get("code_verifier", "")
    rec = _codes.pop(code, None)
    if not rec or time.time() - rec["t"] > CODE_TTL:
        return web.json_response({"error": "invalid_grant"}, status=400)
    if rec["challenge"]:
        calc = _b64url(hashlib.sha256(verifier.encode()).digest())
        if calc != rec["challenge"]:
            return web.json_response({"error": "invalid_grant", "error_description": "PKCE fail"}, status=400)
    access = secrets.token_urlsafe(32)
    _tokens[access] = time.time()
    return web.json_response({
        "access_token": access, "token_type": "Bearer",
        "expires_in": TOKEN_TTL, "scope": "mcp",
    })


def _valid_token(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    tok = auth[7:]
    t = _tokens.get(tok)
    return t is not None and time.time() - t < TOKEN_TTL


# ---------- MCP proxy ----------
async def mcp(request):
    """Verify the access token, then stream the request through to the upstream.

    Responses are relayed chunk by chunk so that MCP server-sent events reach
    the client without buffering.
    """
    if not _valid_token(request):
        return web.json_response({"error": "unauthorized"}, status=401,
            headers={"WWW-Authenticate":
                f'Bearer resource_metadata="{ISSUER}/.well-known/oauth-protected-resource"'})
    url = UPSTREAM + request.rel_url.path_qs
    fwd = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    if GW_TOKEN:
        fwd["Authorization"] = "Bearer " + GW_TOKEN
    body = await request.read()
    to = ClientTimeout(total=None, sock_read=UPSTREAM_READ_TIMEOUT)
    async with ClientSession(timeout=to) as s:
        async with s.request(request.method, url, headers=fwd, data=body or None,
                             allow_redirects=False) as up:
            resp = web.StreamResponse(status=up.status)
            for k, v in up.headers.items():
                if k.lower() not in HOP:
                    resp.headers[k] = v
            await resp.prepare(request)
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
            await resp.write_eof()
            return resp


def main():
    if not GATE_KEY:
        print("!!! MCP_GATE_KEY is not set, refusing to start"); raise SystemExit(1)
    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_get("/.well-known/oauth-authorization-server", meta_as)
    app.router.add_get("/.well-known/oauth-protected-resource", meta_pr)
    app.router.add_get("/.well-known/oauth-protected-resource/mcp", meta_pr)
    app.router.add_post("/register", register)
    app.router.add_get("/authorize", authorize_get)
    app.router.add_post("/authorize", authorize_post)
    app.router.add_post("/token", token)
    app.router.add_route("*", "/mcp{tail:.*}", mcp)
    print(f"[oauth] MCP OAuth gateway online :{LISTEN_PORT}  issuer={ISSUER}", flush=True)
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None)


if __name__ == "__main__":
    main()
