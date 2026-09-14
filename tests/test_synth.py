"""Procedural sound-synthesis tests (node 7).

Verifies the redistributable audio assets are deterministic, valid WAV files,
non-silent, within [-1, 1], and that distinct cues are audibly distinct (length
and spectral footprint differ where the game depends on it, e.g. a capital
explosion is heavier/longer than a fighter's).
"""
import math
import unittest
import wave
from pathlib import Path

from breach.synth import (
    SAMPLE_RATE, explosion_capital, explosion_fighter, synth, synth_all,
    write_wav,
)

AUDIO_DIR = Path(__file__).resolve().parents[1] / "src" / "breach" / "audio"


def _peak(samples):
    return max(abs(s) for s in samples)


def _rms(samples):
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class SynthesisTests(unittest.TestCase):
    def test_every_sound_is_finite_in_range_and_non_silent(self):
        for name, samples in synth_all().items():
            self.assertGreater(len(samples), 0, name)
            self.assertTrue(all(math.isfinite(s) for s in samples), name)
            self.assertLessEqual(_peak(samples), 1.0 + 1e-6, name)
            self.assertGreater(_rms(samples), 0.01, name)

    def test_synthesis_is_deterministic(self):
        for name in synth_all():
            self.assertEqual(synth(name), synth(name), name)

    def test_capital_explosion_is_longer_and_heavier(self):
        fighter = synth("explosion_fighter")
        capital = synth("explosion_capital")
        self.assertGreater(len(capital), len(fighter))
        # Heavier: more total acoustic energy, not just longer.
        energy = lambda s: sum(x * x for x in s)
        self.assertGreater(energy(capital), energy(fighter))

    def test_distinct_cues_have_distinct_lengths(self):
        all_sounds = synth_all()
        lengths = {n: len(s) for n, s in all_sounds.items()}
        # At least three cues must differ in duration (the library is not a
        # single re-skinned buffer).
        self.assertGreaterEqual(len(set(lengths.values())), 3, lengths)

    def test_unknown_sound_raises(self):
        with self.assertRaises(KeyError):
            synth("does-not-exist")


class WavFileTests(unittest.TestCase):
    def test_write_wav_produces_valid_16bit_mono_pcm(self):
        samples = synth("cannon")
        path = Path("/tmp/_test_cannon.wav")
        write_wav(path, samples)
        try:
            with wave.open(str(path), "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertEqual(w.getframerate(), SAMPLE_RATE)
                self.assertEqual(w.getnframes(), len(samples))
        finally:
            path.unlink(missing_ok=True)

    def test_committed_assets_exist_and_are_valid_wav(self):
        manifest = (AUDIO_DIR / "manifest.json").read_text()
        for name in synth_all():
            path = AUDIO_DIR / f"{name}.wav"
            self.assertTrue(path.exists(), name)
            self.assertIn(f"{name}.wav", manifest, name)
            with wave.open(str(path), "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertGreater(w.getnframes(), 0, name)
            # regenerate -> byte-identical (assets are reproducible)
            regen = Path("/tmp/_regen.wav")
            write_wav(regen, synth(name))
            self.assertEqual(path.read_bytes(), regen.read_bytes(), name)
            regen.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
