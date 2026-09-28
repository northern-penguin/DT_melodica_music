"""便携存储、迁移和歌曲操作的回归检查。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import portable_library as library


SONG = {"name": "测试歌曲", "bpm": 100, "notes": [["1", 1], ["2", 1]]}


class PortableLibraryTests(unittest.TestCase):
    def test_migration_collision_and_unknown_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / "old"
            new = base / "portable" / "data"
            (old / "library").mkdir(parents=True)
            (new / "library").mkdir(parents=True)
            (old / "library" / "song.json").write_text(json.dumps(SONG), encoding="utf-8")
            (new / "library" / "song.json").write_text(json.dumps(dict(SONG, name="已有同名文件")), encoding="utf-8")
            (old / "settings.json").write_text('{"bpm":"80"}', encoding="utf-8")
            (old / "keep.me").write_text("保留", encoding="utf-8")
            result = library.migrate_legacy(old, new)
            self.assertTrue(any("song.json" in line for line in result))
            self.assertFalse((old / "library" / "song.json").exists())
            self.assertTrue((new / "library" / "song-migrated-1.json").exists())
            self.assertTrue((old / "keep.me").exists())

    def test_invalid_migration_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / "old"
            (old / "library").mkdir(parents=True)
            bad = old / "library" / "bad.json"
            bad.write_text("{broken", encoding="utf-8")
            report = library.migrate_legacy(old, base / "new")
            self.assertTrue(bad.exists())
            self.assertTrue(any("失败" in line for line in report))

    def test_builtin_override_playlist_and_trash(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(library, "app_root", return_value=Path(folder)):
            entries, warnings = library.load_library()
            self.assertFalse(warnings)
            builtin = entries[0]
            changed = dict(builtin.raw, name="我的小星星")
            library.save_edited_song(builtin, changed)
            entries, _ = library.load_library()
            self.assertEqual(entries[0].song.name, "我的小星星")
            library.add_to_playlist("练习", entries[0].song_id)
            self.assertEqual(library.load_playlists()["练习"], ["builtin:0"])
            custom = library.data_dir() / "library" / "custom.json"
            custom.write_text(json.dumps(SONG), encoding="utf-8")
            entries, _ = library.load_library()
            user = next(entry for entry in entries if entry.song_id == "user:custom")
            library.delete_song(user)
            self.assertFalse(custom.exists())
            self.assertEqual(len(list((library.data_dir() / "trash").glob("custom-*.json"))), 1)
            library.delete_song(entries[0])
            entries, _ = library.load_library()
            self.assertFalse(any(entry.song_id == "builtin:0" for entry in entries))
            self.assertEqual(library.load_playlists()["练习"], [])

    def test_hotkey_validation(self):
        self.assertEqual(library.validate_hotkeys({})["loop"], "F7")
        with self.assertRaises(ValueError):
            library.validate_hotkeys({"start": "F4", "loop": "F4", "stop": "F8"})

    def test_playlist_ids_follow_array_deletion(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(library, "app_root", return_value=Path(folder)):
            path = library.ensure_data_dir() / "library" / "album.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([dict(SONG, name="甲"), dict(SONG, name="乙")]), encoding="utf-8")
            entries, _ = library.load_library()
            library.add_to_playlist("收藏", "user:album:1")
            library.delete_song(next(entry for entry in entries if entry.song_id == "user:album"))
            self.assertEqual(library.load_playlists()["收藏"], ["user:album:1"])
            entries, _ = library.load_library()
            self.assertEqual(next(entry for entry in entries if entry.song_id == "user:album:1").song.name, "乙")
            archives = list((library.data_dir() / "trash").glob("album-record-*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(json.loads(archives[0].read_text(encoding="utf-8"))["name"], "甲")


if __name__ == "__main__":
    unittest.main()
