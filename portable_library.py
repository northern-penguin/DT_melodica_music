"""DTmusic 的便携曲库、歌单与旧数据迁移。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from harmonica import ROOT, Song, load_songs, parse_song


DEFAULT_SETTINGS = {
    "notation": "简谱", "meter": "4/4", "bpm": "96", "key": "C",
    "hotkeys": {"start": "F6", "loop": "F7", "stop": "F8"},
    "loop_mode": "single", "active_playlist": None,
}
LOOP_MODES = {"single", "sequence", "random"}


def app_root() -> Path:
    # 冻结后以 exe 所在目录为准；源码运行时以项目目录为准。
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def data_dir() -> Path:
    return app_root() / "data"


def ensure_data_dir() -> Path:
    base = data_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / f".write-{uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise OSError(f"程序目录不可写：{base}。请将 DTmusic 移到可写的文件夹。") from exc
    return base


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def legacy_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DTmusic"


def migrate_legacy(source: Path | None = None, destination: Path | None = None) -> list[str]:
    """仅迁移已知数据；逐文件核验后才移除旧文件。"""
    source = source or legacy_data_dir()
    destination = destination or ensure_data_dir()
    if not source.is_dir() or source.resolve() == destination.resolve():
        return []
    report: list[str] = []
    candidates = [(source / name, destination / name) for name in ("settings.json", "dtmusic.log", "selftest.json")]
    candidates += [(path, destination / "library" / path.name) for path in sorted((source / "library").glob("*.json"))]
    for original, wanted in candidates:
        if not original.is_file():
            continue
        try:
            if original.suffix == ".json":
                if original.parent.name == "library":
                    load_songs(original)
                else:
                    json.loads(original.read_text(encoding="utf-8"))
            digest = lambda path: hashlib.sha256(path.read_bytes()).digest()
            existing = [wanted, *wanted.parent.glob(f"{wanted.stem}-migrated-*{wanted.suffix}")]
            if any(path.is_file() and digest(path) == digest(original) for path in existing):
                original.unlink()
                report.append(f"已清理已迁移的原件：{original.name}")
                continue
            target = wanted
            serial = 1
            while target.exists():
                target = wanted.with_name(f"{wanted.stem}-migrated-{serial}{wanted.suffix}")
                serial += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + f".{uuid4().hex}.tmp")
            try:
                shutil.copy2(original, temporary)
                if digest(original) != digest(temporary):
                    raise OSError("复制校验失败")
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            original.unlink()
            report.append(f"已迁移：{original.name} → {target.name}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report.append(f"迁移失败，原件保留：{original}：{exc}")
    # 只移除已空的旧目录；未知文件永远保留。
    for folder in (source / "library", source):
        try:
            folder.rmdir()
        except OSError:
            pass
    return report


def load_settings() -> dict:
    raw = _read_json(data_dir() / "settings.json", {})
    if not isinstance(raw, dict):
        raw = {}
    result = dict(DEFAULT_SETTINGS)
    result.update({key: value for key, value in raw.items() if key in DEFAULT_SETTINGS})
    result["hotkeys"] = validate_hotkeys(result.get("hotkeys", {}))
    if result["loop_mode"] not in LOOP_MODES:
        result["loop_mode"] = "single"
    return result


def save_settings(settings: dict) -> None:
    settings = dict(settings)
    settings["hotkeys"] = validate_hotkeys(settings.get("hotkeys", {}))
    if settings.get("loop_mode") not in LOOP_MODES:
        raise ValueError("循环模式无效")
    _atomic_json(ensure_data_dir() / "settings.json", settings)


def validate_hotkeys(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("热键设置必须是对象")
    keys = {name: str(raw.get(name, DEFAULT_SETTINGS["hotkeys"][name])).upper()
            for name in ("start", "loop", "stop")}
    if any(key not in {f"F{i}" for i in range(1, 13)} for key in keys.values()):
        raise ValueError("热键只能使用 F1–F12")
    if len(set(keys.values())) != 3:
        raise ValueError("开始、循环、停止热键不能重复")
    return keys


@dataclass(frozen=True)
class LibraryEntry:
    song_id: str
    song: Song
    source: Path
    record_index: int
    builtin: bool
    raw: dict


def _state() -> dict:
    raw = _read_json(data_dir() / "library_state.json", {})
    return raw if isinstance(raw, dict) else {}


def _save_state(state: dict) -> None:
    _atomic_json(ensure_data_dir() / "library_state.json", state)


def _records(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else [raw]
    if not records or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"曲库文件无效：{path}")
    for index, item in enumerate(records):
        parse_song(item, f"{path.name}[{index}]")
    return records


def load_library() -> tuple[list[LibraryEntry], list[str]]:
    """资源曲目与用户曲目合并；内置修改版覆盖原条目。"""
    base = ensure_data_dir()
    hidden = set(_state().get("hidden", []))
    state = _state()
    record_ids = state.setdefault("record_ids", {})
    ids_changed = False
    entries: list[LibraryEntry] = []
    warnings: list[str] = []
    for index, original in enumerate(_records(ROOT / "songs.json")):
        song_id = f"builtin:{index}"
        if song_id in hidden:
            continue
        override = base / "library" / f"builtin-{index}.json"
        source = override if override.is_file() else ROOT / "songs.json"
        try:
            raw = _records(override)[0] if override.is_file() else original
            entries.append(LibraryEntry(song_id, parse_song(raw, song_id), source,
                                        0 if override.is_file() else index, True, raw))
        except Exception as exc:
            warnings.append(f"内置曲目修改版无效：{override}：{exc}")
            entries.append(LibraryEntry(song_id, parse_song(original, song_id), ROOT / "songs.json", index, True, original))
    for path in sorted((base / "library").glob("*.json")):
        if path.stem.startswith("builtin-"):
            continue
        try:
            records = _records(path)
            ids = record_ids.get(path.name)
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                ids = []
            ids = ids[:len(records)]
            while len(ids) < len(records):
                index = len(ids)
                proposed = f"user:{path.stem}" + (f":{index}" if index else "")
                ids.append(proposed if proposed not in ids else f"user:{uuid4().hex}")
            if record_ids.get(path.name) != ids:
                record_ids[path.name] = ids
                ids_changed = True
            for index, raw in enumerate(records):
                song_id = ids[index]
                entries.append(LibraryEntry(song_id, parse_song(raw, song_id), path, index, False, raw))
        except Exception as exc:
            warnings.append(f"曲库文件无效：{path.name}：{exc}")
    if ids_changed:
        _save_state(state)
    return entries, warnings


def materialize_source(entry: LibraryEntry) -> Path:
    """内置曲目首次打开源文件时建立个人 JSON 副本。"""
    if not entry.builtin or entry.source.name != "songs.json":
        return entry.source
    index = int(entry.song_id.split(":", 1)[1])
    target = ensure_data_dir() / "library" / f"builtin-{index}.json"
    if not target.exists():
        _atomic_json(target, entry.raw)
    return target


def save_edited_song(entry: LibraryEntry, raw: dict) -> Path:
    parse_song(raw, "修改后的歌曲")
    path = materialize_source(entry)
    records = _records(path)
    if entry.record_index >= len(records):
        raise ValueError("源文件中的歌曲位置已改变，请刷新曲库后重试")
    records[entry.record_index] = raw
    _atomic_json(path, records if len(records) > 1 else records[0])
    return path


def load_playlists() -> dict[str, list[str]]:
    raw = _read_json(data_dir() / "playlists.json", {})
    if not isinstance(raw, dict):
        return {}
    return {name: list(dict.fromkeys(ids)) for name, ids in raw.items()
            if isinstance(name, str) and name.strip() and isinstance(ids, list)
            and all(isinstance(item, str) for item in ids)}


def save_playlists(playlists: dict[str, list[str]]) -> None:
    _atomic_json(ensure_data_dir() / "playlists.json", playlists)


def add_to_playlist(name: str, song_id: str) -> None:
    name = name.strip()
    if not name or name == "全部曲库":
        raise ValueError("请输入有效的歌单名称")
    playlists = load_playlists()
    members = playlists.setdefault(name, [])
    if song_id not in members:
        members.append(song_id)
    save_playlists(playlists)


def delete_song(entry: LibraryEntry) -> None:
    """内置曲目隐藏；用户曲谱移入回收目录。"""
    base = ensure_data_dir()
    if entry.builtin:
        if entry.source.name != "songs.json" and entry.source.exists():
            _move_to_trash(entry.source)
        state = _state()
        hidden = set(state.get("hidden", []))
        hidden.add(entry.song_id)
        state["hidden"] = sorted(hidden)
        _save_state(state)
    elif entry.source.exists():
        records = _records(entry.source)
        if len(records) == 1:
            _move_to_trash(entry.source)
            state = _state()
            state.setdefault("record_ids", {}).pop(entry.source.name, None)
            _save_state(state)
        else:
            if entry.record_index >= len(records):
                raise ValueError("源文件中的歌曲位置已改变，请刷新曲库后重试")
            # 多曲目 JSON 删除单首时，也保存被删记录以便用户手动恢复。
            removed = records.pop(entry.record_index)
            trash = base / "trash"
            archive = trash / f"{entry.source.stem}-record-{uuid4().hex[:8]}.json"
            _atomic_json(archive, removed)
            _atomic_json(entry.source, records)
            state = _state()
            ids = state.setdefault("record_ids", {}).get(entry.source.name, [])
            if entry.record_index < len(ids):
                ids.pop(entry.record_index)
                _save_state(state)
    playlists = load_playlists()
    for name, members in playlists.items():
        playlists[name] = [song_id for song_id in members if song_id != entry.song_id]
    save_playlists(playlists)


def _move_to_trash(path: Path) -> Path:
    trash = ensure_data_dir() / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / f"{path.stem}-{uuid4().hex[:8]}{path.suffix}"
    shutil.move(str(path), str(target))
    return target
