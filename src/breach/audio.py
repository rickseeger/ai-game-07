"""Audio playback and runtime recording for combat effects (node 7).

A thin, dependency-light wrapper over Panda3D's OpenAL audio manager. It owns
the redistributable sound assets (src/breach/audio/*.wav) and provides the two
things the effects system needs:

- play(name, volume, loop) / stop_loop(name): trigger a cue through the real
  audio backend, with a master-volume and mute control ("usable audio controls").
- an event log (self.events) that records every cue the running game requests,
  so the emitted-audio stream is independently verifiable against a captured
  OpenAL output (see tools/effects_runtime.py, which runs the app against the
  OpenAL Soft 'wave' backend to record the actual mixed output).

Design:
- muted=True (or no usable audio manager) degrades gracefully: cues are logged
  but never attempt to load/play, so a headless host without a device never
  crashes and --mute is honoured end-to-end.
- Sounds load lazily on first play; loadSfx returning None (null backend) is
  treated exactly like mute.
- Volume is clamped to [0, 1] and multiplied by master_volume, so effects stay
  bounded and controllable. Distance-based attenuation is applied by the caller
  (EffectsSystem) before calling play(), keeping this module device-agnostic.
"""
from __future__ import annotations

from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent / "audio"

# Sound names the effects system can play (keys of synth.py generators).
SOUNDS = (
    "cannon", "scatter", "torpedo", "turret",
    "impact",
    "explosion_fighter", "explosion_capital",
    "repair_start", "repair_complete",
    "fire_loop",
)


def _clamp01(v):
    return max(0.0, min(1.0, v))


def asset_path(name):
    """Filesystem path to a sound asset (no existence guarantee)."""
    return AUDIO_DIR / f"{name}.wav"


class AudioEngine:
    """Plays the synthesized cues and records every play request."""

    def __init__(self, loader=None, sfx_manager=None, muted=False,
                 master_volume=1.0):
        self.loader = loader
        self.sfx_manager = sfx_manager
        self.muted = bool(muted) or sfx_manager is None
        self.master_volume = _clamp01(master_volume)
        self._sounds = {}       # name -> AudioSound | None
        self._loops = {}        # name -> AudioSound (currently looping)
        self.events = []        # dict(name, volume, loop) in play order
        self.played = 0         # total play() requests (incl. muted)

    # -- controls -----------------------------------------------------------
    def set_master_volume(self, value):
        self.master_volume = _clamp01(value)

    def set_mute(self, value):
        self.muted = bool(value)

    # -- loading ------------------------------------------------------------
    def _sound(self, name):
        if name not in self._sounds:
            if self.loader is None or self.muted:
                self._sounds[name] = None
            else:
                try:
                    self._sounds[name] = self.loader.loadSfx(str(asset_path(name)))
                except Exception:
                    self._sounds[name] = None
        return self._sounds.get(name)

    # -- playback -----------------------------------------------------------
    def play(self, name, volume=1.0, loop=False):
        """Request a cue. Logged regardless; actually played only when unmuted."""
        volume = _clamp01(volume)
        effective = _clamp01(volume * self.master_volume)
        self.events.append({"name": name, "volume": round(effective, 4),
                            "loop": bool(loop)})
        self.played += 1
        if self.muted:
            return
        snd = self._sound(name)
        if snd is None:
            return
        try:
            snd.setVolume(effective)
            snd.setLoop(loop)
            snd.play()
            if loop:
                self._loops[name] = snd
        except Exception:
            # A failed play must never break the simulation loop.
            pass

    def stop_loop(self, name):
        snd = self._loops.pop(name, None)
        if snd is not None:
            try:
                snd.stop()
            except Exception:
                pass


__all__ = ["AudioEngine", "asset_path", "SOUNDS", "AUDIO_DIR"]
