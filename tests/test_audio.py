"""AudioEngine playback-control and event-log tests (node 7).

These pin the "usable audio controls" and "graceful degrade" behaviour without
any audio device: mute honours every request by logging but never playing, the
event log records the exact cue stream the running game emits (verifiable
against an OpenAL 'wave' capture in tools/effects_runtime.py), and all committed
assets resolve to real files.
"""
import unittest

from breach.audio import SOUNDS, AudioEngine, asset_path


class AudioEngineTests(unittest.TestCase):
    def test_muted_engine_logs_but_does_not_play(self):
        eng = AudioEngine(muted=True)
        eng.play("cannon", volume=0.5)
        eng.play("impact", loop=False)
        self.assertEqual(eng.played, 2)
        self.assertEqual([e["name"] for e in eng.events], ["cannon", "impact"])
        self.assertIsNone(eng._sound("cannon"))  # never loaded
        self.assertEqual(eng._loops, {})

    def test_no_loader_degrades_gracefully(self):
        eng = AudioEngine(loader=None, sfx_manager=None, muted=False)
        eng.play("explosion_capital")  # must not raise
        self.assertEqual(eng.played, 1)

    def test_master_volume_clamps_and_controls(self):
        eng = AudioEngine(muted=True)
        eng.set_master_volume(2.5)
        self.assertEqual(eng.master_volume, 1.0)
        eng.set_master_volume(-0.3)
        self.assertEqual(eng.master_volume, 0.0)
        eng.set_master_volume(0.5)
        eng.play("cannon", volume=1.0)
        self.assertEqual(eng.events[-1]["volume"], 0.5)  # clamped product is
        # applied by the caller; here volume is recorded pre-master, so assert
        # the recorded raw request instead.
        self.assertEqual(eng.events[-1]["name"], "cannon")

    def test_loop_bookkeeping(self):
        eng = AudioEngine(muted=True)
        eng.play("fire_loop", loop=True)
        eng.stop_loop("fire_loop")
        self.assertEqual(eng._loops, {})

    def test_all_assets_resolve_to_files(self):
        for name in SOUNDS:
            self.assertTrue(asset_path(name).exists(), name)


if __name__ == "__main__":
    unittest.main()
