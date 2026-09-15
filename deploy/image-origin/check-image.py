"""Inspect the JPEG envelope without installing image libraries on the VM."""

import struct
import sys
from pathlib import Path


def check(image_path: str, headers_path: str) -> None:
    headers = Path(headers_path).read_text().lower()
    if "content-type: image/jpeg" not in headers or (
        "cache-control: public, max-age=21600, s-maxage=2592000" not in headers
    ):
        raise ValueError("Unexpected image response headers")
    data = Path(image_path).read_bytes()
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Incomplete JPEG")
    offset = 2
    while offset + 4 < len(data):
        marker = data[offset : offset + 2]
        length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if marker == b"\xff\xc2":
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            if not (0 < height <= 960 and 0 < width <= 960):
                raise ValueError("JPEG dimensions exceed the origin contract")
            print(f"IMAGE_ORIGIN_CHECK=green width={width} height={height} progressive=true")
            return
        if marker == b"\xff\xda" or length < 2:
            break
        offset += length + 2
    raise ValueError("Expected progressive JPEG")


if __name__ == "__main__":
    check(*sys.argv[1:])
