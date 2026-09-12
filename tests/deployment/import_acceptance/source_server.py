"""HTTP fixture streams an exactly 50MiB decodable JPEG without buffering it."""

import io
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image

stream = io.BytesIO()
Image.new("RGB", (4000, 3000), "red").save(stream, "JPEG")
jpeg = stream.getvalue()
# Valid JPEG COM segments before image headers provide the full accepted byte size.
remaining = 52428800 - len(jpeg)
segments = []
while remaining:
    size = min(65537, remaining)
    if 0 < remaining - size < 4:
        size -= 4
    segments.append(b"\xff\xfe" + (size - 2).to_bytes(2, "big") + b"a" * (size - 4))
    remaining -= size


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "52428800")
        self.end_headers()
        self.wfile.write(jpeg[:2])
        for part in segments:
            self.wfile.write(part)
        self.wfile.write(jpeg[2:])


HTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
