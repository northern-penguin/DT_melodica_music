"""三角洲口琴单旋律演奏器。仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


# 打包后资源由 PyInstaller 放在 _MEIPASS；源码运行时就在脚本旁。
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
NOTE_PATTERN = re.compile(r"^(#?)([1-7])([+-]?)$")
KEYS = {"1": "z", "2": "x", "3": "c", "4": "v", "5": "b", "6": "n", "7": "m"}
VK = {**{letter: ord(letter.upper()) for letter in "zxcvbnm"}, ",": 0xBC}
BUTTONS = ("left", "middle", "right")
VK_F6 = 0x75
VK_F8 = 0x77


class ScoreError(ValueError):
    """曲谱格式或音域错误。"""


class PlaybackStopped(RuntimeError):
    """用户停止或游戏窗口失去焦点。"""


@dataclass(frozen=True)
class Event:
    # key=None 表示休止符；升降八度和半音通过鼠标按钮持续按住。
    key: str | None
    buttons: frozenset[str]
    beats: float
    symbol: str


@dataclass(frozen=True)
class Song:
    name: str
    bpm: float
    events: tuple[Event, ...]


def parse_event(raw: object, location: str) -> Event:
    """将 [简谱符号, 拍数] 转成可执行的键鼠动作。"""
    if not isinstance(raw, list) or len(raw) != 2:
        raise ScoreError(f"{location}：应为 [音符, 拍数]")
    symbol, beats = raw
    if not isinstance(symbol, str):
        raise ScoreError(f"{location}：音符必须是字符串")
    if isinstance(beats, bool) or not isinstance(beats, (int, float)) or not 0 < beats <= 16:
        raise ScoreError(f"{location}：拍数必须大于 0 且不超过 16")

    if symbol == "-":
        return Event(None, frozenset(), float(beats), symbol)

    match = NOTE_PATTERN.fullmatch(symbol)
    if match is None:
        raise ScoreError(f"{location}：无效音符 {symbol!r}；示例：1、#4、7-、1+")
    sharp, degree, octave = match.groups()
    buttons: set[str] = set()
    if octave == "-":
        buttons.add("left")
    elif octave == "+":
        buttons.add("right")
    if sharp:
        buttons.add("middle")

    # 高音 do 优先使用逗号；其余高八度音使用右键加原音级键。
    key = KEYS[degree]
    if degree == "1" and octave == "+":
        key = ","
        buttons.discard("right")
    return Event(key, frozenset(buttons), float(beats), symbol)


def parse_song(raw: object, location: str) -> Song:
    """校验歌曲字段、曲速和每个音符，错误信息定位到具体项。"""
    if not isinstance(raw, dict):
        raise ScoreError(f"{location}：歌曲应为 JSON 对象")
    name = raw.get("name")
    bpm = raw.get("bpm")
    notes = raw.get("notes")
    if not isinstance(name, str) or not name.strip():
        raise ScoreError(f"{location}.name：需要非空名称")
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not 20 <= bpm <= 300:
        raise ScoreError(f"{location}.bpm：速度必须在 20–300 之间")
    if not isinstance(notes, list) or not notes:
        raise ScoreError(f"{location}.notes：需要非空音符列表")
    events = tuple(parse_event(note, f"{location}.notes[{index}]") for index, note in enumerate(notes))
    return Song(name.strip(), float(bpm), events)


def load_songs(path: Path) -> list[Song]:
    """读取内置曲库或单首自定义曲谱。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScoreError(f"读取 {path} 失败：{exc}") from exc
    records = raw if isinstance(raw, list) else [raw]
    if not records:
        raise ScoreError(f"{path}：曲库不能为空")
    return [parse_song(item, f"{path.name}[{index}]") for index, item in enumerate(records)]


