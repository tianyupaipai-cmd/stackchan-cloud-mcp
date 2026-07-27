#!/usr/bin/env python3
"""Fake OTA server — 让设备的开机版本检查立刻通过。

xiaozhi-esp32 系固件开机时会先请求一个 OTA 端点问"有没有新固件"。
把设备指向自建服务器后，原厂 OTA 地址要么不可达、要么不该用了；
这时开机检查会一直等到超时，表现为**设备开机后迟迟不连网关**
（弱信号下尤其明显，可能拖到一两分钟）。

这个服务永远回答"你已经是最新版"，让设备秒过检查直接进入连接流程。
它同时回一个服务器时间，设备用它对时。

监听 0.0.0.0:8768（可用环境变量 FAKE_OTA_PORT / FAKE_OTA_HOST 改）。
设备侧要把 OTA 地址指向 http://<本机地址>:8768/ ——
具体在哪配取决于你的固件（通常在配网页面或 config 里）。

注意：这不是"绕过"任何东西，只是在你自建的部署里替掉一个你不再使用的
云端检查点。设备固件仍然是你自己刷进去的那一份。
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("FAKE_OTA_HOST", "0.0.0.0")
PORT = int(os.environ.get("FAKE_OTA_PORT", "8768"))

# 报一个足够低的版本号，设备不会认为需要升级；url 留空表示没有可下载的固件。
REPORT_VERSION = os.environ.get("FAKE_OTA_VERSION", "0.0.1")
# 时区偏移，单位分钟（480 = UTC+8）。设备用它设置本地时间。
TZ_OFFSET_MIN = int(os.environ.get("FAKE_OTA_TZ_OFFSET", "480"))

# 读请求的耐心。设备信号弱时 POST 体可能分片很慢到达，
# 默认 HTTP server 超时会把连接掐掉导致设备重试整个开机流程。
READ_TIMEOUT_S = int(os.environ.get("FAKE_OTA_READ_TIMEOUT", "75"))


def make_resp() -> bytes:
    return json.dumps({
        "firmware": {"version": REPORT_VERSION, "url": ""},
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": TZ_OFFSET_MIN,
        },
    }).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = READ_TIMEOUT_S

    def setup(self):
        print("[fake-ota] connection from", self.client_address, flush=True)
        super().setup()

    def _reply(self):
        body = make_resp()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        # 设备会 POST 一份自身信息（MAC、当前版本等）；读干净再回，
        # 否则 keep-alive 连接上的下一个请求会读到残留字节。
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._reply()

    def log_message(self, fmt, *args):
        print("[fake-ota]", self.address_string(), fmt % args, flush=True)


if __name__ == "__main__":
    print(f"[fake-ota] listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
