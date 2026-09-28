"""桌面曲库搜索、菜单和表格编辑的本地窗口检查。"""

import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import portable_library
from dtmusic_gui import App, SongEditor
from harmonica import load_songs


class GuiLibraryTests(unittest.TestCase):
    def test_search_context_and_human_editor(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(portable_library, "app_root", return_value=Path(folder)):
            try:
                app = App()
            except tk.TclError as exc:
                self.skipTest(f"当前会话无法创建 Tk 窗口：{exc}")
            try:
                app.withdraw()
                app.update()
                self.assertGreaterEqual(len(app.entries), 4)
                app.search_var.set("小星星")
                app.update()
                self.assertEqual(len(app.visible_entries), 1)
                self.assertTrue(app.listbox.bind("<Double-Button-1>"))
                self.assertEqual([app.context_menu.entrycget(i, "label") for i in range(4)],
                                 ["删除", "修改", "打开源文件", "加入歌单"])
                entry = app.visible_entries[0]
                editor = SongEditor(app, entry, app.refresh_library)
                editor.update()
                editor.name_var.set("改名小星星")
                editor.beats_var.set("0.5")
                editor._save()
                app.update()
                path = Path(folder) / "data" / "library" / "builtin-0.json"
                self.assertEqual(load_songs(path)[0].name, "改名小星星")
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
