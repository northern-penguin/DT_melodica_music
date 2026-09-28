"""DTmusic 桌面曲谱导入与演奏窗口。"""

from __future__ import annotations

import logging
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from harmonica import NOTE_PATTERN, ROOT, WindowsOutput, load_songs, parse_event, parse_song
from playback_control import run_session
from portable_library import (LibraryEntry, add_to_playlist, data_dir, delete_song,
                              ensure_data_dir, load_library, load_playlists, load_settings,
                              materialize_source, migrate_legacy, save_edited_song,
                              save_settings, validate_hotkeys)
from score_import import (ImportFailure, find_audiveris, import_file,
                          make_song, save_song, user_data_dir)


MODE_LABELS = {"single": "单曲循环", "sequence": "顺序循环", "random": "随机循环"}
LABEL_MODES = {value: key for key, value in MODE_LABELS.items()}


def configure_log() -> None:
    # 日志与曲库放在程序旁的 data 目录。
    directory = ensure_data_dir()
    logging.basicConfig(filename=directory / "dtmusic.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DTmusic · 曲谱导入与口琴演奏")
        self.geometry("860x680")
        self.minsize(760, 600)
        self.file_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.notation_var = tk.StringVar(value="简谱")
        self.meter_var = tk.StringVar(value="4/4")
        self.bpm_var = tk.StringVar(value="96")
        self.key_var = tk.StringVar(value="C")
        self.force_key_var = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.playlist_var = tk.StringVar(value="全部曲库")
        self.loop_mode_var = tk.StringVar(value="单曲循环")
        self.hotkeys = {"start": "F6", "loop": "F7", "stop": "F8"}
        self._load_settings()
        self.status_var = tk.StringVar(value="选择曲谱后导入；双击曲名准备演奏。")
        self.entries: list[LibraryEntry] = []
        self.visible_entries: list[LibraryEntry] = []
        self.stop_event = threading.Event()
        self.play_thread: threading.Thread | None = None
        self.import_busy = False
        self._draw()
        self.refresh_library()
        self.search_var.trace_add("write", lambda *_: self._filter_library())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _load_settings(self) -> None:
        try:
            settings = load_settings()
            for name, variable in (("notation", self.notation_var), ("meter", self.meter_var),
                                   ("bpm", self.bpm_var), ("key", self.key_var)):
                if isinstance(settings.get(name), str):
                    variable.set(settings[name])
            self.hotkeys = settings["hotkeys"]
            self.loop_mode_var.set(MODE_LABELS[settings["loop_mode"]])
            if settings.get("active_playlist"):
                self.playlist_var.set(settings["active_playlist"])
        except (OSError, ValueError) as exc:
            messagebox.showwarning("设置文件", str(exc))

    def _save_settings(self) -> None:
        data = {"notation": self.notation_var.get(), "meter": self.meter_var.get(),
                "bpm": self.bpm_var.get(), "key": self.key_var.get(),
                "hotkeys": self.hotkeys, "loop_mode": LABEL_MODES[self.loop_mode_var.get()],
                "active_playlist": None if self.playlist_var.get() == "全部曲库" else self.playlist_var.get()}
        save_settings(data)

    def _draw(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="导入曲谱", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(8, 5))
        ttk.Label(row, text="文件").pack(side="left")
        ttk.Entry(row, textvariable=self.file_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="选择…", command=self._browse).pack(side="left")

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=5)
        for label, variable, width in (("类型", self.notation_var, 9), ("拍号", self.meter_var, 7),
                                       ("BPM", self.bpm_var, 7), ("调号", self.key_var, 7),
                                       ("曲名", self.name_var, 18)):
            ttk.Label(options, text=label).pack(side="left", padx=(8, 3))
            if label == "类型":
                ttk.Combobox(options, textvariable=variable, values=("简谱", "五线谱"),
                             state="readonly", width=width).pack(side="left")
            else:
                ttk.Entry(options, textvariable=variable, width=width).pack(side="left")
        self.import_button = ttk.Button(outer, text="识别并加入曲库", command=self._import)
        self.import_button.pack(anchor="w", pady=8)
        ttk.Checkbutton(outer, text="以输入调号为准（覆盖谱面调号）",
                        variable=self.force_key_var).pack(anchor="w")
        ttk.Label(outer, text="支持印刷 PNG/JPG/PDF、TXT 简谱及 MusicXML/XML/MXL；五线谱图片由内置 Audiveris 识别。",
                  foreground="#555555").pack(anchor="w")

        ttk.Separator(outer).pack(fill="x", pady=13)
        ttk.Label(outer, text="曲库", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(5, 2))
        ttk.Label(controls, text="搜索").pack(side="left")
        ttk.Entry(controls, textvariable=self.search_var, width=23).pack(side="left", padx=(4, 10))
        ttk.Label(controls, text="歌单").pack(side="left")
        self.playlist_box = ttk.Combobox(controls, textvariable=self.playlist_var, state="readonly", width=18)
        self.playlist_box.pack(side="left", padx=(4, 10))
        self.playlist_box.bind("<<ComboboxSelected>>", self._playlist_changed)
        ttk.Label(controls, text="循环模式").pack(side="left")
        self.mode_box = ttk.Combobox(controls, textvariable=self.loop_mode_var, state="readonly", width=11,
                                     values=tuple(MODE_LABELS.values()))
        self.mode_box.pack(side="left", padx=4)
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        library = ttk.Frame(outer)
        library.pack(fill="both", expand=True, pady=7)
        self.listbox = tk.Listbox(library, height=10, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(library, command=self.listbox.yview)
        scroll.pack(side="left", fill="y")
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.bind("<Double-Button-1>", lambda _event: self._arm())
        self.listbox.bind("<Button-3>", self._show_context_menu)
        buttons = ttk.Frame(library)
        buttons.pack(side="left", fill="y", padx=10)
        ttk.Button(buttons, text="刷新曲库", command=self.refresh_library).pack(fill="x", pady=2)
        ttk.Button(buttons, text="准备演奏", command=self._arm).pack(fill="x", pady=2)
        ttk.Button(buttons, text="停止", command=self._stop).pack(fill="x", pady=2)
        ttk.Button(buttons, text="热键设置", command=self._hotkey_dialog).pack(fill="x", pady=2)
        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="删除", command=self._delete_selected)
        self.context_menu.add_command(label="修改", command=self._edit_selected)
        self.context_menu.add_command(label="打开源文件", command=self._open_selected)
        self.context_menu.add_command(label="加入歌单", command=self._add_selected_to_playlist)

        ttk.Label(outer, text="导入提示 / 疑点").pack(anchor="w")
        self.details = tk.Text(outer, height=8, wrap="word", state="disabled")
        self.details.pack(fill="both", expand=True, pady=(3, 7))
        ttk.Label(outer, textvariable=self.status_var, foreground="#176a9b", wraplength=800).pack(anchor="w")
        if find_audiveris() is None:
            self._details("未找到 Audiveris：五线谱图片暂不可识别；TXT、简谱图片和 MusicXML 可使用。\n")

    def _details(self, message: str) -> None:
        self.details.configure(state="normal")
        self.details.insert("end", message)
        self.details.see("end")
        self.details.configure(state="disabled")

    def _browse(self) -> None:
        name = filedialog.askopenfilename(filetypes=[("曲谱", "*.txt *.xml *.musicxml *.mxl *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.pdf"),
                                                  ("所有文件", "*.*")])
        if name:
            self.file_var.set(name)
            if not self.name_var.get().strip():
                self.name_var.set(Path(name).stem)

    def _settings(self) -> tuple[tuple[int, int], float]:
        try:
            numerator, denominator = (int(piece) for piece in self.meter_var.get().split("/"))
            bpm = float(self.bpm_var.get())
        except ValueError as exc:
            raise ImportFailure("拍号请写成 4/4，拍速请输入数字") from exc
        return (numerator, denominator), bpm

    def _import(self) -> None:
        if self.import_busy:
            return
        path = Path(self.file_var.get().strip())
        try:
            meter, bpm = self._settings()
            if not path.is_file():
                raise ImportFailure("请先选择存在的曲谱文件")
            from score_import import normalize_key
            key = normalize_key(self.key_var.get())
        except (ImportFailure, OSError) as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.import_busy = True
        self.import_button.configure(state="disabled")
        self.status_var.set("正在识别曲谱…")
        notation = self.notation_var.get()
        force_key = self.force_key_var.get()

        def worker() -> None:
            try:
                result = import_file(path, notation, meter, key, force_key)
                self.after(0, lambda: self._finish_import(result, meter, bpm))
            except Exception as exc:
                logging.exception("导入失败：%s", path)
                self.after(0, lambda error=str(exc): self._import_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _import_error(self, error: str) -> None:
        self.import_busy = False
        self.import_button.configure(state="normal")
        self.status_var.set("导入失败")
        self._details(f"错误：{error}\n")
        messagebox.showerror("导入失败", error)

    def _finish_import(self, result, meter: tuple[int, int], bpm: float) -> None:
        try:
            index = 0
            if len(result.parts) > 1:
                index = self._select_part(result.parts)
                if index is None:
                    self.status_var.set("已取消声部选择")
                    return
            song = make_song(result, index, self.name_var.get(), bpm, meter,
                             self.key_var.get() if self.force_key_var.get() else None)
            destination = save_song(song)
            self.key_var.set(song["key"])
            self._details(f"已保存：{destination}\n")
            for warning in song["warnings"]:
                self._details(f"疑点：{warning}\n")
            self.status_var.set(f"已加入曲库：《{song['name']}》；请核对疑点后演奏。")
            self.playlist_var.set("全部曲库")
            self.search_var.set("")
            self.refresh_library()
            for position, entry in enumerate(self.visible_entries):
                if entry.source == destination:
                    self.listbox.selection_clear(0, "end")
                    self.listbox.selection_set(position)
                    self.listbox.see(position)
                    break
        except Exception as exc:
            logging.exception("转换曲谱失败")
            self._import_error(str(exc))
            return
        finally:
            self.import_busy = False
            self.import_button.configure(state="normal")

    def _select_part(self, parts) -> int | None:
        # 多声部必须由用户指定；不自动猜测哪条是主旋律。
        dialog = tk.Toplevel(self)
        dialog.title("选择演奏声部")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="选择一条旋律声部；同一声部的和弦将取最高音。", padding=12).pack()
        choice = tk.Listbox(dialog, width=55, height=min(12, len(parts)))
        choice.pack(padx=12, pady=5)
        for part in parts:
            choice.insert("end", f"{part.name}（{len(part.notes)} 个音符）")
        choice.selection_set(0)
        chosen: list[int | None] = [None]

        def accept() -> None:
            selection = choice.curselection()
            if selection:
                chosen[0] = selection[0]
            dialog.destroy()

        ttk.Button(dialog, text="使用选中声部", command=accept).pack(pady=10)
        dialog.wait_window()
        return chosen[0]

    def refresh_library(self) -> None:
        selected = self._selected_entry()
        selected_id = selected.song_id if selected else None
        try:
            self.entries, warnings = load_library()
            for warning in warnings:
                self._details(warning + "\n")
        except Exception as exc:
            self._details(f"曲库读取失败：{exc}\n")
            self.entries = []
        names = ["全部曲库", *load_playlists()]
        self.playlist_box.configure(values=names)
        if self.playlist_var.get() not in names:
            self.playlist_var.set("全部曲库")
        self._filter_library(selected_id)

    def _filter_library(self, selected_id: str | None = None) -> None:
        if selected_id is None:
            selected = self._selected_entry()
            selected_id = selected.song_id if selected else None
        playlist = self.playlist_var.get()
        members = load_playlists().get(playlist, []) if playlist != "全部曲库" else None
        scoped = self.entries if members is None else [entry for song_id in members
                                                         for entry in self.entries if entry.song_id == song_id]
        term = self.search_var.get().strip().casefold()
        self.visible_entries = [entry for entry in scoped if term in entry.song.name.casefold()]
        self.listbox.delete(0, "end")
        for entry in self.visible_entries:
            song = entry.song
            self.listbox.insert("end", f"{song.name}  ·  {song.bpm:g} BPM  ·  {len(song.events)} 个事件")
        if self.visible_entries:
            index = next((i for i, entry in enumerate(self.visible_entries) if entry.song_id == selected_id), 0)
            self.listbox.selection_set(index)

    def _selected_entry(self) -> LibraryEntry | None:
        selected = self.listbox.curselection() if hasattr(self, "listbox") else ()
        return self.visible_entries[selected[0]] if selected and selected[0] < len(self.visible_entries) else None

    def _playlist_changed(self, _event=None) -> None:
        self._filter_library()
        try:
            self._save_settings()
        except OSError as exc:
            messagebox.showerror("保存设置失败", str(exc))

    def _show_context_menu(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if not 0 <= index < len(self.visible_entries):
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if not messagebox.askyesno("删除曲目", f"从曲库删除《{entry.song.name}》？\n用户曲谱将移到 data/trash，可手动恢复。"):
            return
        try:
            self._stop()
            if self.play_thread and self.play_thread.is_alive():
                self.play_thread.join(timeout=2)
            delete_song(entry)
            self.refresh_library()
            self.status_var.set(f"已从曲库删除《{entry.song.name}》")
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc))

    def _edit_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            SongEditor(self, entry, lambda: self.refresh_library())

    def _open_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        try:
            path = materialize_source(entry)
            os.startfile(path)
            self.refresh_library()
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def _add_selected_to_playlist(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("加入歌单")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text=f"将《{entry.song.name}》加入歌单：", padding=12).pack(anchor="w")
        name = tk.StringVar()
        box = ttk.Combobox(dialog, textvariable=name, values=tuple(load_playlists()), width=28)
        box.pack(padx=12, pady=5)
        box.focus_set()

        def accept() -> None:
            try:
                add_to_playlist(name.get(), entry.song_id)
                self.playlist_var.set(name.get().strip())
                self.refresh_library()
                self._save_settings()
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror("歌单错误", str(exc), parent=dialog)

        ttk.Button(dialog, text="加入（可输入新歌单名）", command=accept).pack(pady=10)

    def _hotkey_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("热键设置")
        dialog.transient(self)
        dialog.grab_set()
        variables = {name: tk.StringVar(value=self.hotkeys[name]) for name in ("start", "loop", "stop")}
        for row, (name, label) in enumerate((("start", "开始"), ("loop", "循环"), ("stop", "停止"))):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=12, pady=8)
            ttk.Combobox(dialog, textvariable=variables[name], values=[f"F{i}" for i in range(1, 13)],
                         state="readonly", width=9).grid(row=row, column=1, padx=12, pady=8)

        def accept() -> None:
            try:
                self.hotkeys = validate_hotkeys({name: var.get() for name, var in variables.items()})
                self._save_settings()
                dialog.destroy()
                self.status_var.set("热键已保存；下次准备演奏时生效")
            except Exception as exc:
                messagebox.showerror("热键错误", str(exc), parent=dialog)

        ttk.Button(dialog, text="保存", command=accept).grid(row=3, column=0, columnspan=2, pady=12)

    def _arm(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            messagebox.showinfo("选曲", "请先在曲库中选择歌曲")
            return
        if self.play_thread and self.play_thread.is_alive():
            self._stop()
            self.play_thread.join(timeout=1)
            if self.play_thread.is_alive():
                messagebox.showwarning("演奏尚未停止", "请稍后再次准备演奏")
                return
        playlist = self.playlist_var.get()
        members = load_playlists().get(playlist) if playlist != "全部曲库" else None
        queue = ([(item.song_id, item.song) for song_id in members for item in self.entries if item.song_id == song_id]
                 if members is not None else [(item.song_id, item.song) for item in self.entries])
        if entry.song_id not in dict(queue):
            messagebox.showerror("播放队列", "所选曲目不在当前歌单中")
            return
        self.stop_event.clear()
        hotkeys = dict(self.hotkeys)
        mode = LABEL_MODES[self.loop_mode_var.get()]
        self.status_var.set(f"已准备《{entry.song.name}》；切换到游戏，按 {hotkeys['start']} 单次播放、{hotkeys['loop']} 循环、{hotkeys['stop']} 停止。")
        self.play_thread = threading.Thread(target=self._play_worker,
                                            args=(entry.song_id, queue, mode, hotkeys), daemon=True)
        self.play_thread.start()

    def _play_worker(self, song_id, queue, mode, hotkeys) -> None:
        try:
            output = WindowsOutput()
            update = lambda message: self.after(0, lambda: self.status_var.set(message))
            run_session(song_id, queue, mode, hotkeys, output, self.stop_event,
                        target_allowed=lambda: output.foreground_process_id() != os.getpid(),
                        on_status=update,
                        on_track=lambda song: update(f"正在演奏《{song.name}》…"))
        except Exception as exc:
            logging.info("演奏结束：%s", exc)
            self.after(0, lambda message=str(exc): self.status_var.set(f"演奏停止：{message}"))

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("正在停止并释放按键…")

    def _close(self) -> None:
        self._stop()
        if self.play_thread and self.play_thread.is_alive():
            self.play_thread.join(timeout=1)
        try:
            self._save_settings()
        except OSError:
            logging.exception("保存设置失败")
        self.destroy()


class SongEditor(tk.Toplevel):
    """通过音符表格修改 JSON，保留识谱疑点等附加字段。"""

    DEGREES = {"do": "1", "re": "2", "mi": "3", "fa": "4",
               "so": "5", "la": "6", "xi": "7"}

    def __init__(self, parent: App, entry: LibraryEntry, on_saved) -> None:
        super().__init__(parent)
        self.title(f"修改曲目 · {entry.song.name}")
        self.geometry("680x610")
        self.transient(parent)
        self.grab_set()
        self.entry = entry
        self.on_saved = on_saved
        self.raw = json.loads(json.dumps(entry.raw, ensure_ascii=False))
        self.notes = [list(item) for item in self.raw["notes"]]
        self.name_var = tk.StringVar(value=self.raw["name"])
        self.bpm_var = tk.StringVar(value=str(self.raw["bpm"]))
        self.meter_var = tk.StringVar(value=self.raw.get("time_signature", "4/4"))
        self.key_var = tk.StringVar(value=self.raw.get("key", "C"))
        self.degree_var = tk.StringVar(value="do")
        self.octave_var = tk.StringVar(value="中")
        self.sharp_var = tk.BooleanVar()
        self.beats_var = tk.StringVar(value="1")
        self._draw()
        self._refresh_rows(0)

    def _draw(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        for label, variable, width in (("曲名", self.name_var, 24), ("BPM", self.bpm_var, 7),
                                       ("拍号", self.meter_var, 7), ("调号", self.key_var, 7)):
            ttk.Label(header, text=label).pack(side="left", padx=(6, 3))
            ttk.Entry(header, textvariable=variable, width=width).pack(side="left")
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True, pady=12)
        self.tree = ttk.Treeview(table, columns=("no", "degree", "octave", "sharp", "beats"), show="headings")
        for field, title, width in (("no", "序号", 55), ("degree", "音符", 140),
                                    ("octave", "音区", 85), ("sharp", "升半音", 95),
                                    ("beats", "拍数", 100)):
            self.tree.heading(field, text=title)
            self.tree.column(field, width=width, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._load_selection)
        scrollbar = ttk.Scrollbar(table, command=self.tree.yview)
        scrollbar.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        editor = ttk.Frame(outer)
        editor.pack(fill="x")
        ttk.Label(editor, text="音符").pack(side="left")
        ttk.Combobox(editor, textvariable=self.degree_var, values=("休止", *self.DEGREES),
                     state="readonly", width=8).pack(side="left", padx=5)
        ttk.Label(editor, text="音区").pack(side="left")
        ttk.Combobox(editor, textvariable=self.octave_var, values=("低", "中", "高"),
                     state="readonly", width=6).pack(side="left", padx=5)
        ttk.Checkbutton(editor, text="升半音", variable=self.sharp_var).pack(side="left", padx=5)
        ttk.Label(editor, text="拍数").pack(side="left")
        ttk.Entry(editor, textvariable=self.beats_var, width=7).pack(side="left", padx=5)
        commands = ttk.Frame(outer)
        commands.pack(fill="x", pady=9)
        for title, action in (("更新选中", self._apply), ("添加音符", self._add),
                              ("删除音符", self._remove), ("上移", lambda: self._move(-1)),
                              ("下移", lambda: self._move(1))):
            ttk.Button(commands, text=title, command=action).pack(side="left", padx=3)
        ttk.Label(outer, text="休止符不使用音区和升半音；高音 do 对应逗号键。",
                  foreground="#555555").pack(anchor="w")
        ttk.Button(outer, text="校验并保存", command=self._save).pack(anchor="e", pady=12)

    def _describe(self, item: list) -> tuple[str, str, str]:
        symbol = item[0]
        if symbol == "-":
            return "休止", "—", "—"
        match = NOTE_PATTERN.fullmatch(symbol)
        if not match:
            return symbol, "?", "?"
        sharp, degree, octave = match.groups()
        label = next(name for name, number in self.DEGREES.items() if number == degree)
        return label, {"-": "低", "": "中", "+": "高"}[octave], "是" if sharp else "否"

    def _refresh_rows(self, select: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.notes):
            degree, octave, sharp = self._describe(item)
            self.tree.insert("", "end", iid=str(index), values=(index + 1, degree, octave, sharp, item[1]))
        if select is not None and 0 <= select < len(self.notes):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def _index(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _load_selection(self, _event=None) -> None:
        index = self._index()
        if index is None:
            return
        symbol, beats = self.notes[index]
        if symbol == "-":
            self.degree_var.set("休止")
            self.octave_var.set("中")
            self.sharp_var.set(False)
        else:
            match = NOTE_PATTERN.fullmatch(symbol)
            if match is None:
                return
            sharp, degree, octave = match.groups()
            self.degree_var.set(next(name for name, value in self.DEGREES.items() if value == degree))
            self.octave_var.set({"-": "低", "": "中", "+": "高"}[octave])
            self.sharp_var.set(bool(sharp))
        self.beats_var.set(str(beats))

    def _form_note(self) -> list:
        try:
            beats = float(self.beats_var.get())
        except ValueError as exc:
            raise ValueError("拍数请输入数字") from exc
        if self.degree_var.get() == "休止":
            symbol = "-"
        else:
            degree = self.DEGREES[self.degree_var.get()]
            octave = {"低": "-", "中": "", "高": "+"}[self.octave_var.get()]
            symbol = f"{'#' if self.sharp_var.get() else ''}{degree}{octave}"
        parse_event([symbol, beats], "当前音符")
        return [symbol, beats]

    def _apply(self) -> None:
        index = self._index()
        if index is None:
            messagebox.showinfo("修改音符", "请先选中一个音符", parent=self)
            return
        try:
            self.notes[index] = self._form_note()
            self._refresh_rows(index)
        except Exception as exc:
            messagebox.showerror("音符无效", str(exc), parent=self)

    def _add(self) -> None:
        try:
            index = self._index()
            insert_at = len(self.notes) if index is None else index + 1
            self.notes.insert(insert_at, self._form_note())
            self._refresh_rows(insert_at)
        except Exception as exc:
            messagebox.showerror("音符无效", str(exc), parent=self)

    def _remove(self) -> None:
        index = self._index()
        if index is not None:
            self.notes.pop(index)
            self._refresh_rows(min(index, len(self.notes) - 1))

    def _move(self, offset: int) -> None:
        index = self._index()
        if index is not None and 0 <= index + offset < len(self.notes):
            self.notes[index], self.notes[index + offset] = self.notes[index + offset], self.notes[index]
            self._refresh_rows(index + offset)

    def _save(self) -> None:
        try:
            index = self._index()
            if index is not None:
                self.notes[index] = self._form_note()
            from score_import import _beat_capacity, normalize_key
            numerator, denominator = (int(part) for part in self.meter_var.get().split("/"))
            _beat_capacity((numerator, denominator))
            self.raw.update(name=self.name_var.get().strip(), bpm=float(self.bpm_var.get()),
                            time_signature=f"{numerator}/{denominator}",
                            key=normalize_key(self.key_var.get()), notes=self.notes)
            parse_song(self.raw, "编辑曲谱")
            save_edited_song(self.entry, self.raw)
            self.on_saved()
            self.destroy()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)


def main() -> None:
    ensure_data_dir()
    migration_report = migrate_legacy()
    configure_log()
    if "--self-test" in sys.argv:
        # 打包验证入口：无需打开窗口即可检查独立包的运行时组件。
        report = {"builtin_songs": False, "tesseract": False, "pdf": False, "tk": False,
                  "audiveris": False}
        try:
            temp_root = ensure_data_dir() / "tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            audiveris = find_audiveris()
            if audiveris:
                audiveris_data = ensure_data_dir() / "audiveris"
                audiveris_data.mkdir(parents=True, exist_ok=True)
                environment = os.environ.copy()
                environment["APPDATA"] = str(audiveris_data)
                environment["LOCALAPPDATA"] = str(audiveris_data)
                environment["TEMP"] = str(temp_root)
                environment["TMP"] = str(temp_root)
                checked = subprocess.run([str(audiveris), "-version"], capture_output=True,
                                         text=True, timeout=60, env=environment,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                report["audiveris"] = checked.returncode == 0 and "5.11.0" in checked.stdout
            window = tk.Tk()
            window.withdraw()
            window.update()
            window.destroy()
            report["tk"] = True
            report["builtin_songs"] = bool(load_songs(ROOT / "songs.json"))
            from PIL import Image, ImageDraw, ImageFont
            from score_import import _tesseract_path
            report["tesseract"] = Path(_tesseract_path()).is_file()
            with tempfile.TemporaryDirectory(prefix="dtmusic-smoke-", dir=temp_root) as folder:
                image = Image.new("RGB", (1500, 300), "white")
                font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 64)
                ImageDraw.Draw(image).text((50, 80), "1=C  |  1  2  3  4  |", fill="black", font=font)
                pdf = Path(folder) / "sample.pdf"
                image.save(pdf, "PDF", resolution=150)
                result = import_file(pdf, "简谱", (4, 4))
                report["pdf"] = bool(make_song(result, 0, "自检", 96, (4, 4))["notes"])
        except Exception as exc:
            report["error"] = str(exc)
            logging.exception("独立包自检失败")
        (user_data_dir() / "selftest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    app = App()
    for line in migration_report:
        app._details(line + "\n")
    app.mainloop()


if __name__ == "__main__":
    main()
