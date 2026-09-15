"""Real-renderer combat-effects and emitted-audio evidence (node 7, no vision).

Drives the actual Panda3D app offscreen through a deterministic scripted
sequence and validates, with code-level framebuffer sampling (no image
perception), that damage state -> effect binding actually renders:

  healthy -> smoking (smoke) -> burning (fire) -> destruction (explosion),
  with a capital-ship explosion larger than a fighter's, plus repair recovery
  (green glow). Effects are bound to the real damage events/state, not cosmetic
  timers: the trace records which particle kinds are active in which state.

It also records the game's *emitted audio*: an unmuted run against the OpenAL
Soft 'wave' backend writes the actual mixed output to a WAV, checked to be
non-silent and correlated with the game's own audio-event tally. Merely
generating the sound assets is not the claim; the running game emitting them
(and that emission being captured) is.

Rendering proof uses whole-frame differencing against a static healthy
baseline: the player, camera, scene and HUD are all static in the progression
run, so every changed pixel is one of our effects. Per-state pixel counts then
show the smoke -> fire -> explosion -> repair progression and the capital's
larger explosion, independent of exact screen-space projections.

Three runs:
- progression (static targets + damage script): pixel + trace validation of
  smoke/fire/explosion/repair.
- audio (same damage script + a fire script, unmuted, wave backend): the real
  mixed audio.
- live (default enemy AI + a fire script): effects fire during live combat.

Run under DISPLAY (xvfb-run -a or tools/headless.py's private Xvfb):
    xvfb-run -a .venv/bin/python tools/effects_runtime.py --output artifacts/effects-runtime
"""
import argparse
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave
import zlib


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


# -- scripted scenario --------------------------------------------------------
def progression_damage():
    """Damage/repair timeline keyed by fixed tick (applied the following tick).
    Capital: 3000 HP -> 1980 (0.66 smoking) -> 990 (0.33 burning) -> 0 (boom)."""
    return [
        {"tick": 20, "target_id": "capital", "amount": 1020},
        {"tick": 40, "target_id": "capital", "amount": 990},
        {"tick": 60, "target_id": "capital", "amount": 990},
        {"tick": 80, "target_id": "fighter-1", "amount": 100},
        {"tick": 90, "target_id": "fighter-2", "amount": 40, "subsystem": "engine"},
        {"tick": 96, "type": "repair", "target_id": "fighter-2",
         "subsystem": "engine", "active": True},
    ]


def fire_script():
    return [{"tick": 5, "fire": True}, {"tick": 28, "fire": False}]


# -- PNG decoding (pure stdlib) ----------------------------------------------
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
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
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
            if ftype == 0:
                pass
            elif ftype == 1:
                line[x] = (line[x] + a) % 256
            elif ftype == 2:
                line[x] = (line[x] + b) % 256
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) % 256
            elif ftype == 4:
                pa, pb, pc = a, b, c
                pr = pa + pb - pc
                pa_, pb_, pc_ = abs(pr - pa), abs(pr - pb), abs(pr - pc)
                pred = pa if (pa_ <= pb_ and pa_ <= pc_) else (pb if pb_ <= pc_ else pc)
                line[x] = (line[x] + pred) % 256
            else:
                raise AssertionError("bad filter %s" % ftype)
        out += line
        prev = line
    pixels = []
    for j in range(0, len(out), channels):
        if channels == 4:
            pixels.append((out[j], out[j + 1], out[j + 2]))
        elif channels == 3:
            pixels.append((out[j], out[j + 1], out[j + 2]))
        else:
            pixels.append((out[j], out[j], out[j]))
    return width, height, pixels


def whole_diff(baseline, frame, threshold=40):
    """Count pixels whose per-channel delta vs the healthy baseline is > threshold."""
    n = 0
    for i in range(len(baseline)):
        b, f = baseline[i], frame[i]
        if (abs(b[0] - f[0]) > threshold or abs(b[1] - f[1]) > threshold
                or abs(b[2] - f[2]) > threshold):
            n += 1
    return n


