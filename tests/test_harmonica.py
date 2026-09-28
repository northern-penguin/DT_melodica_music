"""使用假时钟和输入记录器验证演奏逻辑，不发送真实键鼠输入。"""

import unittest

from harmonica import NoteDriver, PlaybackStopped, ScoreError, parse_event, parse_song, play_song


class RecordingOutput:
    def __init__(self):
        self.events = []

    def key_down(self, key):
        self.events.append(("key_down", key))

    def key_up(self, key):
        self.events.append(("key_up", key))

    def button_down(self, button):
        self.events.append(("button_down", button))

    def button_up(self, button):
        self.events.append(("button_up", button))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HarmonicaTests(unittest.TestCase):
    def test_note_mapping_and_invalid_scores(self):
        self.assertEqual(parse_event(["1+", 1], "test").key, ",")
        self.assertEqual(parse_event(["#1+", 1], "test").buttons, frozenset({"middle"}))
        self.assertEqual(parse_event(["#2-", 0.5], "test").buttons, frozenset({"left", "middle"}))
        self.assertIsNone(parse_event(["-", 1], "test").key)
        for note in (["1++", 1], ["8", 1], ["1", 0], ["1", -1], ["1", 17]):
            with self.subTest(note=note), self.assertRaises(ScoreError):
                parse_event(note, "test.notes[3]")

    def test_modifier_order_mutual_exclusion_and_rest(self):
        output = RecordingOutput()
        driver = NoteDriver(output)
        driver.set_note(parse_event(["#2-", 1], "test"))
        driver.set_note(parse_event(["3+", 1], "test"))
        driver.set_note(parse_event(["-", 1], "test"))
        driver.release_all()
        self.assertEqual(output.events, [
            ("button_down", "left"), ("button_down", "middle"), ("key_down", "x"),
            ("key_up", "x"), ("button_up", "left"), ("button_up", "middle"),
            ("button_down", "right"), ("key_down", "c"),
            ("key_up", "c"), ("button_up", "right"),
        ])

    def test_playback_timing_and_release(self):
        song = parse_song({"name": "测试", "bpm": 120, "notes": [["1", 1], ["1", 0.5], ["-", 0.5]]}, "test")
        output = RecordingOutput()
        fake = FakeClock()
        driver = NoteDriver(output)
        play_song(song, driver, lambda: True, fake.clock, fake.sleep)
        self.assertAlmostEqual(fake.now, 1.0)
        self.assertEqual(output.events, [
            ("key_down", "z"), ("key_up", "z"),
            ("key_down", "z"), ("key_up", "z"),
        ])
        self.assertIsNone(driver.key)
        self.assertFalse(driver.buttons)

    def test_stop_or_focus_loss_releases_everything(self):
        song = parse_song({"name": "测试", "bpm": 60, "notes": [["#3-", 4]]}, "test")
        output = RecordingOutput()
        fake = FakeClock()
        driver = NoteDriver(output)
        with self.assertRaises(PlaybackStopped):
            play_song(song, driver, lambda: fake.now < 0.15, fake.clock, fake.sleep)
        self.assertEqual(output.events[-3:], [
            ("key_up", "c"), ("button_up", "left"), ("button_up", "middle"),
        ])
        self.assertIsNone(driver.key)
        self.assertFalse(driver.buttons)


if __name__ == "__main__":
    unittest.main()
