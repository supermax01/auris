"""Loopback-only engine: the built interface plus the JSON API.

Same protections as the review server: bound to 127.0.0.1, Host and Origin
checks, a session token on every API call, bounded bodies, no request logs and
a strict Content-Security-Policy. The interface is plain files from a fixed
folder; only hashed asset names and a short allowlist are served.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from auris.app.api import AppSession

BIND_ADDRESS = ("127.0.0.1", 0)
MAX_BODY = 1_000_000
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self'; connect-src 'self'; media-src 'self' blob:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
PERMISSIONS = "microphone=(self), camera=(), geolocation=(), payment=(), usb=()"
ASSET = re.compile(r"^/assets/[A-Za-z0-9][A-Za-z0-9._-]*$")
ROOT_FILES = frozenset({"/favicon.svg"})
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}
NOT_BUILT = (
    b"<!doctype html><title>Auris</title><p>The interface is not built yet. "
    b"Run <code>npm --prefix ui run build</code>, then restart.</p>"
)


def handler_for(session: AppSession, ui_dir: Path | None) -> type[BaseHTTPRequestHandler]:
    root = ui_dir.resolve() if ui_dir is not None else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass  # No patient names, URLs or tokens in logs.

        def do_GET(self) -> None:
            self.respond()

        def do_POST(self) -> None:
            self.respond()

        def respond(self) -> None:
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get("Host") != origin[7:]
                or self.headers.get("Origin", origin) != origin
                or (self.command == "POST" and self.headers.get("Origin") != origin)
            ):
                self.reply(403, b'{"error":"loopback origin required"}')
                return
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self.api()
            elif self.command != "GET":
                self.reply(405, b'{"error":"method not allowed"}')
            elif path in ("/", "/index.html"):
                self.static("/index.html", fallback=NOT_BUILT)
            elif ASSET.match(path) or path in ROOT_FILES:
                self.static(path)
            else:
                self.reply(404, b'{"error":"not found"}')

        def static(self, path: str, fallback: bytes | None = None) -> None:
            suffix = Path(path).suffix
            if root is not None and suffix in TYPES:
                candidate = (root / path.lstrip("/")).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    self.reply(200, candidate.read_bytes(), TYPES[suffix])
                    return
            if fallback is not None:
                self.reply(200, fallback, TYPES[".html"])
            else:
                self.reply(404, b'{"error":"not found"}')

        def api(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= MAX_BODY:
                    self.reply(413, b'{"error":"request too large"}')
                    return
                body = self.rfile.read(size) if self.command == "POST" else b""
                status, result = session.dispatch(
                    self.command, self.path, self.headers.get("X-Auris-Token", ""), body
                )
            except ValidationError as exc:
                status, result = (
                    422,
                    {
                        "error": "validation failed",
                        "details": exc.errors(
                            include_input=False, include_context=False, include_url=False
                        ),
                    },
                )
            except (ValueError, json.JSONDecodeError) as exc:
                status, result = 422, {"error": str(exc)}
            except (KeyError, TypeError):
                status, result = 422, {"error": "invalid request"}
            except OSError:
                status, result = 500, {"error": "could not save on this computer"}
            self.reply(status, json.dumps(result, ensure_ascii=False, allow_nan=False).encode())

        def reply(
            self, status: int, payload: bytes, content_type: str = "application/json; charset=utf-8"
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Permissions-Policy", PERMISSIONS)
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def _stop_when_parent_exits(server: ThreadingHTTPServer) -> None:
    sys.stdin.read()  # The desktop shell holds stdin open; EOF means it has gone.
    server.shutdown()


def serve(
    session: AppSession, ui_dir: Path | None, *, json_events: bool, exit_with_parent: bool
) -> None:
    try:
        with ThreadingHTTPServer(BIND_ADDRESS, handler_for(session, ui_dir)) as server:
            url = f"http://127.0.0.1:{server.server_port}/#token={session.token}"
            if json_events:
                print(json.dumps({"event": "ready", "url": url}), flush=True)
            else:
                print(f"Open {url}", flush=True)
                print("Auris engine. Keep this URL private. Ctrl-C to stop.", flush=True)
            if exit_with_parent:
                threading.Thread(
                    target=_stop_when_parent_exits, args=(server,), daemon=True
                ).start()
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
