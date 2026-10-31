#!/usr/bin/env python3
"""Receives the demo recorder's captures and writes them to disk.

The recorder (tools/demo_recorder.js) runs inside the unlocked dashboard and
POSTs every API response it sees here. Raw captures contain private data
(tokens, real balances) until build_demo.py cleans them, so they are written
OUTSIDE this repo by default and never committed.

    python3 tools/demo_record_server.py [out_dir]     # default ~/Desktop/ohlcvlabs-demo-raw

Endpoints (127.0.0.1:8793 only):
    POST /save      {url, status, ctype, body}   one API response (latest wins)
    POST /sse       {path, type, t, data}         one server-sent event
    POST /blocked   {method, url, body}           a write the recorder refused
    GET  /status                                  counts so far
"""

import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Desktop/ohlcvlabs-demo-raw"))
ALLOWED_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000"}
os.makedirs(os.path.join(OUT, "api"), exist_ok=True)
counts = {"save": 0, "sse": 0, "blocked": 0}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path != "/status":
            self.send_response(404); self.end_headers(); return
        body = json.dumps(counts).encode()
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        kind = self.path.strip("/")
        if kind not in counts:
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            msg = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400); self.end_headers(); return
        if kind == "save":
            name = hashlib.sha1(msg.get("url", "").encode()).hexdigest()[:16] + ".json"
            with open(os.path.join(OUT, "api", name), "w", encoding="utf-8") as fh:
                json.dump(msg, fh, ensure_ascii=False)
        else:
            with open(os.path.join(OUT, kind + ".jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        counts[kind] += 1
        self.send_response(204); self._cors(); self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("demo recorder: writing to %s on http://127.0.0.1:8793" % OUT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8793), Handler).serve_forever()
