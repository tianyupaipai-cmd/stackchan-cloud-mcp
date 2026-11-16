"""Run with the gateway dependencies installed: python -m unittest discover -s tests -v."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, ClientTimeout, WSMsgType, WSServerHandshakeError

from cloud.railway_server import Config
from cloud import reflex

ROOT = Path(__file__).resolve().parent.parent


class ConfigurationTests(unittest.TestCase):
    def test_refuses_missing_credentials_or_insecure_public_origin(self):
        for env in ({}, {"STACKCHAN_TOKEN": "test", "STACKCHAN_PUBLIC_URL": "http://example.com"},
                    {"STACKCHAN_TOKEN": "test", "STACKCHAN_PUBLIC_URL": "https://example.com/mcp"}):
            with self.subTest(names=list(env)):
                with self.assertRaises(ValueError):
                    Config.from_env(env)

    def test_railway_domain_wires_public_capture_and_local_gateway(self):
        config = Config.from_env({"STACKCHAN_TOKEN": "test", "RAILWAY_PUBLIC_DOMAIN": "robot.up.railway.app",
                                  "PORT": "9000", "MCP_HTTP_ALLOWED_HOSTS": "existing.example"})
        self.assertEqual(config.env["VISION_URL"], "https://robot.up.railway.app/capture")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.env["HOST"], "127.0.0.1")
        self.assertIn("existing.example", config.env["MCP_HTTP_ALLOWED_HOSTS"])
        self.assertIn("robot.up.railway.app", config.env["MCP_HTTP_ALLOWED_HOSTS"])
        self.assertEqual(config.env["GATEWAY_LOG_SOURCE"], "stdin")

    def test_reflex_can_follow_supervisor_stdin_without_journalctl(self):
        with patch.dict(os.environ, {"GATEWAY_LOG_SOURCE": "stdin"}), \
             patch.object(sys, "stdin", io.StringIO("ESP32 ready: device=test\nsay(): duration_ms=100\n")), \
             patch.object(reflex.subprocess, "Popen") as popen, \
             patch.object(reflex.threading, "Timer") as timer, \
             patch.object(reflex, "schedule_idle") as idle:
            reflex.watch_log()
            popen.assert_not_called()
            timer.assert_called_once_with(reflex.RECONNECT_DELAY_S, reflex.push_avatar)
            timer.return_value.start.assert_called_once()
            idle.assert_called_once_with(100)

    def test_reflex_does_not_report_failed_avatar_upload_as_success(self):
        with patch.object(reflex, "call", return_value={"ok": False, "error": "no_device"}) as call, \
             patch.object(reflex, "restore_settings") as restore, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            reflex.push_avatar()
            self.assertIn("avatar not loaded", output.getvalue())
            self.assertNotIn("avatar set pushed", output.getvalue())
            self.assertEqual(call.call_count, 1)
            restore.assert_not_called()


class MockDevice:
    def __init__(self, client, ws):
        self.client, self.ws = client, ws
        self.initialized = asyncio.Event()
        self.binary_received = asyncio.Event()
        self.commands = []
        self.vision = None
        self.avatar = None
        self.audio = []

    async def run(self):
        await self.ws.send_json({"type": "hello", "version": 1, "features": {"mcp": True}})
        async for message in self.ws:
            if message.type == WSMsgType.BINARY:
                self.audio.append(message.data)
                self.binary_received.set()
                continue
            if message.type != WSMsgType.TEXT:
                continue
            data = json.loads(message.data)
            if data["type"] == "mcp":
                request = data["payload"]
                if "id" not in request:
                    continue
                method = request["method"]
                if method == "initialize":
                    self.vision = request["params"]["capabilities"]["vision"]
                    result = {"protocolVersion": "2024-11-05", "capabilities": {},
                              "serverInfo": {"name": "simulated-stackchan", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": "self.robot.set_head_angles", "description": "Move head",
                                         "inputSchema": {"type": "object"}}]}
                    self.initialized.set()
                elif method == "tools/call":
                    self.commands.append(request["params"])
                    result = {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}
                else:
                    result = {}
                await self.ws.send_json({"type": "mcp", "payload": {
                    "jsonrpc": "2.0", "id": request["id"], "result": result}})
            elif data["type"] == "avatar_set_fetch":
                async with self.client.get(data["url"], headers={"Authorization": "Bearer " + data["token"]}) as response:
                    if response.status != 200:
                        raise AssertionError(f"Public avatar download returned {response.status}")
                    self.avatar = await response.read()
                    checksum = "sha256:" + hashlib.sha256(self.avatar).hexdigest()
                    assert checksum == data["checksum"]
                    assert len(self.avatar) == data["expected_size"]
                await self.ws.send_json({"type": "avatar_set_loaded", "ok": True, "checksum": checksum})


class RailwayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sockets = [socket.socket() for _ in range(4)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in sockets]
        self.ws_port = ports[1]
        for sock in sockets:
            sock.close()
        self.url = f"http://127.0.0.1:{ports[0]}"
        self.token = secrets.token_urlsafe(32)
        self.headers = {"Authorization": "Bearer " + self.token}
        env = dict(os.environ, STACKCHAN_TOKEN=self.token, STACKCHAN_PUBLIC_URL=self.url,
                   STACKCHAN_ENABLE_REFLEX="0", **{k: str(v) for k, v in zip(
                       ("PORT", "WS_PORT", "CAPTURE_PORT", "MCP_HTTP_PORT"), ports)})
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        self.log = tempfile.TemporaryFile()
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "cloud.railway_server", cwd=ROOT, env=env,
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.client = ClientSession(timeout=ClientTimeout(total=15), trust_env=False)
        deadline = asyncio.get_running_loop().time() + 40
        while True:
            try:
                async with self.client.get(self.url + "/healthz") as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if self.process.returncode is not None or asyncio.get_running_loop().time() >= deadline:
                self.log.seek(0)
                output = self.log.read().decode(errors="replace")
                await self.client.close()
                if self.process.returncode is None:
                    self.process.terminate()
                    await self.process.wait()
                self.log.close()
                self.fail("Deployment failed to start:\n" + output)
            await asyncio.sleep(0.1)

    async def asyncTearDown(self):
        if self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 20)
            except asyncio.TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        await self.client.close()
        self.log.close()

    async def initialize(self, path="/mcp"):
        headers = dict(self.headers, Accept="application/json, text/event-stream")
        async with self.client.post(self.url + path, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "kelivo-compatible-test", "version": "1"}}}) as response:
            self.assertEqual(response.status, 200, await response.text())
            self.assertIn("result", await response.json())
            headers["mcp-session-id"] = response.headers["mcp-session-id"]
        async with self.client.post(self.url + "/mcp", headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"}) as response:
            self.assertEqual(response.status, 202)
        return headers

    async def call(self, headers, method, params=None):
        async with self.client.post(self.url + "/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}) as response:
            self.assertEqual(response.status, 200, await response.text())
            data = await response.json()
            self.assertNotIn("error", data)
            return data["result"]

    @asynccontextmanager
    async def device(self):
        async with self.client.ws_connect(self.url + "/ws", headers=dict(self.headers, **{"Device-Id": "test-device"})) as ws:
            device = MockDevice(self.client, ws)
            task = asyncio.create_task(device.run())
            try:
                await asyncio.wait_for(device.initialized.wait(), 10)
                for _ in range(100):
                    async with self.client.get(self.url + "/status", headers=self.headers) as response:
                        status = await response.json()
                        if status.get("initialized") and status.get("tools_count"):
                            break
                    await asyncio.sleep(0.01)
                self.assertTrue(status.get("initialized"))
                yield device
            finally:
                await ws.close()
                await asyncio.wait_for(task, 5)

    async def test_public_health_auth_and_kelivo_mcp_handshake(self):
        for path in ("/mcp", "/mcp/", "/status", "/capture", "/pcm"):
            async with self.client.request("POST" if path in {"/capture", "/pcm"} else "GET", self.url + path) as response:
                self.assertEqual(response.status, 401, path)
        with self.assertRaises(WSServerHandshakeError) as failure:
            await self.client.ws_connect(self.url + "/ws", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(failure.exception.status, 401)
        headers = await self.initialize("/mcp/")
        # A pending SSE stream must not buffer or block concurrent MCP requests.
        sse = await asyncio.wait_for(self.client.get(self.url + "/mcp", headers=headers), 5)
        try:
            self.assertEqual(sse.status, 200)
            self.assertIn("text/event-stream", sse.headers["Content-Type"])
            tools = await self.call(headers, "tools/list")
        finally:
            sse.close()
        self.assertTrue({"say", "listen", "get_status", "move_head"} <= {tool["name"] for tool in tools["tools"]})
        result = await self.call(headers, "tools/call", {"name": "get_status", "arguments": {}})
        self.assertFalse(json.loads(result["content"][0]["text"])["connected"])

    async def test_device_hello_move_head_and_public_capture_address(self):
        async with self.device() as device:
            self.assertEqual(device.vision["url"], self.url + "/capture")
            headers = await self.initialize()
            result = await self.call(headers, "tools/call", {
                "name": "move_head", "arguments": {"yaw": 10, "pitch": 30, "speed": 60}})
            self.assertTrue(json.loads(result["content"][0]["text"])["ok"])
            command = next(c for c in device.commands if c["name"] == "self.robot.set_head_angles")
            self.assertEqual(command["arguments"]["yaw"], 10)
            self.assertEqual(command["arguments"]["pitch"], 30)

    async def test_public_avatar_download_and_opus_audio_relay(self):
        async with self.device() as device:
            headers = await self.initialize()
            archive = ROOT / "sprites/avatar_set.bin"
            result = await self.call(headers, "tools/call", {"name": "load_avatar_set", "arguments": {
                "archive_path": str(archive), "mode": "layered", "timeout": 10}})
            loaded = json.loads(result["content"][0]["text"])
            self.assertTrue(loaded["ok"], loaded)
            self.assertEqual(device.avatar, archive.read_bytes())
            self.assertEqual(loaded["bytes_transferred"], 1536000)
            pcm = bytes(16000 * 2 // 5)
            async with self.client.post(self.url + "/pcm", data=pcm,
                headers=dict(self.headers, **{"X-Sample-Rate": "16000", "Content-Type": "application/octet-stream"})) as response:
                self.assertEqual(response.status, 200, await response.text())
                audio = await response.json()
                self.assertGreater(audio["frame_count"], 0)
            await asyncio.wait_for(device.binary_received.wait(), 3)
            self.assertTrue(all(device.audio))

    async def test_ota_keep_alive_and_service_failure(self):
        for method, body in (("POST", {"version": "1.0"}), ("GET", None)):
            async with self.client.request(method, self.url + "/ota/", json=body) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["firmware"]["url"], "")
        # A failed required child must terminate the supervisor so Railway can restart it.
        lock = Path.home() / ".stackchan-mcp" / f"owner-{self.ws_port}.lock"
        owner = json.loads(lock.read_text())
        os.kill(owner["pid"], signal.SIGTERM)
        self.assertEqual(await asyncio.wait_for(self.process.wait(), 20), 1)


if __name__ == "__main__":
    unittest.main()
