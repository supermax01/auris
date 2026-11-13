"""Loopback-only stdlib server; no static directory or arbitrary file route."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files

from pydantic import ValidationError

from auris.annotation.adjudication import AdjudicationSession
from auris.annotation.service import AnnotationSession

BIND_ADDRESS = ("127.0.0.1", 0)
MAX_BODY = 2_000_000
PAGE = files("auris.annotation").joinpath("page.html").read_bytes()


def handler_for(session: AnnotationSession | AdjudicationSession) -> type[BaseHTTPRequestHandler]:
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
            ):
                self.reply(403, b'{"error":"loopback origin required"}')
                return
            if self.command == "GET" and self.path == "/":
                self.reply(200, PAGE, "text/html; charset=utf-8")
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
                status, result = 422, {"error": "invalid annotation request"}
            except OSError:
                status, result = 500, {"error": "private save failed; contact coordinator"}
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
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def serve(session: AnnotationSession | AdjudicationSession) -> None:
    try:
        with HTTPServer(BIND_ADDRESS, handler_for(session)) as server:
            print(f"Open http://127.0.0.1:{server.server_port}/#token={session.token}", flush=True)
            print(
                "Human annotation only. Keep this session URL private. Ctrl-C to stop.", flush=True
            )
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
