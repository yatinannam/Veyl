"""A tiny OpenAI-compatible stand-in for CI and offline demos. Answers with the first
sentence of source S1 and cites it. Standard library only."""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = re.compile(r'<source id="S1"[^>]*>\n(.*?)\n</source>', re.S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        last = body["messages"][-1]["content"]
        match = SOURCE.search(last)
        if match:
            first = re.split(r"(?<=[.!?])\s", match.group(1).strip(), maxsplit=1)[0]
            answer = f"According to the documents: {first} [S1]"
        else:
            answer = "I could not find that in the documents."
        payload = json.dumps(
            {
                "id": "stub-1",
                "model": "veyl-stub",
                "choices": [{"message": {"role": "assistant", "content": answer}}],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 - container only