class Output(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def button_down(self, button: str) -> None: ...
    def button_up(self, button: str) -> None: ...


class NoteDriver:
    """记录脚本按住的输入，确保切音与退出时能够全部释放。"""

    def __init__(self, output: Output):
        self.output = output
        self.key: str | None = None
        self.buttons: set[str] = set()

    def release_key(self) -> None:
        if self.key is not None:
            key = self.key
            self.output.key_up(key)
            self.key = None

    def set_note(self, event: Event) -> None:
        self.release_key()
        # 先松开旧修饰键，再按下新修饰键，最后触发音符。
        for button in BUTTONS:
            if button in self.buttons and button not in event.buttons:
                self.output.button_up(button)
                self.buttons.remove(button)
        for button in BUTTONS:
            if button in event.buttons and button not in self.buttons:
                self.output.button_down(button)
                self.buttons.add(button)
        if event.key is not None:
            self.output.key_down(event.key)
            self.key = event.key

    def release_all(self) -> None:
        errors: list[Exception] = []
        try:
            self.release_key()
        except Exception as exc:
            errors.append(exc)
        for button in BUTTONS:
            if button in self.buttons:
                try:
                    self.output.button_up(button)
                    self.buttons.remove(button)
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise RuntimeError(f"释放输入失败：{errors[0]}") from errors[0]


def wait_until(
    deadline: float,
    is_active: Callable[[], bool],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """短间隔检查停止键和窗口焦点，同时按单调时钟保持节奏。"""
    while True:
        if not is_active():
            raise PlaybackStopped("已按 F8 停止，或前台窗口发生变化")
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(0.01, remaining))


def play_song(
    song: Song,
    driver: NoteDriver,
    is_active: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """逐音演奏；每个音尾留少量间隙，避免相邻同音连成一声。"""
    deadline = clock()
    seconds_per_beat = 60.0 / song.bpm
    try:
        for event in song.events:
            if not is_active():
                raise PlaybackStopped("已按 F8 停止，或前台窗口发生变化")
            duration = event.beats * seconds_per_beat
            deadline += duration
            if event.key is None:
                driver.release_all()
                wait_until(deadline, is_active, clock, sleep)
                continue
            driver.set_note(event)
            gap = min(0.04, max(0.005, duration * 0.12))
            wait_until(deadline - gap, is_active, clock, sleep)
            driver.release_key()
            wait_until(deadline, is_active, clock, sleep)
    finally:
        driver.release_all()


class WindowsOutput:
    """通过 Windows SendInput 发送普通键鼠事件，不访问游戏进程。"""

    KEYEVENTF_KEYUP = 0x0002
    MOUSE_FLAGS = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("键鼠输入仅支持 Windows")

        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p),
            ]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("data", InputUnion)]

        self.Input = Input
        self.InputUnion = InputUnion
        self.MouseInput = MouseInput
        self.KeyboardInput = KeyboardInput
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(Input), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetForegroundWindow.argtypes = ()
        self.user32.GetForegroundWindow.restype = ctypes.c_void_p
        self.user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        self.user32.GetWindowThreadProcessId.restype = ctypes.c_ulong

    def _send(self, item: ctypes.Structure) -> None:
        if self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(self.Input)) != 1:
            raise OSError(ctypes.get_last_error(), "SendInput 失败")

    def _key(self, key: str, up: bool) -> None:
        item = self.Input(1, self.InputUnion(ki=self.KeyboardInput(VK[key], 0, self.KEYEVENTF_KEYUP if up else 0, 0, None)))
        self._send(item)

    def _button(self, button: str, up: bool) -> None:
        flag = self.MOUSE_FLAGS[button][1 if up else 0]
        item = self.Input(0, self.InputUnion(mi=self.MouseInput(0, 0, 0, flag, 0, None)))
        self._send(item)

    def key_down(self, key: str) -> None:
        self._key(key, False)

    def key_up(self, key: str) -> None:
        self._key(key, True)

    def button_down(self, button: str) -> None:
        self._button(button, False)

    def button_up(self, button: str) -> None:
        self._button(button, True)

    def pressed(self, virtual_key: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(virtual_key) & 0x8000)

    def foreground_window(self) -> int | None:
        return self.user32.GetForegroundWindow()

    def foreground_process_id(self) -> int:
        # GUI 可据此避免把演奏输入发给自身窗口。
        process_id = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(self.foreground_window(), ctypes.byref(process_id))
        return process_id.value


def choose_song(songs: list[Song], requested: int | None) -> Song:
    for index, song in enumerate(songs, 1):
        print(f"{index}. {song.name}（{song.bpm:g} BPM，{len(song.events)} 个事件）")
    if requested is not None:
        if not 1 <= requested <= len(songs):
            raise ScoreError(f"--song 应在 1–{len(songs)} 之间")
        return songs[requested - 1]
    while True:
        choice = input("选择曲目编号：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(songs):
            return songs[int(choice) - 1]
        print(f"请输入 1–{len(songs)}。")


def main() -> int:
    parser = argparse.ArgumentParser(description="三角洲口琴单旋律自动演奏器")
    parser.add_argument("--file", type=Path, help="自定义 JSON 曲谱或曲库")
    parser.add_argument("--playlist", help="从指定歌单选曲与循环；与 --file 不同时使用")
    parser.add_argument("--loop-mode", choices=("single", "sequence", "random"), help="覆盖设置中的循环模式")
    parser.add_argument("--song", type=int, help="曲目编号；省略则交互选择")
    parser.add_argument("--list", action="store_true", help="列出曲目并退出")
    args = parser.parse_args()
    try:
        from playback_control import run_session
        from portable_library import (ensure_data_dir, load_library, load_playlists,
                                      load_settings, migrate_legacy)
        if args.file and args.playlist:
            raise ScoreError("--file 与 --playlist 不能同时使用")
        ensure_data_dir()
        for line in migrate_legacy():
            print(line)
        settings = load_settings()
        if args.file:
            queue = [(f"file:{index}", song) for index, song in enumerate(load_songs(args.file))]
        else:
            entries, warnings = load_library()
            for warning in warnings:
                print(warning, file=sys.stderr)
            if args.playlist:
                playlists = load_playlists()
                if args.playlist not in playlists:
                    raise ScoreError(f"歌单不存在：{args.playlist}")
                ids = playlists[args.playlist]
                queue = [(song_id, entry.song) for song_id in ids for entry in entries if entry.song_id == song_id]
            else:
                queue = [(entry.song_id, entry.song) for entry in entries]
        songs = [song for _, song in queue]
        if not songs:
            raise ScoreError("没有可播放的歌曲")
        if args.list:
            for index, song in enumerate(songs, 1):
                print(f"{index}. {song.name}")
            return 0
        song = choose_song(songs, args.song)
        song_id = queue[next(index for index, current in enumerate(songs) if current is song)][0]
        output = WindowsOutput()
        hotkeys = settings["hotkeys"]
        print(f"已选择《{song.name}》。切换到游戏并拿出口琴；{hotkeys['start']} 单次播放，{hotkeys['loop']} 循环，{hotkeys['stop']} 停止。")
        run_session(song_id, queue, args.loop_mode or settings["loop_mode"], hotkeys,
                    output, threading.Event(), on_status=print,
                    on_track=lambda current: print(f"正在演奏《{current.name}》"))
        return 0
    except (ScoreError, OSError, RuntimeError, ValueError, KeyboardInterrupt, EOFError) as exc:
        print(f"已停止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
