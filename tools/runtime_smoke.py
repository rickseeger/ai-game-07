"""Launch the real renderer, inject an X11 key, verify framebuffer captures."""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from panda3d.core import PNMImage

def send_turn(window):
    x = C.CDLL("libX11.so.6")
    t = C.CDLL("libXtst.so.6")
    x.XOpenDisplay.argtypes = [C.c_char_p]
    x.XOpenDisplay.restype = C.c_void_p
    x.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
    x.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
    x.XKeysymToKeycode.restype = C.c_uint
    x.XFlush.argtypes = [C.c_void_p]
    x.XCloseDisplay.argtypes = [C.c_void_p]
    t.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
    d = x.XOpenDisplay(None)
    if not d:
        raise RuntimeError("Cannot open DISPLAY for real X11 event injection")
    try:
        x.XSetInputFocus(d, window, 1, 0)
        code = x.XKeysymToKeycode(d, 0xff53)
        assert code
        assert t.XTestFakeKeyEvent(d, code, 1, 0)
        x.XFlush(d)
        time.sleep(0.1)  # Deliberate key hold, not display readiness polling.
        assert t.XTestFakeKeyEvent(d, code, 0, 0)
        x.XFlush(d)
    finally:
        x.XCloseDisplay(d)

def launch(out, mode):
    capture, report = out / f"{mode}.png", out / f"{mode}.json"
    # Remove stale evidence so it cannot satisfy a failed launch.
    capture.unlink(missing_ok=True)
    report.unlink(missing_ok=True)
    command = [sys.executable, "-m", "breach.app", "--mute", "--no-combat-targets", "--frames", "180",
               "--capture", str(capture), "--report", str(report)]
    if mode == "offscreen":
        command.append("--offscreen")
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    ready = False
    details = None
    pending = b""
    selector = selectors.DefaultSelector()
    selector.register(p.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 45
    try:
        with (out / f"{mode}.log").open("wb") as log:
            log.write(("COMMAND " + " ".join(command) + "\n").encode())
            while selector.get_map():
                if time.monotonic() > deadline:
                    raise TimeoutError("Application did not terminate within 45 seconds")
                for key, _ in selector.select(0.5):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(chunk)
                    log.flush()
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        line = line.decode(errors="replace")
                        print(line, flush=True)
                        if line.startswith("RENDERER "):
                            details = json.loads(line[len("RENDERER "):])
                        if line == "WINDOW_READY":
                            ready = True
                            if mode == "window":
                                send_turn(details["window_id"])
        assert p.wait(timeout=5) == 0, "renderer process failed"
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        selector.close()
        p.stdout.close()
    assert ready and report.is_file() and capture.is_file()
    data = json.loads(report.read_text())
    assert data["frames"] == 180
    assert data["size"] == [960, 540]
    assert data["input_events"] == (1 if mode == "window" else 0)
    assert data["output_type"] == ("glxGraphicsWindow" if mode == "window" else "glxGraphicsBuffer")
    image = PNMImage()
    assert image.read(str(capture.resolve()))
    assert (image.getXSize(), image.getYSize()) == (960, 540)
    # Central 3D viewport only: title/footer cannot make an empty scene pass.
    colors = set()
    foreground = 0
    samples = 0
    for y in range(100, 440, 3):
        for x in range(160, 800, 3):
            rgb = tuple(round(v * 255) for v in image.getXel(x, y))
            colors.add(rgb)
            foreground += max(rgb) > 35
            samples += 1
    assert len(colors) >= 8, "central viewport has insufficient scene colors"
    assert foreground / samples > 0.03, "central viewport is nearly empty"
    return dict(mode=mode, sha256=hashlib.sha256(capture.read_bytes()).hexdigest(),
                viewport_colors=len(colors), foreground_fraction=foreground / samples,
                input_events=data["input_events"], renderer=data["renderer"],
                capture=str(capture), frames=data["frames"])

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("DISPLAY"):
        raise SystemExit("DISPLAY required: start Xvfb or use a real Linux desktop")
    results = [launch(args.output, mode) for mode in ("window", "offscreen")]
    assert results[0]["sha256"] != results[1]["sha256"], "camera input must change framebuffer"
    summary = dict(status="passed", captures=results,
                   input_scope="XTest Right-arrow through X11/Panda event loop, not a human/device playtest",
                   audio_scope="muted; audible playback NOT verified")
    (args.output / "runtime-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("RUNTIME_SMOKE_PASS " + json.dumps(summary))
if __name__ == "__main__":
    main()
