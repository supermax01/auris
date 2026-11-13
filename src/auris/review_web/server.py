"""Reuse annotation Host/Origin checks, bounded bodies and silent HTTP handling."""

import base64
import hashlib
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files

from auris.annotation.server import BIND_ADDRESS
from auris.annotation.server import handler_for as local_handler
from auris.review_web.service import ReviewSession

PAGE = files("auris.review_web").joinpath("page.html").read_bytes()


def _hashes(tag: bytes) -> str:
    return " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(block).digest()).decode() + "'"
        for block in re.findall(b"<" + tag + b">(.*?)</" + tag + b">", PAGE, re.S)
    )


CSP = (
    "default-src 'none'; script-src "
    + _hashes(b"script")
    + "; style-src "
    + _hashes(b"style")
    + "; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def handler_for(session: ReviewSession) -> type[BaseHTTPRequestHandler]:
    return local_handler(session, page=PAGE, csp=CSP, require_post_origin=True)


def serve(session: ReviewSession) -> None:
    try:
        with HTTPServer(BIND_ADDRESS, handler_for(session)) as server:
            print(f"Open http://127.0.0.1:{server.server_port}/#token={session.token}", flush=True)
            print(
                "Local clinician review. Keep this session URL private. Ctrl-C to stop.", flush=True
            )
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
