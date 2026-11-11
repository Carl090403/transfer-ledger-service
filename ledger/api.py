"""HTTP layer (stdlib only): routing, JSON parsing, error mapping and structured logs."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .domain import BusinessRuleError, NotFoundError, ValidationError, format_cents, validate_transfer
from .store import Ledger

log = logging.getLogger("ledger")
MAX_BODY = 8 * 1024
KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def make_handler(ledger: Ledger):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ledger/1.0"

        def log_message(self, *args):  # silence default stderr access log
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-Id", self.request_id)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            log.info(json.dumps({
                "request_id": self.request_id, "method": self.command, "path": self.path,
                "status": status, "ms": round((time.perf_counter() - self.started) * 1000, 2),
            }))

        def _dispatch(self) -> None:
            self.request_id = self.headers.get("X-Request-Id") or str(uuid.uuid4())
            self.started = time.perf_counter()
            try:
                self._route()
            except ValidationError as exc:
                self._send(400, {"error": "validation_failed", "problems": exc.problems})
            except NotFoundError as exc:
                self._send(404, {"error": "not_found", "message": str(exc)})
            except BusinessRuleError as exc:
                self._send(422, {"error": "rule_violation", "message": str(exc)})
            except Exception:  # never leak internals to the client
                log.exception("unhandled error request_id=%s", self.request_id)
                self._send(500, {"error": "internal_error", "request_id": self.request_id})

        do_GET = do_POST = _dispatch

        def _method_not_allowed(self) -> None:
            self.request_id = self.headers.get("X-Request-Id") or str(uuid.uuid4())
            self.started = time.perf_counter()
            self._send(405, {"error": "method_not_allowed", "message": f"{self.command} is not supported"})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

        def _route(self) -> None:
            path = self.path.split("?", 1)[0]
            if self.command == "GET" and path == "/health":
                return self._send(200, {"status": "ok"})
            m = re.fullmatch(r"/accounts/([A-Z]{3}-\d{6})/balance", path)
            if self.command == "GET" and m:
                cents = ledger.balance(m.group(1))
                return self._send(200, {"account": m.group(1), "balance": format_cents(cents)})
            m = re.fullmatch(r"/transfers/([0-9a-f-]{36})", path)
            if self.command == "GET" and m:
                return self._send(200, ledger.get_transfer(m.group(1)))
            if self.command == "POST" and path == "/transfers":
                key = self.headers.get("Idempotency-Key", "")
                if not KEY_RE.match(key):
                    raise ValidationError(["Idempotency-Key header (8-64 chars) is required"])
                transfer, created = ledger.transfer(validate_transfer(self._json()), key)
                return self._send(201 if created else 200, transfer)
            raise NotFoundError("route not found")

        def _json(self) -> object:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ValidationError(["Content-Type must be application/json"])
            raw_length = self.headers.get("Content-Length", "0") or "0"
            if not raw_length.isdigit():  # rejects "abc" and negatives (read(-1) would block until EOF)
                raise ValidationError(["Content-Length must be a non-negative integer"])
            length = int(raw_length)
            if length > MAX_BODY:
                raise ValidationError(["body too large"])
            try:
                return json.loads(self.rfile.read(length) or b"null")
            except json.JSONDecodeError:
                raise ValidationError(["malformed JSON"]) from None

    return Handler


def serve(ledger: Ledger, port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(ledger))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    demo = Ledger()
    demo.open_account("CHQ-000001", "Demo A", 500_00)
    demo.open_account("SAV-000002", "Demo B", 0)
    print("Listening on http://127.0.0.1:8080")
    serve(demo).serve_forever()
