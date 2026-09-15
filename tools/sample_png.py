#!/usr/bin/env python3
"""Code-level PNG pixel sampler (no vision model).

Decodes a captured framebuffer PNG with the standard library and reports its
dimensions and a sampled distinct-color count, proving a real, non-blank,
non-uniform rendered frame.
"""
import struct
import sys
import zlib
from pathlib import Path


def decode_png(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG signature"
    pos = 8
    width = height = color_type = None
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk)
            assert interlace == 0 and bit_depth == 8
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        ftype = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if ftype == 1:
                line[x] = (line[x] + a) % 256
            elif ftype == 2:
                line[x] = (line[x] + b) % 256
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) % 256
            elif ftype == 4:
                pa, pb, pc = a, b, c
                pr = pa + pb - pc
                p1, p2, p3 = abs(pr - pa), abs(pr - pb), abs(pr - pc)
                pred = pa if (p1 <= p2 and p1 <= p3) else (pb if p2 <= p3 else pc)
                line[x] = (line[x] + pred) % 256
        out += line
        prev = line
    pixels = [tuple(out[j:j + 3]) for j in range(0, len(out), channels)]
    return width, height, pixels


def main():
    path = sys.argv[1]
    width, height, pixels = decode_png(path)
    step = max(1, len(pixels) // 20000)
    colors = {pixels[i] for i in range(0, len(pixels), step)}
    print("SAMPLE_PNG %s %dx%d distinct_colors=%d pixels=%d"
          % (path, width, height, len(colors), len(pixels)))


if __name__ == "__main__":
    main()
