#!/usr/bin/env python3
"""Code-level PNG color probe (no vision model).

Decodes a captured framebuffer PNG and counts pixels within a tolerance of
named flat colors drawn by Breach Flight's cockpit/HUD (exact colors from
src/breach/cockpit.py and src/breach/effects.py). Emits one JSON object.
"""
import json
import struct
import sys
import zlib
from pathlib import Path

TARGETS = {
    "cockpit_steel":  (0.30, 0.33, 0.40),
    "gauge_ok":       (0.15, 0.85, 0.40),
    "gauge_warn":     (1.00, 0.70, 0.10),
    "gauge_crit":     (1.00, 0.10, 0.10),
    "radar_rim":      (0.25, 0.60, 0.70),
    "radar_blip":     (1.00, 0.62, 0.16),
    "radar_above":    (0.30, 0.95, 0.55),
    "radar_below":    (0.90, 0.42, 1.00),
    "alert_red":      (1.00, 0.06, 0.06),
    "text_fg":        (0.82, 0.90, 0.96),
    "text_good":      (0.35, 0.95, 0.55),
    "smoke":          (0.55, 0.50, 0.45),
    "fire_orange":    (1.00, 0.50, 0.10),
    "fire_yellow":    (1.00, 0.85, 0.30),
    "muzzle":         (1.00, 0.96, 0.62),
    "impact":         (1.00, 0.86, 0.42),
    "repair_glow":    (0.30, 0.95, 0.55),
}

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
        ftype = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
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
    return width, height, out, channels

def main():
    path = sys.argv[1]
    tol = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    width, height, raw, channels = decode_png(path)
    counts = {name: 0 for name in TARGETS}
    n = width * height
    for j in range(0, len(raw), channels):
        r = raw[j]
        g = raw[j + 1] if channels >= 2 else r
        b = raw[j + 2] if channels >= 3 else r
        for name, (tr, tg, tb) in TARGETS.items():
            rr, gg, bb = round(tr * 255), round(tg * 255), round(tb * 255)
            if abs(r - rr) <= tol and abs(g - gg) <= tol and abs(b - bb) <= tol:
                counts[name] += 1
    step = max(1, n // 20000)
    colors = set()
    for j in range(0, len(raw), step * channels):
        r = raw[j]
        g = raw[j + 1] if channels >= 2 else r
        b = raw[j + 2] if channels >= 3 else r
        colors.add((r, g, b))
    result = {
        "path": str(path),
        "size": [width, height],
        "tolerance": tol,
        "distinct_colors_sampled": len(colors),
        "color_pixel_counts": {k: v for k, v in counts.items() if v > 0},
        "present": {k: v > 0 for k, v in counts.items()},
    }
    print(json.dumps(result))

if __name__ == "__main__":
    main()
