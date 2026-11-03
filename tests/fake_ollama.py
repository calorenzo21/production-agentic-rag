"""Speaks enough of Ollama's HTTP API to check the connection in CI, where no model runs."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RESPONSES = {
    "/api/version": {"version": "0.0.0-fake"},
    "/api/tags": {"models": []},
}


class FakeOllama(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = RESPONSES.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body or {"error": "not found"}).encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    ThreadingHTTPServer(("0.0.0.0", port), FakeOllama).serve_forever()  # noqa: S104
