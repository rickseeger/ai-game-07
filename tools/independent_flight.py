"""Independent closed-loop pilot: reads telemetry; sends ONLY XTest keys.
A different flight from the fixed regression schedule, at two render limiters.
Target is the existing stationary orange reference fighter, not enemy AI.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import statistics
import subprocess
import sys
import time
from panda3d.core import PNMImage, Quat, Vec3
from flight_runtime import XInput

TARGET = Vec3(21, 63, -5)


def run_flight(out, hz):
    out.mkdir(parents=True, exist_ok=False)
    total = hz * 9
    command = [sys.executable, "-m", "breach.app", "--mute", "--keyboard-only",
               "--render-hz", str(hz), "--frames", str(total),
               "--trace-dir", str(out / "flight"), "--report", str(out / "report.json"),
               "--capture-frames", ",".join(str(hz*s) for s in (1, 3, 5, 7, 9))]
    (out / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    x = XInput()
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(p.stdout, selectors.EVENT_READ)
    pending = b""
    trace = None
    frames = []
    deadline = time.monotonic() + 100
    try:
        with (out / "runtime.log").open("wb") as log, (out / "pilot.jsonl").open("w") as pilot:
            while selector.get_map():
                if time.monotonic() > deadline:
                    raise TimeoutError("independent pilot timeout")
                for key, _ in selector.select(.5):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(chunk); log.flush(); pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        text = line.decode(errors="replace")
                        if text.startswith("RENDERER "):
                            x.focus(json.loads(text[9:])["window_id"])
                        if not text.startswith("FLIGHT_FRAME "):
                            continue
                        if trace is None:
                            trace = (out / "flight/trace.jsonl").open()
                        latest = None
                        for entry in trace:
                            event = json.loads(entry)
                            if event["kind"] == "frame":
                                frames.append(event); latest = event
                        if latest is None:
                            continue
                        ship = latest["ship"]
                        elapsed = latest["tick"] / 60
                        q = Quat(*ship["orientation"])
                        delta = TARGET - Vec3(*ship["position"])
                        local = q.conjugate().xform(delta)
                        yaw = math.degrees(math.atan2(local.x, local.y))
                        pitch = math.degrees(math.atan2(local.z, math.hypot(local.x, local.y)))
                        desired = set()
                        if elapsed < .6:
                            desired = {"Left", "d"}
                            phase = "disorient"
                        elif elapsed < 2:
                            desired = {"Home"}
                            phase = "recover"
                        elif elapsed < 7 and delta.length() > 22:
                            phase = "pursue stationary fighter"
                            if yaw > 1.5: desired.add("Right")
                            if yaw < -1.5: desired.add("Left")
                            if pitch > 1.5: desired.add("Up")
                            if pitch < -1.5: desired.add("Down")
                            if ship["throttle"] < .55: desired.add("w")
                        else:
                            phase = "stop"
                            desired = {"b"}
                        for name in sorted(x.held - desired): x.key(name, False)
                        for name in sorted(desired - x.held): x.key(name, True)
                        pilot.write(json.dumps(dict(after_frame=latest["frame"], tick=latest["tick"],
                            phase=phase, keys=sorted(desired), range=delta.length(),
                            yaw_error=yaw, pitch_error=pitch)) + "\n")
                        pilot.flush()
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None: p.kill(); p.wait()
        x.close(); selector.close(); p.stdout.close()
        if trace: trace.close()
    events = [json.loads(s) for s in (out / "flight/trace.jsonl").read_text().splitlines()]
    frames = [e for e in events if e["kind"] == "frame"]
    decisions = [json.loads(s) for s in (out / "pilot.jsonl").read_text().splitlines()]
    distance = lambda f: (TARGET - Vec3(*f["ship"]["position"])).length()
    assert len(frames) == total
    assert distance(frames[-1]) < distance(frames[0]) - 20
    assert Vec3(*frames[-1]["ship"]["velocity"]).length() < .01
    assert frames[-1]["ship"]["throttle"] == 0
    recovered = [f for f in frames if 1.9 < f["tick"]/60 < 2]
    assert recovered and all(Quat(*f["ship"]["orientation"]).xform(Vec3(0, 0, 1)).z > .999 for f in recovered)
    assert any(d["phase"].startswith("pursue") and abs(d["yaw_error"]) < 3 and abs(d["pitch_error"]) < 3 for d in decisions)
    assert any(e["kind"] == "simulation" and e["controls"]["yaw"] > 0 and e["controls"]["throttle"] > 0 for e in events)
    captures = []
    for f in frames:
        assert f["eye_local"] == frames[0]["eye_local"]
        if not f["capture"]: continue
        path = out / "flight" / f["capture"]
        image = PNMImage(); assert image.read(str(path.resolve()))
        captures.append(dict(path=str(path), frame=f["frame"], tick=f["tick"],
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert len(captures) == 5
    result = dict(status="passed", render_hz=hz, frames=total, ticks=frames[-1]["tick"],
                  median_frame_dt=statistics.median(f["dt"] for f in frames),
                  initial_range=distance(frames[0]), final_range=distance(frames[-1]),
                  final_speed=Vec3(*frames[-1]["ship"]["velocity"]).length(), captures=captures,
                  checks=["independent XTest keyboard flight", "roll and yaw then Home recovery",
                          "feedback-directed aim and pursuit of stationary scene fighter", "brake stop",
                          "cockpit local eye unchanged"], scope="Automated pilot, not a physical human playtest")
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [run_flight(args.output / str(hz), hz) for hz in (30, 144)]
    (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print("INDEPENDENT_FLIGHT_PASS " + json.dumps(results))

if __name__ == "__main__": main()
