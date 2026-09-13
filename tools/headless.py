"""Own a private Xvfb server, health-check it, verify, and always clean up."""
import argparse
import ctypes as C
import os
from pathlib import Path
import select
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--venv", default=".venv-verify")
    p.add_argument("--output", default="artifacts/local")
    args = p.parse_args()
    out = (ROOT / args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    read_fd, write_fd = os.pipe()
    with (out / "xvfb.log").open("w") as log:
        server = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0",
                                   "960x540x24", "-nolisten", "tcp"],
                                  pass_fds=(write_fd,), stdout=log, stderr=log)
        os.close(write_fd)
        try:
            if not select.select([read_fd], [], [], 15)[0]:
                raise RuntimeError("Xvfb did not announce a display within 15s")
            number = os.read(read_fd, 32).decode().strip()
            if not number.isdigit():
                raise RuntimeError("Xvfb failed; inspect xvfb.log")
            display = ":" + number
            x = C.CDLL("libX11.so.6")
            x.XOpenDisplay.argtypes = [C.c_char_p]
            x.XOpenDisplay.restype = C.c_void_p
            x.XCloseDisplay.argtypes = [C.c_void_p]
            connection = x.XOpenDisplay(display.encode())
            if not connection:
                raise RuntimeError("X11 readiness health check failed")
            x.XCloseDisplay(connection)
            log.write("XOpenDisplay health check PASS on " + display + "\n")
            log.flush()
            env = dict(os.environ, DISPLAY=display, LIBGL_ALWAYS_SOFTWARE="1")
            result = subprocess.run([sys.executable, "tools/verify.py", "--venv", args.venv,
                                     "--output", args.output], cwd=ROOT, env=env, timeout=240)
            if result.returncode:
                raise SystemExit(result.returncode)
        finally:
            os.close(read_fd)
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
if __name__ == "__main__":
    main()
