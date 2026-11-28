"""Loopback-only telemetry proxy for BgUtils PO Token requests.

It forwards requests without persisting request/response bodies. For /get_pot,
only aggregate counts, HTTP status, and whether a non-empty poToken was returned
are retained in process memory. The proxy must bind to loopback only.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.request_count = 0
        self.http_success_count = 0
        self.http_failure_count = 0
        self.token_response_count = 0
        self.last_status_code = None
        self.last_request_at = None
        self.last_request_outcome = None
        self.last_token_response_at = None

    def record(self, status_code: int, token_present: bool) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.lock:
            self.request_count += 1
            if 200 <= status_code < 300:
                self.http_success_count += 1
                outcome = "http-success"
            else:
                self.http_failure_count += 1
                outcome = "http-failure"
            self.last_status_code = int(status_code)
            self.last_request_at = now
            self.last_request_outcome = outcome
            if token_present:
                self.token_response_count += 1
                self.last_token_response_at = now

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "requestCount": self.request_count,
                "httpSuccessCount": self.http_success_count,
                "httpFailureCount": self.http_failure_count,
                "tokenResponseCount": self.token_response_count,
                "lastStatusCode": self.last_status_code,
                "lastRequestAt": self.last_request_at,
                "lastRequestOutcome": self.last_request_outcome,
                "lastTokenResponseAt": self.last_token_response_at,
            }


def make_handler(upstream: str, metrics: Metrics):
    upstream = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            # Do not log request paths, query strings, payloads, or response data.
            return

        def _send(self, status: int, payload: bytes, content_type: str = "application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_GET(self):
            if urlsplit(self.path).path == "/stats":
                payload = json.dumps(metrics.snapshot(), separators=(",", ":")).encode()
                self._send(200, payload)
                return
            self._forward()

        def do_HEAD(self):
            self._forward()

        def do_POST(self):
            self._forward()

        def do_PUT(self):
            self._forward()

        def do_OPTIONS(self):
            self._forward()

        def _forward(self):
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self._send(400, b'{"error":"invalid_content_length"}')
                return
            if length < 0 or length > 1024 * 1024:
                self._send(413, b'{"error":"request_too_large"}')
                return
            body = self.rfile.read(length) if length else None
            headers = {}
            for key in ("Content-Type", "Accept", "User-Agent"):
                value = self.headers.get(key)
                if value:
                    headers[key] = value
            url = upstream + self.path
            request = Request(url, data=body, headers=headers, method=self.command)
            status = 502
            response_body = b'{"error":"provider_unreachable"}'
            content_type = "application/json"
            try:
                with urlopen(request, timeout=35) as response:
                    status = int(response.status)
                    response_body = response.read(1024 * 1024)
                    content_type = response.headers.get("Content-Type", content_type)
            except HTTPError as exc:
                status = int(exc.code)
                response_body = exc.read(1024 * 1024)
                content_type = exc.headers.get("Content-Type", content_type) if exc.headers else content_type
            except (URLError, TimeoutError, OSError):
                status = 502
            except Exception:
                status = 502

            is_get_pot = self.command == "POST" and urlsplit(self.path).path.rstrip("/") == "/get_pot"
            if is_get_pot:
                token_present = False
                if 200 <= status < 300:
                    try:
                        result = json.loads(response_body.decode("utf-8", errors="replace"))
                        token_present = isinstance(result, dict) and isinstance(result.get("poToken"), str) and bool(result["poToken"].strip())
                    except Exception:
                        token_present = False
                metrics.record(status, token_present)
            self._send(status, response_body, content_type)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4417)
    parser.add_argument("--upstream", default="http://127.0.0.1:4416")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("Telemetry proxy must bind to loopback only")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.upstream, Metrics()))
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
