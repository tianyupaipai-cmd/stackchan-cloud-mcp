#!/usr/bin/env python3
"""One Railway HTTP port for Kelivo MCP, device WebSockets and capture assets."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hmac
import logging
import os
from pathlib import Path
import signal
import sys
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web

from cloud.fake_ota import make_resp

LOG = logging.getLogger("railway")
ROOT = Path(__file__).resolve().parent.parent
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


def port(env: dict[str, str], key: str, default: int) -> int:
    value = int(env.get(key, str(default)))
    if not 1 <= value <= 65535:
        raise ValueError(f"{key} must be between 1 and 65535")
    return value


@dataclass
class Config:
    public_url: str
    port: int
    ws_port: int
    capture_port: int
    mcp_port: int
    reflex: bool
    token: str = field(repr=False)
    env: dict[str, str] = field(repr=False)

    @classmethod
    def from_env(cls, source: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if source is None else source)
        token = env.get("STACKCHAN_TOKEN", "")
        if not token or not token.isascii() or token != token.strip() or any(c in token for c in "\r\n"):
            raise ValueError("Set a non-empty ASCII STACKCHAN_TOKEN without surrounding whitespace")
        domain = env.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        public_url = env.get("STACKCHAN_PUBLIC_URL", "").strip().rstrip("/")
        if not public_url and domain:
            public_url = f"https://{domain}"
        parsed = urlsplit(public_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname:
            raise ValueError("Generate a Railway public domain, or set STACKCHAN_PUBLIC_URL to your HTTPS origin")
        if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("STACKCHAN_PUBLIC_URL must be an origin, without a path, query or credentials")
        ports = [port(env, key, default) for key, default in (
            ("PORT", 8080), ("WS_PORT", 8765), ("CAPTURE_PORT", 8766), ("MCP_HTTP_PORT", 8767),
        )]
        if len(set(ports)) != len(ports):
            raise ValueError("PORT, WS_PORT, CAPTURE_PORT and MCP_HTTP_PORT must be distinct")
        reflex = env.get("STACKCHAN_ENABLE_REFLEX", "1").lower() in {"1", "true", "yes"}
        env.update({
            "HOST": "127.0.0.1", "WS_PORT": str(ports[1]), "CAPTURE_PORT": str(ports[2]),
            "MCP_HTTP_HOST": "127.0.0.1", "MCP_HTTP_PORT": str(ports[3]),
            "VISION_URL": f"{public_url}/capture",
            "STACKCHAN_MCP_URL": f"http://127.0.0.1:{ports[3]}/mcp",
            "GATEWAY_LOG_SOURCE": "stdin", "PYTHONUNBUFFERED": "1",
        })
        allowed = [h.strip() for h in env.get("MCP_HTTP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        env["MCP_HTTP_ALLOWED_HOSTS"] = ",".join(dict.fromkeys([*allowed, parsed.netloc]))
        env.setdefault("STACKCHAN_TTS_ENGINE", "edge-tts")
        env.setdefault("STACKCHAN_EDGE_TTS_DEFAULT_VOICE", "zh-CN-XiaoxiaoNeural")
        env.setdefault("HF_HUB_DISABLE_XET", "1")
        env.setdefault("IDLE_MOTION", "0")
        env.setdefault("AVATAR_SET_PATH", str(ROOT / "sprites/avatar_set.bin"))
        return cls(public_url, *ports, reflex, token, env)


def forward_headers(headers, *, websocket: bool = False) -> dict[str, str]:
    connection_headers = {h.strip().lower() for h in headers.get("Connection", "").split(",")}
    return {
        key: value for key, value in headers.items()
        if key.lower() not in HOP_HEADERS | connection_headers
        and not (websocket and key.lower().startswith("sec-websocket-"))
    }


class Services:
    def __init__(self, config: Config):
        self.config = config
        self.gateway: asyncio.subprocess.Process | None = None
        self.reflex: asyncio.subprocess.Process | None = None
        self.websockets: set[web.WebSocketResponse] = set()

    async def gateway_logs(self):
        assert self.gateway and self.gateway.stdout
        async for raw in self.gateway.stdout:
            print(raw.decode(errors="replace").rstrip(), flush=True)
            if self.reflex and self.reflex.returncode is None and self.reflex.stdin:
                try:
                    self.reflex.stdin.write(raw)
                    await self.reflex.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The child monitor reports the failed reflex process.

    async def ready(self, client: ClientSession) -> bool:
        if not self.gateway or self.gateway.returncode is not None:
            return False
        if self.reflex and self.reflex.returncode is not None:
            return False
        try:
            async with client.get(f"http://127.0.0.1:{self.config.mcp_port}/healthz",
                                  timeout=ClientTimeout(total=2)) as response:
                return response.status == 200 and (await response.json()).get("ok") is True
        except (ClientError, OSError, asyncio.TimeoutError):
            return False


def create_app(services: Services, client: ClientSession) -> web.Application:
    config = services.config
    app = web.Application(client_max_size=64 * 1024 * 1024)

    async def index(_request):
        return web.json_response({"service": "stackchan-cloud-mcp", "mcp": "/mcp", "health": "/healthz"})

    async def health(_request):
        ready = await services.ready(client)
        return web.json_response({"ok": ready}, status=200 if ready else 503)

    async def ota(request):
        # Consume POST bodies so keep-alive requests do not retain unread data.
        await request.read()
        return web.Response(body=make_resp(), content_type="application/json")

    async def proxy(request):
        mcp_request = request.path in {"/mcp", "/mcp/", "/status"}
        if mcp_request and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {config.token}".encode(),
        ):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
        target_port = config.mcp_port if mcp_request else config.capture_port
        relative = request.rel_url
        if request.path == "/mcp/":
            relative = relative.with_path("/mcp").with_query(request.rel_url.query)
        url = f"http://127.0.0.1:{target_port}{relative}"
        response = None
        try:
            async with client.request(
                request.method, url, headers=forward_headers(request.headers),
                data=request.content.iter_chunked(65536) if request.can_read_body else None,
                allow_redirects=False,
            ) as upstream:
                response = web.StreamResponse(status=upstream.status)
                for key, value in forward_headers(upstream.headers).items():
                    response.headers[key] = value
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (ClientError, OSError, asyncio.TimeoutError) as exc:
            if response is not None and response.prepared:
                response.force_close()
                return response
            LOG.warning("Upstream HTTP request failed: %s", type(exc).__name__)
            return web.json_response({"error": "gateway_unavailable"}, status=502)

    async def device(request):
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {config.token}".encode()):
            raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
        try:
            upstream = await client.ws_connect(
                f"http://127.0.0.1:{config.ws_port}/", headers=forward_headers(request.headers, websocket=True),
                max_msg_size=0, autoping=True,
            )
        except (ClientError, OSError, asyncio.TimeoutError):
            return web.json_response({"error": "gateway_unavailable"}, status=502)
        downstream = web.WebSocketResponse(heartbeat=20, max_msg_size=0)

        async def relay(source, target):
            async for message in source:
                if message.type == WSMsgType.TEXT:
                    await target.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await target.send_bytes(message.data)
                elif message.type == WSMsgType.ERROR:
                    break

        tasks = []
        try:
            await downstream.prepare(request)
            services.websockets.add(downstream)
            tasks = [asyncio.create_task(relay(downstream, upstream)), asyncio.create_task(relay(upstream, downstream))]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception():
                    LOG.info("Device connection closed: %s", type(task.exception()).__name__)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await upstream.close()
            await downstream.close()
            services.websockets.discard(downstream)
        return downstream

    async def close_devices(_app):
        await asyncio.gather(*(ws.close(code=1001, message=b"Server restarting")
                               for ws in list(services.websockets)))

    app.on_shutdown.append(close_devices)
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/ws", device)
    for path in ("/ota", "/ota/"):
        app.router.add_get(path, ota)
        app.router.add_post(path, ota)
    for path in ("/mcp", "/mcp/", "/status", "/capture", "/pcm", "/avatar_set/{id}"):
        app.router.add_route("*", path, proxy)
    return app


async def terminate(process):
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=10)
    except ProcessLookupError:
        pass
    except asyncio.TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


async def run(config: Config):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    services = Services(config)
    runner = None
    tasks = []
    log_task = None
    timeout = ClientTimeout(total=None, sock_connect=10, sock_read=300)
    async with ClientSession(timeout=timeout, auto_decompress=False, trust_env=False) as client:
        try:
            services.gateway = await asyncio.create_subprocess_exec(
                "stackchan-mcp", "serve", "--transport", "streamable-http", "--no-mdns",
                cwd=ROOT, env=config.env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            )
            log_task = asyncio.create_task(services.gateway_logs())
            deadline = loop.time() + 45
            while not await services.ready(client):
                if services.gateway.returncode is not None:
                    raise RuntimeError("Gateway exited during startup; see preceding logs")
                if stop.is_set():
                    return
                if loop.time() >= deadline:
                    raise RuntimeError("Gateway did not become ready within 45 seconds")
                await asyncio.sleep(0.2)
            if config.reflex:
                services.reflex = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", str(ROOT / "cloud/reflex.py"),
                    cwd=ROOT, env=config.env, stdin=asyncio.subprocess.PIPE, start_new_session=True,
                )
            runner = web.AppRunner(create_app(services, client), access_log=None, shutdown_timeout=5)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", config.port).start()
            LOG.info("Ready on port %s; MCP %s/mcp; device %s/ws", config.port,
                     config.public_url, config.public_url.replace("http", "ws", 1))
            tasks = [asyncio.create_task(stop.wait()), asyncio.create_task(services.gateway.wait()), log_task]
            if services.reflex:
                tasks.append(asyncio.create_task(services.reflex.wait()))
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if not stop.is_set():
                raise RuntimeError("A required service exited; restarting the container is necessary")
        finally:
            if runner:
                await runner.cleanup()
            await terminate(services.reflex)
            await terminate(services.gateway)
            for task in [*tasks, log_task]:
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in [*tasks, log_task] if task), return_exceptions=True)
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    try:
        config = Config.from_env()
        asyncio.run(run(config))
    except (ValueError, RuntimeError, OSError) as exc:
        LOG.error("%s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
