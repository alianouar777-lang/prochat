import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_client as api  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good":
            return self._json(401, {"error": {"message": "bad key", "code": "invalid_api_key"}})
        self._json(200, {"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3-small"},
                                  {"id": "gpt-4o-mini"}, {"id": "whisper-1"}]})

    def do_POST(self):
        auth = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if auth == "Bearer noquota":
            return self._json(429, {"error": {"message": "quota", "code": "insufficient_quota"}})
        if auth != "Bearer good":
            return self._json(401, {"error": {"message": "bad key"}})
        assert body["stream"] is True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ["سلام", " ", "world"]:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {}}]}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        api.API_BASE = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_stream_text(self):
        out = "".join(api.stream_chat("good", "gpt-4o", [{"role": "user", "content": "hi"}]))
        self.assertEqual(out, "سلام world")

    def test_invalid_key(self):
        with self.assertRaises(api.ApiError) as ctx:
            list(api.stream_chat("bad", "gpt-4o", []))
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_no_quota(self):
        with self.assertRaises(api.ApiError) as ctx:
            list(api.stream_chat("noquota", "gpt-4o", []))
        self.assertIn("insufficient_quota", str(ctx.exception))

    def test_models_filtered(self):
        self.assertEqual(api.list_models("good"), ["gpt-4o", "gpt-4o-mini"])

    def test_stop_event(self):
        stop = threading.Event()
        stop.set()
        self.assertEqual(list(api.stream_chat("good", "gpt-4o", [], stop=stop)), [])

    def test_non_ascii_key_rejected(self):
        with self.assertRaises(api.ApiError):
            api.list_models("مفتاح")

    def test_unreachable(self):
        old = api.API_BASE
        api.API_BASE = "http://127.0.0.1:1/v1"
        try:
            with self.assertRaises(api.ApiError):
                api.list_models("good")
        finally:
            api.API_BASE = old


if __name__ == "__main__":
    unittest.main()
