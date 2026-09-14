"""Procedural sound synthesis for combat effects (node 7).

Pure standard library, fully deterministic (fixed seeds, no wall-clock or
os.urandom). Each generator returns a list of float samples in [-1, 1] at
SAMPLE_RATE; write_wav persists them as 16-bit mono PCM WAV. These are the
redistributable sound assets: original procedural content (no third-party
recordings), regenerable from tools/gen_audio.py, and therefore MIT-licensed
with the rest of the repository. No runtime network, no external files.

The synthesis is deliberately simple (tone + filtered noise + envelope) so it
is legible and auditable, yet each cue has a distinct character: a cannon is a
short sharp crack, scatter is a wider blast, the torpedo is a descending
whoosh, the turret is a deep thud, impacts ring, and explosions carry a
low-frequency body whose length/weight scales with ship size.
"""
from __future__ import annotations

import math
import random
import struct
import wave

SAMPLE_RATE = 48000


def _clip(samples):
    return [max(-1.0, min(1.0, s)) for s in samples]


def write_wav(path, samples, rate=SAMPLE_RATE):
    """Persist float samples in [-1, 1] as 16-bit mono PCM WAV."""
    samples = _clip(samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for s in samples:
            frames += struct.pack("<h", int(round(s * 32767.0)))
        w.writeframes(bytes(frames))


def _noise(rng, n, lowpass=0.0):
    """White noise (uniform) with an optional one-pole low-pass (0 = none)."""
    out = []
    prev = 0.0
    for _ in range(n):
        raw = rng.uniform(-1.0, 1.0)
        if lowpass > 0.0:
            prev += lowpass * (raw - prev)
            out.append(prev)
        else:
            out.append(raw)
    return out


def _tone(freq, dur, phase=0.0):
    n = int(round(dur * SAMPLE_RATE))
    return [math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE + phase)
            for i in range(n)]


def _sweep(f0, f1, dur):
    """Log-frequency sweep from f0 to f1 over dur seconds."""
    n = int(round(dur * SAMPLE_RATE))
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        frac = t / dur if dur > 0 else 1.0
        f = f0 * ((f1 / f0) ** frac)
        phase += 2.0 * math.pi * f / SAMPLE_RATE
        out.append(math.sin(phase))
    return out


def _mix(*tracks):
    """Sum aligned sample lists (truncating to the shortest)."""
    n = min(len(t) for t in tracks)
    return [sum(t[i] for t in tracks) for i in range(n)]


def _gain(samples, g):
    return [s * g for s in samples]


def _env(samples, attack, decay_hold=0.0, exp=True):
    """Apply attack (linear) and exponential decay to a sample list."""
    n = len(samples)
    out = []
    for i, s in enumerate(samples):
        t = i / SAMPLE_RATE
        if t < attack:
            a = t / attack if attack > 0 else 1.0
        else:
            a = 1.0
        held = max(0.0, t - attack - decay_hold)
        d = math.exp(-held * 6.0) if exp else max(0.0, 1.0 - held)
        out.append(s * a * d)
    return out


def _concat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


# -- one-shot weapon fire ----------------------------------------------------
def cannon(seed=1):
    rng = _rng(seed)
    n = int(0.09 * SAMPLE_RATE)
    crack = _noise(rng, n, lowpass=0.35)
    body = _tone(150.0, 0.09)
    snd = _mix(_gain(crack, 0.9), _gain(body, 0.5))
    return _env(snd, 0.002)


def scatter(seed=2):
    rng = _rng(seed)
    n = int(0.22 * SAMPLE_RATE)
    blast = _noise(rng, n, lowpass=0.25)
    body = _tone(90.0, 0.22)
    snd = _mix(_gain(blast, 0.9), _gain(body, 0.7))
    return _env(snd, 0.004)


def torpedo(seed=3):
    rng = _rng(seed)
    n = int(0.6 * SAMPLE_RATE)
    whoosh = _noise(rng, n, lowpass=0.15)
    sweep = _sweep(300.0, 80.0, 0.6)
    snd = _mix(_gain(whoosh, 0.5), _gain(sweep, 0.6))
    return _env(snd, 0.03)


