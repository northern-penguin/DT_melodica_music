"""循环选择及热键切换时的输入释放。"""

import random
import threading
import unittest

from harmonica import PlaybackStopped, Song, parse_event
from playback_control import next_song_id, run_session


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeOutput:
    def __init__(self, clock, *, single=False, stop_at=None, focus_at=None):
        self.clock = clock
        self.held = set()
        self.single = single
        self.stop_at = stop_at
        self.focus_at = focus_at

    def pressed(self, key):
        t = self.clock.value
        if key == 0x75 and self.single:
            return 0.04 <= t < 0.08
        if key == 0x77 and self.stop_at is not None:
            return t >= self.stop_at
        if key == 0x76:  # F7：先启动循环，第二首播放时关闭循环。
            return not self.single and (0.04 <= t < 0.08 or 0.53 <= t < 0.58)
        return False

    def foreground_window(self):
        return 2 if self.focus_at is not None and self.clock.value >= self.focus_at else 1

    def key_down(self, key):
        self.held.add(("key", key))

    def key_up(self, key):
        self.held.discard(("key", key))

    def button_down(self, button):
        self.held.add(("button", button))

    def button_up(self, button):
        self.held.discard(("button", button))


class PlaybackControlTests(unittest.TestCase):
    def test_next_song_modes(self):
        queue = ["a", "b", "c"]
        self.assertEqual(next_song_id("b", queue, "single"), "b")
        self.assertEqual(next_song_id("c", queue, "sequence"), "a")
        self.assertNotEqual(next_song_id("b", queue, "random", random.Random(1)), "b")

    def test_f7_starts_and_toggles_loop(self):
        clock = FakeClock()
        output = FakeOutput(clock)
        song = Song("测试", 300, (parse_event(["#1-", 1], "测试"),))
        tracks = []
        statuses = []
        run_session("a", [("a", song)], "single",
                    {"start": "F6", "loop": "F7", "stop": "F8"},
                    output, threading.Event(), clock=clock.now, sleep=clock.sleep,
                    on_track=lambda item: tracks.append(item.name), on_status=statuses.append)
        self.assertEqual(len(tracks), 2)
        self.assertFalse(output.held)
        self.assertTrue(any("关闭" in message for message in statuses))

    def test_f6_single_and_f8_or_focus_release(self):
        song = Song("测试", 300, (parse_event(["#1-", 1], "测试"),))
        for stop_at, focus_at in ((0.12, None), (None, 0.12)):
            with self.subTest(stop_at=stop_at, focus_at=focus_at):
                clock = FakeClock()
                output = FakeOutput(clock, single=True, stop_at=stop_at, focus_at=focus_at)
                with self.assertRaises(PlaybackStopped):
                    run_session("a", [("a", song)], "single",
                                {"start": "F6", "loop": "F7", "stop": "F8"},
                                output, threading.Event(), clock=clock.now, sleep=clock.sleep)
                self.assertFalse(output.held)


if __name__ == "__main__":
    unittest.main()
