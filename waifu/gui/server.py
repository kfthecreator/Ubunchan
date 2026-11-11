"""Tiny localhost-only HTTP server for the avatar page, the .vrm model and TTS clips.

QtWebEngine can't load ES modules from file:// URLs, so the page is served
from 127.0.0.1 on a random port. Only files inside web/ are exposed, plus
/model.vrm (the configured model) and /audio/<id>.wav (in-memory clips).
"""

from __future__ import annotations

import itertools
import mimetypes
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".vrm")


class AvatarServer:
    def __init__(self, vrm_path: str, port: int = 0):
        self.vrm_path = vrm_path
        self._clips: dict[str, bytes] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the console quiet
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/model.vrm":
                    return self._send_file(server.vrm_path, "model/gltf-binary")
                if path.startswith("/audio/"):
                    with server._lock:
                        data = server._clips.pop(path[len("/audio/"):], None)
                    if data is None:
                        return self.send_error(HTTPStatus.NOT_FOUND)
                    return self._send_bytes(data, "audio/wav")
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
                full = os.path.realpath(os.path.join(WEB_DIR, rel))
                if not full.startswith(os.path.realpath(WEB_DIR) + os.sep):
                    return self.send_error(HTTPStatus.FORBIDDEN)
                return self._send_file(full)

            def _send_file(self, full: str, ctype: str | None = None):
                if not os.path.isfile(full):
                    return self.send_error(HTTPStatus.NOT_FOUND)
                with open(full, "rb") as f:
                    data = f.read()
                self._send_bytes(data, ctype or mimetypes.guess_type(full)[0]
                                 or "application/octet-stream")

            def _send_bytes(self, data: bytes, ctype: str):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> "AvatarServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()

    def add_clip(self, wav: bytes) -> str:
        """Store a WAV clip; returns its URL path. Each clip can be fetched once."""
        clip_id = f"{next(self._ids)}.wav"
        with self._lock:
            self._clips[clip_id] = wav
        return f"/audio/{clip_id}"


if __name__ == "__main__":  # handy for debugging the page in a normal browser
    import sys
    import time

    srv = AvatarServer(sys.argv[1] if len(sys.argv) > 1 else
                       os.path.expanduser("~/Downloads/Ubunchan.vrm"),
                       int(sys.argv[2]) if len(sys.argv) > 2 else 8765).start()
    print("serving", srv.url, flush=True)
    while True:
        time.sleep(3600)
