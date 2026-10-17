"""SPK-2 fake external service: records every request, so duplicates are counted, not guessed.

POST /effect {key, kind}  find-or-create by key; counts every request per key
POST /mark {lane, id, event}  appends a timestamped mark (lane experiments)
GET /state                 everything recorded
POST /reset                forget everything
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
state: dict = {"requests": {}, "effects": {}, "marks": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def reply(self, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        with lock:
            self.reply(state)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        with lock:
            if self.path == "/reset":
                state.update({"requests": {}, "effects": {}, "marks": []})
                self.reply({})
            elif self.path == "/effect":
                key = data["key"]
                state["requests"][key] = state["requests"].get(key, 0) + 1
                created = key not in state["effects"]
                if created:
                    state["effects"][key] = {"kind": data.get("kind"), "t": time.time()}
                self.reply({"created": created, "requests": state["requests"][key]})
            elif self.path == "/mark":
                state["marks"].append({**data, "t": time.time()})
                self.reply({})
            else:
                self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
