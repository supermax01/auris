"""Loopback-only HTTP handler for local pages: Host/Origin checks, bounded
bodies, no-store responses and no request logging."""

import json
from http.server import BaseHTTPRequestHandler
from typing import Protocol

from pydantic import ValidationError

BIND_ADDRESS = ("127.0.0.1", 0)
MAX_BODY = 2_000_000


class LocalSession(Protocol):
    token: str

    def dispatch(self, method: str, path: str, token: str, body: bytes) -> tuple[int, dict]: ...

    def close(self) -> None: ...


def handler_for(
    session: LocalSession,
    *,
    page: bytes,
    csp: str | None = None,
    require_post_origin: bool = False,
    invalid_message: str = "invalid request",
    save_failed_message: str = "private save failed",
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass  # No transcript text, URLs or tokens in HTTP logs.

        def do_GET(self) -> None:
            self.respond()

        def do_POST(self) -> None:
            self.respond()

        def respond(self) -> None:
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get("Host") != origin[7:]
                or self.headers.get("Origin", origin) != origin
                or (
                    require_post_origin
                    and self.command == "POST"
                    and self.headers.get("Origin") != origin
                )
            ):
                self.reply(403, b'{"error":"loopback origin required"}')
                return
            if self.command == "GET" and self.path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
                return
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
            except ValueError as exc:
                status, result = 422, {"error": str(exc)}
            except (KeyError, TypeError):
                status, result = 422, {"error": invalid_message}
            except OSError:
                status, result = 500, {"error": save_failed_message}
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
            self.send_header(
                "Content-Security-Policy",
                csp
                or "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)

    return Handler
