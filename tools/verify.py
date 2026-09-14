"""Independent setup -> tests -> actual renderer; all subprocess output retained."""
import argparse
import ctypes.util
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def run(command, log, env, timeout=180):
    result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout)
    log.write_text("COMMAND " + " ".join(map(str, command)) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit(f"Failed ({result.returncode}); see {log}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--venv", default=".venv")
    p.add_argument("--output", default="artifacts/local")
    args = p.parse_args()
    out = (ROOT / args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    venv = (ROOT / args.venv).resolve()
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv))
    devices = {}
    for name in ("/dev/dri", "/dev/snd", "/dev/input"):
        path = Path(name)
        devices[name] = sorted(x.name for x in path.iterdir()) if path.exists() else None
    proc = {}
    for name in ("/proc/asound/cards", "/proc/bus/input/devices", "/etc/os-release"):
        path = Path(name)
        proc[name] = path.read_text() if path.exists() else None
    environment = dict(platform=platform.platform(), machine=platform.machine(),
        variables={k: os.environ.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY", "PULSE_SERVER", "XDG_RUNTIME_DIR", "LIBGL_ALWAYS_SOFTWARE")},
        devices=devices, proc=proc,
        libraries={k: ctypes.util.find_library(k) for k in ("GL", "EGL", "X11", "Xtst", "asound", "pulse", "openal")})
    (out / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    run(["uv", "sync", "--project", str(ROOT), "--frozen"], out / "setup.log", env)
    python = str(venv / "bin/python")
    run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], out / "tests.log", env)
    run([python, "tools/runtime_smoke.py", "--output", str(out)], out / "runtime.log", env)
    run([python, "tools/flight_runtime.py", "--output", str(out / "input-flight")], out / "flight-runtime.log", env)
    run([python, "tools/independent_flight.py", "--output", str(out / "independent-pilot")], out / "independent-pilot.log", env)
    run([python, "tools/damage_runtime.py", "--output", str(out / "damage-runtime")], out / "damage-runtime.log", env)
    run([python, "tools/weapons_sim.py", "--output", str(out / "weapons-sim")], out / "weapons-sim.log", env)
    run([python, "tools/weapons_runtime.py", "--output", str(out / "weapons-runtime")], out / "weapons-runtime.log", env)
    run([python, "tools/enemies_sim.py", "--output", str(out / "enemies-sim")], out / "enemies-sim.log", env)
    run([python, "tools/enemies_runtime.py", "--output", str(out / "enemies-runtime")], out / "enemies-runtime.log", env, timeout=300)
    run([python, "tools/cockpit_runtime.py", "--output", str(out / "cockpit-runtime")], out / "cockpit-runtime.log", env, timeout=300)
    print("VERIFY_PASS " + str(out))
if __name__ == "__main__":
    main()
