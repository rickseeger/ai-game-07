"""Live integration check: scripted damage/repair events drive the real app.

Run under DISPLAY (xvfb-run or tools/headless.py's private Xvfb). Launches the
actual Panda3D application with a scripted damage schedule, then validates the
recorded health/state/repair trace, proving the damage system is wired into the
live ship rather than a disconnected demonstration. No vision is used: every
assertion reads the deterministic simulation trace.

Timeline (fighter profile, hull 100, engine 40; schedule tick -> effective tick+1):
    tick  60: engine 40 damage  -> engine destroyed (thrust/turning 0)
    tick 120: hull 40 damage    -> smoking
    tick 180: hull 30 damage    -> burning
    tick 240: repair engine     -> restores to 0.70 over ~4s (cap)
    tick 540: hull 30 damage    -> destroyed
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

SCHEDULE = [
    {"tick": 60, "amount": 40, "subsystem": "engine"},
    {"tick": 120, "amount": 40, "subsystem": None},
    {"tick": 180, "amount": 30, "subsystem": None},
    {"tick": 240, "type": "repair", "subsystem": "engine", "active": True},
    {"tick": 540, "amount": 30, "subsystem": None},
]


def run_app(out):
    out.mkdir(parents=True, exist_ok=False)
    (out / "damage-script.json").write_text(json.dumps(SCHEDULE, indent=2) + "\n")
    command = [sys.executable, "-m", "breach.app", "--mute", "--offscreen",
               "--frames", "700", "--trace-dir", str(out / "trace"),
               "--report", str(out / "report.json"),
               "--damage-script", str(out / "damage-script.json"),
               "--capture-frames", "120,240,480,680"]
    (out / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=180)
    (out / "runtime.log").write_text("COMMAND " + " ".join(command) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit(f"app failed ({result.returncode}); see {out / 'runtime.log'}")


def validate(out):
    events = [json.loads(line) for line in (out / "trace" / "trace.jsonl").read_text().splitlines()]
    sims = [e for e in events if e["kind"] == "simulation"]
    report = json.loads((out / "report.json").read_text())
    assert sims, "no simulation ticks recorded"
    assert report["health"]["state"] == "destroyed", report["health"]

    def at(tick):
        matches = [e["health"] for e in sims if e["tick"] >= tick]
        return matches[0] if matches else None

    # Ordered distinct state transitions: healthy -> smoking -> burning -> destroyed.
    states = []
    for e in sims:
        st = e["health"]["state"]
        if not states or states[-1] != st:
            states.append(st)
    assert states == ["healthy", "smoking", "burning", "destroyed"], states

    # Hull thresholds land on the exact fractions (0.66 / 0.33 boundaries).
    assert at(1)["hull"] == 1.0 and at(1)["state"] == "healthy"
    assert abs(at(122)["hull"] - 0.6) < 1e-9 and at(122)["state"] == "smoking"
    assert abs(at(182)["hull"] - 0.3) < 1e-9 and at(182)["state"] == "burning"
    assert at(542)["hull"] == 0.0 and at(542)["state"] == "destroyed"

    # Engine capability arc: intact -> destroyed -> repaired to cap -> destroyed.
    assert at(1)["engine_multiplier"] == 1.0
    assert at(62)["engine_multiplier"] == 0.0
    assert at(62)["turning_multiplier"] == 0.0
    repaired = [e for e in sims
                if 460 <= e["tick"] <= 540 and abs(e["health"]["engine_multiplier"] - 0.70) < 0.02]
    assert repaired, "engine never repaired to 0.70 cap"
    assert at(542)["engine_multiplier"] == 0.0 and at(542)["weapons_multiplier"] == 0.0

    captures = sorted((out / "trace").glob("*.png"))
    assert len(captures) == 4, captures
    summary = dict(
        status="passed", frames=report["frames"], ticks=report["ticks"],
        final_health=report["health"], states_observed=states,
        captures=[str(c) for c in captures],
        schedule=SCHEDULE,
        checks=["engine damage zeroes thrust+turning live",
                "hull thresholds smoking/burning/destroyed observed live",
                "repair restores engine to 0.70 cap live",
                "terminal destruction zeroes all capability",
                "real Panda3D app, deterministic trace (no vision)"],
        scope="Scripted events -> DamageSystem -> live FlightSystem performance; no human playtest.",
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("DAMAGE_RUNTIME_PASS " + json.dumps(summary))
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--validate-only", action="store_true")
    args = p.parse_args()
    if args.validate_only:
        validate(args.output)
        return
    run_app(args.output)
    validate(args.output)


if __name__ == "__main__":
    main()
