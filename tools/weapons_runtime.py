"""Real-renderer combat captures and trace validation (no vision).

Launches the actual Panda3D app offscreen with scripted fire and validates the
deterministic trace (projectile spawns, hit/miss resolution, enemy damage routed
through the shared DamageSystem) plus real framebuffer captures. Two runs:
- hit: default enemy/capital hitboxes registered -> sustained fire hits the
  capital and reduces its hull through DamageSystem.
- miss: --no-combat-targets -> every shot misses; nothing is damaged.

Run under DISPLAY (xvfb-run or tools/headless.py's private Xvfb):
    xvfb-run -a .venv/bin/python tools/weapons_runtime.py --output artifacts/weapons-runtime
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

FIRE_SCRIPT = [{"tick": 10, "fire": True}]
FRAMES = 600
CAPTURE_FRAMES = "100,300,500"


def run_app(out, no_targets):
    out.mkdir(parents=True, exist_ok=False)
    (out / "fire-script.json").write_text(json.dumps(FIRE_SCRIPT, indent=2) + "\n")
    command = [sys.executable, "-m", "breach.app", "--mute", "--offscreen",
               "--frames", str(FRAMES),
               "--trace-dir", str(out / "trace"),
               "--report", str(out / "report.json"),
               "--capture-frames", CAPTURE_FRAMES,
               "--fire-script", str(out / "fire-script.json")]
    if no_targets:
        command.append("--no-combat-targets")
    else:
        # Isolate node-4 weapons evidence from the node-5 enemy AI.
        command.append("--static-targets")
    (out / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=180)
    (out / "runtime.log").write_text("COMMAND " + " ".join(command) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit(f"app failed ({result.returncode}); see {out / 'runtime.log'}")


def load_events(out):
    return [json.loads(line) for line in (out / "trace" / "trace.jsonl").read_text().splitlines()]


def validate(out, expect_hits):
    events = load_events(out)
    frames = [e for e in events if e["kind"] == "frame"]
    report = json.loads((out / "report.json").read_text())
    assert frames, "no frame events"
    assert "weapons" in frames[-1], "frame trace missing weapons state"

    final = report["weapons"]
    assert final["fired"] > 0, final
    in_flight = [f["weapons"]["projectiles"] for f in frames]
    assert max(in_flight) > 0, "no projectile ever in flight"

    enemy = report["enemy_health"]
    if expect_hits:
        assert final["hits"] > 0, final
        assert final["misses"] == 0, final
        assert "capital" in enemy, enemy
        assert enemy["capital"]["hull_hp"] < 3000.0, enemy["capital"]
        hit_events = [h for f in frames for h in f["weapon_hits"]]
        assert hit_events, "no weapon hit events in trace"
        assert all(h[0] == "capital" for h in hit_events), hit_events[:5]
    else:
        assert final["hits"] == 0, final
        assert final["misses"] > 0, final
        assert enemy == {}, enemy

    captures = sorted((out / "trace").glob("*.png"))
    assert len(captures) == 3, captures
    return dict(status="passed", frames=report["frames"], ticks=report["ticks"],
                weapons=final, enemy_health=enemy, captures=[str(c) for c in captures])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    hit_dir = args.output / "hit"
    miss_dir = args.output / "miss"
    run_app(hit_dir, no_targets=False)
    run_app(miss_dir, no_targets=True)
    hit = validate(hit_dir, expect_hits=True)
    miss = validate(miss_dir, expect_hits=False)
    summary = dict(
        status="passed",
        hit=hit,
        miss=miss,
        checks=[
            "real Panda3D offscreen renderer with scripted fire",
            "projectiles spawn, fly, and resolve (no vision used)",
            "hit run: sustained fire damages the capital through DamageSystem",
            "miss run: no targets -> all shots miss, nothing damaged",
            "weapons/aim/enemy-health recorded in the deterministic trace",
        ],
        scope="Scripted offscreen combat, not a human playtest.",
    )
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("WEAPONS_RUNTIME_PASS " + json.dumps(summary))


if __name__ == "__main__":
    main()