def turret(seed=4):
    rng = _rng(seed)
    n = int(0.35 * SAMPLE_RATE)
    thud = _noise(rng, n, lowpass=0.12)
    body = _tone(60.0, 0.35)
    snd = _mix(_gain(thud, 0.7), _gain(body, 0.8))
    return _env(snd, 0.006)


# -- impact ------------------------------------------------------------------
def impact(seed=5):
    rng = _rng(seed)
    n = int(0.18 * SAMPLE_RATE)
    ring = _tone(1650.0, 0.18)
    ring2 = _tone(2200.0, 0.18)
    tick = _noise(rng, n, lowpass=0.5)
    snd = _mix(_gain(ring, 0.45), _gain(ring2, 0.3), _gain(tick, 0.5))
    return _env(snd, 0.001)


# -- explosions (scale with ship size) ---------------------------------------
def _explosion(dur, body_freq, body_gain, noise_gain, seed):
    rng = _rng(seed)
    n = int(dur * SAMPLE_RATE)
    rumble = _sweep(body_freq * 1.6, body_freq * 0.6, dur)
    noise = _noise(rng, n, lowpass=0.08)
    crackle = _noise(_rng(seed + 1), n, lowpass=0.4)
    snd = _mix(_gain(rumble, body_gain), _gain(noise, noise_gain),
               _gain(crackle, noise_gain * 0.4))
    return _env(snd, 0.005)


def explosion_fighter(seed=6):
    return _explosion(1.1, 90.0, 0.8, 0.7, seed)


def explosion_capital(seed=7):
    # Longer, deeper, heavier: the boom communicates a capital ship's mass.
    return _explosion(2.6, 45.0, 0.9, 0.75, seed)


# -- repair cues -------------------------------------------------------------
def repair_start(seed=8):
    a = _tone(440.0, 0.14)
    b = _tone(660.0, 0.16)
    snd = _concat(_env(a, 0.01), _env(b, 0.01))
    return _gain(snd, 0.5)


def repair_complete(seed=9):
    notes = [_tone(f, 0.12) for f in (523.0, 659.0, 784.0)]
    parts = [_env(n, 0.01) for n in notes]
    return _gain(_concat(*parts), 0.55)


# -- looping fire crackle ----------------------------------------------------
def fire_loop(seed=10):
    """A 2 s crackling loop for burning ships (endpoints matched for looping)."""
    rng = _rng(seed)
    n = int(2.0 * SAMPLE_RATE)
    base = _noise(rng, n, lowpass=0.06)
    base = _gain(base, 0.25)
    # Sparse crackle pops on top.
    pops = [0.0] * n
    pos = 0
    while pos < n - 2400:
        pos += int(rng.uniform(400, 2200))
        strength = rng.uniform(0.4, 0.9)
        for k in range(min(2400, n - pos)):
            decay = math.exp(-k / 600.0)
            pops[pos + k] += strength * decay * rng.uniform(-1.0, 1.0)
    snd = _mix(base, pops)
    return snd


_GENERATORS = {
    "cannon": cannon,
    "scatter": scatter,
    "torpedo": torpedo,
    "turret": turret,
    "impact": impact,
    "explosion_fighter": explosion_fighter,
    "explosion_capital": explosion_capital,
    "repair_start": repair_start,
    "repair_complete": repair_complete,
    "fire_loop": fire_loop,
}


def _rng(seed):
    return random.Random(seed)


def synth(name, seed=None):
    """Return clipped float samples in [-1, 1] for a named sound."""
    if name not in _GENERATORS:
        raise KeyError(f"unknown sound: {name}")
    fn = _GENERATORS[name]
    return _clip(fn() if seed is None else fn(seed))


def synth_all(seed=0):
    """Ordered map of name -> clipped samples for every sound."""
    return {name: _clip(fn()) for name, fn in _GENERATORS.items()}


__all__ = [
    "SAMPLE_RATE", "write_wav", "synth", "synth_all", "cannon", "scatter",
    "torpedo", "turret", "impact", "explosion_fighter", "explosion_capital",
    "repair_start", "repair_complete", "fire_loop",
]
