"""桌面窗口与命令行共用的热键和循环演奏控制。"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

from harmonica import NoteDriver, PlaybackStopped, Song, WindowsOutput, play_song, wait_until
from portable_library import validate_hotkeys


def virtual_key(label: str) -> int:
    return 0x70 + int(label[1:]) - 1


def next_song_id(current: str, queue: list[str], mode: str,
                 picker: random.Random | None = None) -> str:
    """单曲、顺序和随机循环都只从当前播放队列取歌。"""
    if not queue:
        raise ValueError("播放队列为空")
    if mode == "single":
        return current
    if mode == "sequence":
        return queue[(queue.index(current) + 1) % len(queue)] if current in queue else queue[0]
    if mode == "random":
        choices = [song_id for song_id in queue if song_id != current]
        return (picker or random).choice(choices or queue)
    raise ValueError("循环模式无效")


def run_session(
    initial_id: str,
    queue: list[tuple[str, Song]],
    mode: str,
    hotkeys: dict[str, str],
    output: WindowsOutput,
    stop_event: threading.Event,
    *,
    target_allowed: Callable[[], bool] = lambda: True,
    on_status: Callable[[str], None] = lambda _message: None,
    on_track: Callable[[Song], None] = lambda _song: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    picker: random.Random | None = None,
) -> None:
    """等待开始键，再在原前台窗口按循环状态逐曲演奏。"""
    keys = {name: virtual_key(value) for name, value in validate_hotkeys(hotkeys).items()}
    song_map = dict(queue)
    if initial_id not in song_map:
        raise ValueError("选中的曲目不在播放队列中")
    previous = {name: output.pressed(code) for name, code in keys.items()}
    looping = False
    target = None
    while not stop_event.is_set():
        current = {name: output.pressed(code) for name, code in keys.items()}
        if current["stop"]:
            raise PlaybackStopped("已按停止热键")
        if current["start"] and not previous["start"]:
            looping = False
            target = output.foreground_window()
            break
        if current["loop"] and not previous["loop"]:
            looping = True
            target = output.foreground_window()
            break
        previous = current
        sleep(0.03)
    if stop_event.is_set():
        raise PlaybackStopped("已停止")
    if not target or not target_allowed():
        raise PlaybackStopped("请切换到游戏窗口后再按演奏热键")

    last_loop_pressed = output.pressed(keys["loop"])

    def active() -> bool:
        nonlocal looping, last_loop_pressed
        if stop_event.is_set() or output.pressed(keys["stop"]) or output.foreground_window() != target:
            return False
        pressed = output.pressed(keys["loop"])
        if pressed and not last_loop_pressed:
            looping = not looping
            on_status("循环已开启" if looping else "循环已关闭；当前歌曲结束后停止")
        last_loop_pressed = pressed
        return True

    current_id = initial_id
    queue_ids = [song_id for song_id, _ in queue]
    while True:
        if not active():
            raise PlaybackStopped("已停止，或游戏窗口失去焦点")
        song = song_map[current_id]
        on_track(song)
        play_song(song, NoteDriver(output), active, clock, sleep)
        if not looping:
            on_status("演奏完成")
            return
        # 切歌前短暂释放输入，同时继续监听停止和循环热键。
        wait_until(clock() + 0.15, active, clock, sleep)
        if not looping:
            on_status("演奏完成")
            return
        current_id = next_song_id(current_id, queue_ids, mode, picker)
