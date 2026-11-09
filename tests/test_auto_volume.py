import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QCoreApplication
from auto_volume import AutoVolume, DualEnvelope
from config import Config


class FakeAudio:
    volume = 80
    peak = 1.0

    def get_volume(self, name):
        return self.volume

    def get_peak(self, name):
        return self.peak

    def set_volume(self, name, value, *, unmute=True):
        assert not unmute
        self.volume = value
        return value


class AutoVolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.audio = FakeAudio()
        self.cfg = Config(auto_volume_enabled=True)
        self.controller = AutoVolume(self.audio, self.cfg)
        self.controller.timer.stop()

    def test_attenuates_and_restores(self):
        self.controller.tick()
        self.assertEqual(self.audio.volume, 20)
        self.assertEqual(self.controller.base, 80)
        self.controller.tick()
        self.assertEqual(self.audio.volume, 20)  # No feedback/pumping.
        self.cfg.auto_volume_enabled = False
        self.controller.tick()
        self.assertEqual(self.audio.volume, 80)

    def test_silence_never_boosts(self):
        self.audio.peak = 0
        self.controller.tick()
        self.assertEqual(self.audio.volume, 80)

    def test_manual_ceiling_and_external_edit(self):
        self.controller.tick()
        self.controller.set_manual(60)
        self.assertEqual(self.controller.base, 60)
        self.audio.volume = 50
        self.controller.tick()
        self.assertEqual(self.controller.base, 50)

    def test_missing_meter_and_restart(self):
        self.controller.tick()
        self.audio.peak = None
        self.controller.tick()
        self.assertEqual(self.audio.volume, 80)
        self.audio.volume = None
        self.controller.tick()
        self.assertIsNone(self.controller.base)

    def test_sustained_engine(self):
        env = DualEnvelope()
        for _ in range(60):
            gain = env.step(.11, 100, .08, self.cfg)
        self.assertTrue(env.noise_active)
        self.assertAlmostEqual(gain, .25, places=2)
        for _ in range(40):
            gain = env.step(.02, 100, .08, self.cfg)
        self.assertFalse(env.noise_active)
        self.assertEqual(gain, 1)

    def test_noise_slider_respects_manual_base_and_floor(self):
        env = DualEnvelope()
        self.cfg.auto_min_gain = 15
        self.cfg.auto_noise_volume = 20
        for _ in range(60):
            gain = env.step(.15, 80, .08, self.cfg)
        self.assertAlmostEqual(gain, .2)
        self.assertAlmostEqual(80 * gain, 16)
        self.cfg.auto_noise_volume = 5
        for _ in range(20):
            gain = env.step(.15, 80, .08, self.cfg)
        self.assertAlmostEqual(gain, .15)

    def test_short_noise_and_shot_recovery(self):
        self.cfg.auto_impacts_progressive = False
        env = DualEnvelope()
        for _ in range(50):
            gain = env.step(.02, 100, .08, self.cfg)
        self.assertEqual(gain, 1)
        gain = env.step(.25, 100, .08, self.cfg)
        self.assertEqual(gain, 1)  # A bush does not trigger peak protection.
        gain = env.step(1, 100, .08, self.cfg)
        self.assertEqual(gain, .25)
        for _ in range(10):
            gain = env.step(.02, 100, .08, self.cfg)
        self.assertEqual(gain, 1)
        self.assertFalse(env.noise_active)

    def test_repeated_impacts_hold_and_expire(self):
        env = DualEnvelope()
        self.cfg.auto_noise_enabled = False
        for _ in range(4):
            for _ in range(2):
                env.step(1, 100, .08, self.cfg)
            for _ in range(18):
                env.step(.01, 100, .08, self.cfg)
        self.assertLessEqual(env.impact_gain, .3)
        for _ in range(40):
            env.step(.01, 100, .08, self.cfg)
        self.assertEqual(env.impact_gain, 1)

    def test_long_burst_is_not_repeated_impacts(self):
        env = DualEnvelope()
        for _ in range(50):
            env.step(1, 100, .08, self.cfg)
        self.assertEqual(env.impact_count, 0)
        self.assertEqual(env.impact_gain, 1)

    def test_disable_impacts_clears_hold(self):
        env = DualEnvelope()
        env.impact_until = 5
        env.impact_gain = .3
        self.cfg.auto_impacts_enabled = False
        env.step(.01, 100, .08, self.cfg)
        self.assertEqual(env.impact_gain, 1)
        self.assertEqual(env.impact_until, 0)

    def test_progressive_steps(self):
        env = DualEnvelope()
        def impulse():
            for _ in range(2):
                env.step(1, 100, .08, self.cfg)
            for _ in range(2):
                env.step(.01, 100, .08, self.cfg)
        impulse()
        self.assertEqual(env.impact_count, 1)
        self.assertAlmostEqual(env.impact_gain, .6)
        for _ in range(15):
            env.step(.01, 100, .08, self.cfg)
        impulse()
        self.assertEqual(env.impact_count, 2)
        self.assertAlmostEqual(env.impact_gain, .3)
        self.cfg.auto_impacts_progressive = False
        env.step(.01, 100, .08, self.cfg)
        self.assertEqual(env.impact_gain, 1)

    def test_legacy_three_impulses(self):
        self.cfg.auto_impacts_progressive = False
        env = DualEnvelope()
        for count in range(1, 4):
            for _ in range(2):
                env.step(1, 100, .08, self.cfg)
            for _ in range(18):
                env.step(.01, 100, .08, self.cfg)
            self.assertEqual(env.impact_gain, 1 if count < 3 else .3)


if __name__ == "__main__":
    unittest.main()