# -- app runs -----------------------------------------------------------------
def run_app(out, *, fire, damage, frames, capture, extra=(), mute=True, env=None):
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "damage-script.json", damage)
    command = [sys.executable, "-m", "breach.app",
               "--offscreen", "--static-targets",
               "--frames", str(frames),
               "--trace-dir", str(out / "trace"),
               "--report", str(out / "report.json"),
               "--capture-frames", capture,
               "--damage-script", str(out / "damage-script.json")]
    if fire:
        write_json(out / "fire-script.json", fire)
        command += ["--fire-script", str(out / "fire-script.json")]
    if mute:
        command.append("--mute")
    command += list(extra)
    write_json(out / "command.json", command)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=300, env=env)
    (out / "runtime.log").write_text("COMMAND " + " ".join(command) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit("app failed (%s); see %s" % (result.returncode, out / "runtime.log"))
    return json.loads((out / "report.json").read_text())


def load_events(out):
    return [json.loads(line) for line in (out / "trace" / "trace.jsonl").read_text().splitlines()]


def frames_of(events):
    return [e for e in events if e["kind"] == "frame"]


# -- progression validation ---------------------------------------------------
def validate_progression(out, capture):
    events = load_events(out)
    frames = frames_of(events)
    assert frames, "no frame events"
    captured = [f for f in frames if f.get("capture")
                and f["capture"].startswith("frame-")]
    assert len(captured) >= 5, (len(captured), "need dense captures")

    def state_of(f, eid):
        return f["enemy_health"][eid]["state"]

    baseline = min(captured, key=lambda f: f["frame"])
    assert state_of(baseline, "capital") == "healthy", state_of(baseline, "capital")
    bw, bh, bpixels = decode_png(out / "trace" / baseline["capture"])

    def diff(f):
        w, h, pixels = decode_png(out / "trace" / f["capture"])
        return whole_diff(bpixels, pixels)

    # Sanity: a healthy frame shortly after baseline is unchanged (static scene).
    later_healthy = [f for f in captured if f["frame"] > baseline["frame"] and
                     state_of(f, "capital") == "healthy"
                     and state_of(f, "fighter-1") == "healthy"]
    if later_healthy:
        assert diff(later_healthy[0]) == 0, diff(later_healthy[0])

    def max_diff(state, eid):
        best = 0
        for f in captured:
            if state_of(f, eid) == state:
                best = max(best, diff(f))
        return best

    smoke_chg = max_diff("smoking", "capital")
    burn_chg = max_diff("burning", "capital")
    cap_boom = max_diff("destroyed", "capital")
    fighter_boom = max_diff("destroyed", "fighter-1")
    assert smoke_chg > 0, smoke_chg            # smoke renders while smoking
    assert burn_chg > 0, burn_chg              # still degrading while burning
    assert cap_boom > burn_chg, (cap_boom, burn_chg)      # explosion is a burst
    assert cap_boom > fighter_boom, (cap_boom, fighter_boom)  # capital scales

    # repair recovery: green glow renders while fighter-2 repairs
    repairing = [f for f in captured
                 if f["enemy_health"]["fighter-2"].get("repairing") == "engine"]
    assert repairing, "no captured frame during fighter-2 repair"
    glow_chg = max(diff(f) for f in repairing)
    assert glow_chg > 0, glow_chg

    # event binding: cumulative tally (authoritative; survives frame catch-up)
    report = json.loads((out / "report.json").read_text())
    event_tally = report.get("effects_event_tally", {})
    for wanted in ("destroy", "repair_start", "repair_complete"):
        assert event_tally.get(wanted, 0) > 0, (wanted, event_tally)
    destroyed_kinds = set(report.get("effects_destroyed_kinds", []))
    assert destroyed_kinds == {"fighter", "capital"}, destroyed_kinds
    audio_tally = report.get("audio_tally", {})
    for wanted in ("explosion_fighter", "explosion_capital", "repair_start",
                   "repair_complete", "fire_loop"):
        assert audio_tally.get(wanted, 0) > 0, (wanted, audio_tally)

    # particle kinds bound to the right damage states
    def by_kind(state):
        ks = set()
        for f in captured:
            if state_of(f, "capital") == state:
                ks |= set((f.get("effects") or {}).get("by_kind", {}))
        return ks
    assert "smoke" in by_kind("smoking"), by_kind("smoking")
    assert "fire" in by_kind("burning"), by_kind("burning")
    assert "explosion" in by_kind("destroyed"), by_kind("destroyed")
    glow_kinds = set()
    for f in captured:
        if f["enemy_health"]["fighter-2"].get("repairing") == "engine":
            glow_kinds |= set((f.get("effects") or {}).get("by_kind", {}))
    assert "glow" in glow_kinds, glow_kinds

    return dict(
        smoke_changed=smoke_chg, burning_changed=burn_chg,
        capital_boom=cap_boom, fighter_boom=fighter_boom, glow_changed=glow_chg,
        destroyed_kinds=sorted(destroyed_kinds),
        event_tally=event_tally,
        audio_cues=sorted(audio_tally),
    )


# -- audio validation ---------------------------------------------------------
def run_audio(out, fire, damage, frames):
    out.mkdir(parents=True, exist_ok=False)
    cfg = out / "cfg"
    cfg.mkdir()
    wav = out / "emitted-audio.wav"
    (cfg / "alsoft.conf").write_text(
        "[general]\ndrivers = wave\nchannels = stereo\nsample-type = int16\n"
        "frequency = 48000\n\n[wave]\nfile = %s\n" % wav)
    env = dict(os.environ, ALSOFT_DRIVERS="wave", XDG_CONFIG_HOME=str(cfg))
    report = run_app(out / "app", fire=fire, damage=damage, frames=frames,
                     capture="1", mute=False, env=env)
    assert wav.exists(), "wave backend did not write audio"
    return wav, report


def wav_stats(wav):
    with wave.open(str(wav), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        n = w.getnframes()
        data = w.readframes(n)
    samples = struct.unpack("<%dh" % (n * ch), data)
    mono = []
    if ch == 2:
        for i in range(0, n * 2, 2):
            mono.append((samples[i] + samples[i + 1]) / 2)
    else:
        mono = list(samples)
    win = rate // 10
    rms = []
    for i in range(0, len(mono), win):
        c = mono[i:i + win]
        rms.append(math.sqrt(sum(x * x for x in c) / len(c)) if c else 0.0)
    return rms, max(abs(x) for x in mono)


def validate_audio(wav, report):
    rms, peak = wav_stats(wav)
    assert peak > 1500, peak           # the game actually emitted sound
    assert max(rms) > 800, max(rms)    # sustained combat audio
    tally = report.get("audio_tally", {})
    assert tally, "no audio tally in report"
    for cue in ("cannon", "impact", "explosion_fighter", "explosion_capital",
                "repair_start", "repair_complete"):
        assert tally.get(cue, 0) > 0, (cue, tally)
    assert report["audio"] == "openal", report["audio"]
    return dict(peak=peak, max_rms=round(max(rms), 1),
                duration_s=round(len(rms) / 10, 2), tally=tally)


# -- live combat validation ---------------------------------------------------
def run_live(out):
    out.mkdir(parents=True, exist_ok=False)
    fire = fire_script()
    write_json(out / "fire-script.json", fire)
    command = [sys.executable, "-m", "breach.app", "--mute", "--offscreen",
               "--demo-enemies",  # node-8 default is the mission loop (capital
                                  # only appears at wave 4); demo-enemies spawns
                                  # the 3-fighter + capital lab at once so the
                                  # capital turret actually engages here.
               "--frames", "240", "--trace-dir", str(out / "trace"),
               "--report", str(out / "report.json"),
               "--fire-script", str(out / "fire-script.json")]
    write_json(out / "command.json", command)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=300)
    (out / "runtime.log").write_text("COMMAND " + " ".join(command) + "\n" + result.stdout)
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise SystemExit("live app failed (%s)" % result.returncode)
    frames = frames_of(load_events(out))
    spawns = [s for f in frames for s in f.get("effects_spawns", [])]
    kinds = [s["event"] for s in spawns]
    assert "fire" in kinds, "no weapon-fire effects in live combat"
    assert "impact" in kinds, "no impact effects in live combat"
    counts = [f.get("effects", {}) for f in frames]
    by_kind = {}
    for c in counts:
        for k, v in (c.get("by_kind") or {}).items():
            by_kind[k] = max(by_kind.get(k, 0), v)
    assert by_kind.get("smoke", 0) > 0, by_kind  # the player took damage
    report = json.loads((out / "report.json").read_text())
    tally = report.get("audio_tally", {})
    assert tally.get("turret", 0) > 0, tally  # the capital fired back
    assert tally.get("cannon", 0) > 0, tally  # fighters fired
    return dict(spawn_kinds=sorted(set(kinds)), peak_by_kind=by_kind,
                audio_tally=tally)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    damage = progression_damage()
    capture = ",".join(str(n) for n in range(3, 300, 4))

    prog_dir = args.output / "progression"
    run_app(prog_dir, fire=[], damage=damage, frames=340, capture=capture, mute=True)
    prog = validate_progression(prog_dir, capture)

    audio_dir = args.output / "audio"
    wav, audio_report = run_audio(audio_dir, fire_script(), damage, frames=420)
    aud = validate_audio(wav, audio_report)

    live_dir = args.output / "live"
    live = run_live(live_dir)

    summary = dict(
        status="passed",
        progression=prog,
        audio=aud,
        emitted_audio_wav=str(wav),
        live=live,
        checks=[
            "progressive smoke (smoking) and fire (burning) render before destruction",
            "destruction renders an explosion; a capital's is larger than a fighter's",
            "repair recovery renders a green glow and emits start/complete cues",
            "weapon fire and impacts emit distinct effects and sounds (live run)",
            "the running game's mixed audio was captured via the OpenAL 'wave' backend",
        ],
        scope="Scripted offscreen combat + real audio capture; subjective quality "
              "is deferred to the human playtest node.",
    )
    write_json(args.output / "summary.json", summary)
    print("EFFECTS_RUNTIME_PASS " + json.dumps(summary))


if __name__ == "__main__":
    main()
