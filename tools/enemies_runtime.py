"""Real-renderer enemy encounter captures and trace validation (no vision).

Launches the actual Panda3D app offscreen with the live EnemySystem and validates
the deterministic trace (fighters maneuver + attack, capital fights back, player
fire damages the capital, crippled enemies enter/exit a vulnerable timed repair)
plus code-level framebuffer sampling of the captured PNGs (no image-perception
model). Two runs:

- encounter: default live enemies; scripted player fire damages the capital while
  fighters pursue and the capital turret returns fire.
- repair: a damage script cripples a fighter's and the capital's weapons; each
  enters a vulnerable repair (stops shooting, brakes) and restores over time.

Run under DISPLAY (xvfb-run -a or tools/headless.py's private Xvfb):
    xvfb-run -a .venv/bin/python tools/enemies_runtime.py --output artifacts/enemies-runtime
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

FIRE_SCRIPT = [{"tick": 10, "fire": True}]
DAMAGE_SCRIPT = [
    {"tick": 10, "target_id": "fighter-1", "amount": 40, "subsystem": "weapons"},
    {"tick": 10, "target_id": "fighter-2", "amount": 40, "subsystem": "weapons"},
    {"tick": 10, "target_id": "fighter-3", "amount": 40, "subsystem": "weapons"},
    {"tick": 10, "target_id": "capital", "amount": 600, "subsystem": "weapons"},
]
ENCOUNTER_FRAMES = 720
REPAIR_FRAMES = 1200
CAPTURE_FRAMES = "150,450,650"


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


def run_app(out, fire=None, damage=None, frames=ENCOUNTER_FRAMES):
    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "breach.app", "--mute", "--offscreen",
               "--demo-enemies",
               "--frames", str(frames),
               "--trace-dir", str(out / "trace"),
               "--report", str(out / "report.json"),
               "--capture-frames", CAPTURE_FRAMES]
    if fire is not None:
        write_json(out / "fire-script.json", fire)
        command += ["--fire-script", str(out / "fire-script.json")]
    if damage is not None:
        write_json(out / "damage-script.json", damage)
        command += ["--damage-script", str(out / "damage-script.json")]
    write_json(out / "command.json", command)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=300)
    (out / "runtime.log").write_text("COMMAND " + " ".join(command) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit(f"app failed ({result.returncode}); see {out / 'runtime.log'}")


def load_events(out):
    return [json.loads(line) for line in (out / "trace" / "trace.jsonl").read_text().splitlines()]


def frames_of(events):
    return [e for e in events if e["kind"] == "frame"]


def enemy_rows(frames):
    """Map entity_id -> list of (tick, row) across frames."""
    rows = {}
    for f in frames:
        for row in f.get("enemy_state", []):
            rows.setdefault(row["entity_id"], []).append((f["tick"], row))
    return rows


def decode_png(path):
    """Minimal PNG decoder -> (width, height, [(r,g,b), ...]) for 8-bit RGB(A)/gray."""
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG signature"
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk)
            assert interlace == 0, "interlaced PNG unsupported"
            assert bit_depth == 8, f"bit depth {bit_depth} unsupported"
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
            if ftype == 0:
                pass
            elif ftype == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ftype == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                pa, pb, pc = a, b, c
                pr = pa + pb - pc
                pa_, pb_, pc_ = abs(pr - pa), abs(pr - pb), abs(pr - pc)
                pred = pa if (pa_ <= pb_ and pa_ <= pc_) else (pb if pb_ <= pc_ else pc)
                line[x] = (line[x] + pred) & 0xFF
            else:
                raise AssertionError(f"bad filter {ftype}")
        out += line
        prev = line
    pixels = []
    for i in range(0, len(out), channels):
        if channels == 1:
            pixels.append((out[i], out[i], out[i]))
        elif channels == 3:
            pixels.append((out[i], out[i + 1], out[i + 2]))
        else:
            pixels.append((out[i], out[i + 1], out[i + 2]))
    return width, height, pixels


def distinct_colors(pixels, step=7):
    return len({pixels[i] for i in range(0, len(pixels), step)})


def validate_encounter(out):
    events = load_events(out)
    frames = frames_of(events)
    report = json.loads((out / "report.json").read_text())
    assert frames, "no frame events"
    rows = enemy_rows(frames)

    # Fighters maneuver: each moved from its spawn position.
    moved = {}
    for eid, seq in rows.items():
        if rows[eid][0][1]["kind"] != "fighter":
            continue
        p0 = seq[0][1]["position"]
        pn = seq[-1][1]["position"]
        dist = sum((a - b) ** 2 for a, b in zip(p0, pn)) ** 0.5
        moved[eid] = dist
    assert moved and all(d > 5.0 for d in moved.values()), moved

    # Active opposition: at least one enemy fired, and the player took damage.
    enemy_fired = any(row["firing"] for seq in rows.values() for _, row in seq)
    assert enemy_fired, "no enemy ever fired"
    assert report["health"]["hull_hp"] < 100.0, report["health"]

    # Player fire damaged the capital through the shared DamageSystem.
    assert "capital" in report["enemy_health"], report["enemy_health"]
    capital_hp = report["enemy_health"]["capital"]["hull_hp"]
    assert capital_hp < 3000.0, capital_hp

    # Code-level framebuffer sampling: captures are real, non-blank and change.
    captures = sorted((out / "trace").glob("*.png"))
    assert len(captures) == 3, captures
    color_counts = []
    for cap in captures:
        w, h, pixels = decode_png(cap)
        assert (w, h) == (960, 540), (w, h)
        color_counts.append(distinct_colors(pixels))
    assert all(c > 200 for c in color_counts), color_counts  # not a blank frame

    return dict(moved=moved, enemy_fired=enemy_fired,
                player_hull_hp=report["health"]["hull_hp"],
                capital_hull_hp=capital_hp,
                capture_distinct_colors=color_counts)


def validate_repair(out):
    events = load_events(out)
    frames = frames_of(events)
    rows = enemy_rows(frames)
    assert "fighter-1" in rows and "capital" in rows

    def repairing_phases(seq):
        """Return (entered_tick, exit_tick_or_None, peak_progress, fired_during)."""
        entered = exited = None
        peak = 0.0
        fired = False
        for tick, row in seq:
            if row["repairing"] is not None:
                if entered is None:
                    entered = tick
                peak = max(peak, row["repair_progress"])
                if row["firing"]:
                    fired = True
            elif entered is not None and exited is None:
                exited = tick
        return entered, exited, peak, fired

    f_entered, f_exited, f_peak, f_fired = repairing_phases(rows["fighter-1"])
    c_entered, c_exited, c_peak, c_fired = repairing_phases(rows["capital"])

    # Both enemies entered a repair and held fire while repairing.
    assert f_entered is not None, "fighter-1 never entered repair"
    assert c_entered is not None, "capital never entered repair"
    assert not f_fired, "fighter-1 fired while repairing"
    assert not c_fired, "capital fired while repairing"

    # Fighter repair completes (reaches the cap) and exits; the capital's 30 s
    # repair makes timed progress within the window (never instant).
    assert f_exited is not None, "fighter-1 never exited its repair"
    assert f_peak >= 0.9, f_peak  # reached the cap before exit
    assert c_peak > 0.0, "capital repair made no progress"

    return dict(fighter_entered=f_entered, fighter_exited=f_exited,
                fighter_peak_progress=round(f_peak, 4),
                fighter_held_fire=not f_fired,
                capital_entered=c_entered, capital_exited=c_exited,
                capital_peak_progress=round(c_peak, 4),
                capital_held_fire=not c_fired)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    enc_dir = args.output / "encounter"
    rep_dir = args.output / "repair"
    run_app(enc_dir, fire=FIRE_SCRIPT, frames=ENCOUNTER_FRAMES)
    run_app(rep_dir, damage=DAMAGE_SCRIPT, frames=REPAIR_FRAMES)
    encounter = validate_encounter(enc_dir)
    repair = validate_repair(rep_dir)
    summary = dict(
        status="passed",
        encounter=encounter,
        repair=repair,
        checks=[
            "real Panda3D offscreen renderer with the live EnemySystem",
            "fighters maneuver and attack; capital turret returns fire",
            "player fire damages the capital through the shared DamageSystem",
            "crippled enemies enter a vulnerable repair (hold fire, brake, timed)",
            "code-level PNG framebuffer sampling (no vision model used)",
        ],
        scope="Scripted offscreen encounter, not a human playtest.",
    )
    write_json(args.output / "summary.json", summary)
    print("ENEMIES_RUNTIME_PASS " + json.dumps(summary))


if __name__ == "__main__":
    main()
