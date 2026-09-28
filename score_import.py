"""将简谱、MusicXML 与图片识谱结果转换为口琴曲谱。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class ImportFailure(ValueError):
    """输入无法转换为可演奏曲谱。"""


KEYS = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3,
        "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8,
        "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11}
DEGREES = (0, 2, 4, 5, 7, 9, 11)
NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
FIFTHS = {n: key for n, key in zip(range(-7, 8),
    ("Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"))}
TOKEN_RE = re.compile(r"^(?P<acc>[#b]?)(?P<num>[0-7])(?P<oct>[+-]?)(?P<short>_{0,2})(?P<dot>\.?)(?:/(?P<beats>\d+(?:\.\d+)?))?$")
HEADER_RE = re.compile(r"1\s*=\s*([A-Ga-g][#b]?)")
MUSICXML_SUFFIXES = {".xml", ".musicxml", ".mxl"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".pdf"}


@dataclass
class Note:
    # start/duration 均以四分音符为一拍；pitch=None 为休止。
    pitch: int | None
    start: float
    duration: float
    measure: int
    tie_start: bool = False
    tie_stop: bool = False


@dataclass
class Part:
    name: str
    notes: list[Note]
    warnings: list[str] = field(default_factory=list)
    measure_lengths: dict[int, float] = field(default_factory=dict)


@dataclass
class Result:
    parts: list[Part]
    key: str
    warnings: list[str] = field(default_factory=list)


def user_data_dir() -> Path:
    """曲谱与设置统一放在程序旁的便携 data 目录。"""
    from portable_library import data_dir
    return data_dir()


def normalize_key(value: str) -> str:
    key = value.strip().replace("♯", "#").replace("♭", "b")
    if not key or key[0].upper() not in "ABCDEFG":
        raise ImportFailure(f"无效调号：{value!r}")
    result = key[0].upper() + key[1:].lower()
    if result.upper() not in KEYS:
        raise ImportFailure(f"暂不支持调号：{value!r}")
    return result


def _beat_capacity(meter: tuple[int, int]) -> float:
    numerator, denominator = meter
    if not 1 <= numerator <= 32 or denominator not in (2, 4, 8, 16):
        raise ImportFailure("拍号应为 1–32 / 2、4、8 或 16")
    return numerator * 4 / denominator


def _jianpu_pitch(token: re.Match[str], key: str) -> int | None:
    degree = int(token.group("num"))
    if degree == 0:
        return None
    accidental = 1 if token.group("acc") == "#" else -1 if token.group("acc") == "b" else 0
    octave = 12 if token.group("oct") == "+" else -12 if token.group("oct") == "-" else 0
    return 72 + KEYS[key.upper()] + DEGREES[degree - 1] + accidental + octave


def parse_jianpu(text: str, meter: tuple[int, int], key: str = "C", *,
                 from_ocr: bool = False, force_key: bool = False) -> Result:
    """解析空格分隔简谱；下划线表示八/十六分，斜杠可指定拍数。"""
    capacity = _beat_capacity(meter)
    header = HEADER_RE.search(text)
    detected_key = normalize_key(key) if force_key or not header else normalize_key(header.group(1))
    warnings: list[str] = []
    if not header and key == "C":
        warnings.append("未发现 1= 调号，暂按 C 调")
    if from_ocr:
        warnings.append("图片简谱由 OCR 识别，请核对数字、音区点和时值横线")
    cleaned = HEADER_RE.sub(" ", text)
    if from_ocr and not header and re.search(r"1\s*=", cleaned):
        # OCR 有时漏掉 1=C 的字母；避免把标题中的 1 当成音符。
        cleaned = re.sub(r"1\s*=\s*[A-Za-z]?", " ", cleaned)
        warnings.append("调号标记 1= 识别不完整，暂用窗口填写的调号")
    # 去除简谱以外的歌词；每个无法识别的可疑记号仍记录为疑点。
    token_pattern = r"[#b]?[0-7][+-]?_{0,2}\.?(?:/\d+(?:\.\d+)?)?|\||—|－|-"
    matches = list(re.finditer(token_pattern, cleaned))
    tokens = [match.group() for match in matches]
    position = 0
    for match in matches:
        gap = cleaned[position:match.start()]
        if re.search(r"[0-9#_./+=?^~@*]", gap):
            warnings.append(f"未识别记号：{gap.strip()[:20]}")
        position = match.end()
    tail = cleaned[position:]
    if re.search(r"[0-9#_./+=?^~@*]", tail):
        warnings.append(f"未识别记号：{tail.strip()[:20]}")
    if not tokens:
        raise ImportFailure("未识别到简谱音符")
    notes: list[Note] = []
    cursor = 0.0
    bar_start = 0.0
    measure = 1
    for token in tokens:
        if token == "|":
            used = cursor - bar_start
            if used > 0 and abs(used - capacity) > 0.02:
                warnings.append(f"第 {measure} 小节为 {used:g} 拍，拍号要求 {capacity:g} 拍")
            if used > 0:
                measure += 1
            bar_start = cursor
            continue
        if token in ("—", "－", "-"):
            if notes:
                notes[-1].duration += 1
                cursor += 1
            else:
                warnings.append("开头的延音线没有前一个音")
            continue
        matched = TOKEN_RE.fullmatch(token)
        if matched is None:
            warnings.append(f"未解析记号：{token}")
            continue
        explicit = matched.group("beats")
        duration = float(explicit) if explicit else 1 / (2 ** len(matched.group("short")))
        if matched.group("dot"):
            duration *= 1.5
        if duration <= 0 or duration > 16:
            warnings.append(f"第 {measure} 小节记号 {token} 的时值无效，暂设 1 拍")
            duration = 1.0
        if not explicit and not matched.group("short") and not matched.group("dot"):
            warnings.append(f"第 {measure} 小节 {token} 未写时值，暂按 1 拍")
        notes.append(Note(_jianpu_pitch(matched, detected_key), cursor, duration, measure))
        cursor += duration
    # 乐曲首尾允许弱起或不完整小节，中间小节的差异已记录。
    if len(notes) == 0 or all(note.pitch is None for note in notes):
        raise ImportFailure("曲谱没有可演奏音符")
    return Result([Part("简谱主旋律", notes)], detected_key, list(dict.fromkeys(warnings)))


def _xml_bytes(path: Path) -> bytes:
    if path.suffix.lower() != ".mxl":
        return path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF/")]
        if not names:
            raise ImportFailure("MXL 文件中没有 MusicXML")
        return archive.read(names[0])


def _child_text(element: ET.Element, tag: str, default: str = "") -> str:
    found = element.find(f"{{*}}{tag}")
    return found.text.strip() if found is not None and found.text else default


def parse_musicxml(path: Path, meter: tuple[int, int]) -> Result:
    """读取分部和谱表、时值、休止及连音，供用户选择单旋律。"""
    _beat_capacity(meter)
    try:
        root = ET.fromstring(_xml_bytes(path))
    except (OSError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise ImportFailure(f"MusicXML 读取失败：{exc}") from exc
    part_names = {}
    for item in root.findall(".//{*}score-part"):
        part_names[item.attrib.get("id", "")] = _child_text(item, "part-name", "未命名声部")
    parts: list[Part] = []
    key_name = "C"
    for part in root.findall("./{*}part"):
        part_id = part.attrib.get("id", "")
        part_name = part_names.get(part_id, part_id or "未命名声部")
        divisions = 1
        base = 0.0
        by_voice: dict[tuple[str, str], list[Note]] = {}
        measure_lengths: dict[int, float] = {}
        for measure_index, measure in enumerate(part.findall("./{*}measure"), 1):
            cursor = 0.0
            max_end = 0.0
            last_start = 0.0
            for item in measure:
                tag = item.tag.rsplit("}", 1)[-1]
                if tag == "attributes":
                    new_divisions = _child_text(item, "divisions")
                    if new_divisions:
                        divisions = max(1, int(new_divisions))
                    fifths = item.find("./{*}key/{*}fifths")
                    if fifths is not None and fifths.text:
                        key_name = FIFTHS.get(int(fifths.text), key_name)
                    continue
                if tag in ("backup", "forward"):
                    delta = float(_child_text(item, "duration", "0")) / divisions
                    cursor += -delta if tag == "backup" else delta
                    continue
                if tag != "note":
                    continue
                duration_text = _child_text(item, "duration")
                duration = float(duration_text) / divisions if duration_text else 0.0
                if duration <= 0:
                    # 装饰音不计入主旋律时值。
                    continue
                chord = item.find("{*}chord") is not None
                start = last_start if chord else cursor
                if not chord:
                    last_start = start
                    cursor += duration
                max_end = max(max_end, start + duration)
                pitch_node = item.find("{*}pitch")
                pitch = None
                if pitch_node is not None:
                    step = _child_text(pitch_node, "step", "C").upper()
                    octave = int(_child_text(pitch_node, "octave", "4"))
                    alter = int(float(_child_text(pitch_node, "alter", "0")))
                    pitch = (octave + 1) * 12 + NOTE_NAMES[step] + alter
                ties = {node.attrib.get("type") for node in item.findall("{*}tie")}
                staff = _child_text(item, "staff", "1")
                voice = _child_text(item, "voice", "1")
                by_voice.setdefault((staff, voice), []).append(Note(pitch, base + start, duration, measure_index,
                    "start" in ties, "stop" in ties))
            measure_lengths[measure_index] = max_end
            base += max_end
        for (staff, voice), notes in sorted(by_voice.items()):
            if notes:
                label = f"{part_name} · 谱表 {staff} · 声部 {voice}" if len(by_voice) > 1 else part_name
                parts.append(Part(label, _merge_ties(notes), measure_lengths=measure_lengths))
    if not parts:
        raise ImportFailure("MusicXML 中没有可用的声部")
    return Result(parts, key_name)


def _merge_ties(notes: list[Note]) -> list[Note]:
    ordered = sorted(notes, key=lambda n: (n.start, -1 if n.pitch is None else -n.pitch))
    result: list[Note] = []
    active: dict[int, Note] = {}
    for note in ordered:
        previous = active.get(note.pitch) if note.pitch is not None and note.tie_stop else None
        if previous is not None and abs(previous.start + previous.duration - note.start) < 0.02:
            previous.duration += note.duration
            if not note.tie_start:
                active.pop(note.pitch, None)
        else:
            result.append(note)
            if note.pitch is not None and note.tie_start:
                active[note.pitch] = note
    return result


def find_audiveris() -> Path | None:
    configured = os.environ.get("DTMUSIC_AUDIVERIS")
    candidates = [Path(configured)] if configured else []
    # PyInstaller 单目录包将 Audiveris 和 Java 运行时放在独立工具目录。
    candidates.append(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) /
                      "tools" / "audiveris" / "Audiveris.exe")
    candidates.append(Path(__file__).resolve().parent / "vendor" /
                      "Audiveris-5.11.0-extracted" / "Audiveris" / "Audiveris.exe")
    found = shutil.which("Audiveris.exe") or shutil.which("audiveris")
    if found:
        candidates.append(Path(found))
    candidates.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Audiveris" / "Audiveris.exe")
    return next((p for p in candidates if p.is_file()), None)


def import_staff_image(path: Path, meter: tuple[int, int]) -> Result:
    tool = find_audiveris()
    if tool is None:
        raise ImportFailure("缺少 Audiveris；请使用完整 Windows 程序包，或设置 DTMUSIC_AUDIVERIS")
    from portable_library import ensure_data_dir
    temp_root = ensure_data_dir() / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dtmusic-omr-", dir=temp_root) as folder:
        destination = Path(folder)
        input_path = path
        if path.suffix.lower() != ".pdf":
            # 截图的谱线间距常小于 Audiveris 所需分辨率，先放大再识别。
            from PIL import Image
            with Image.open(path) as image:
                if max(image.size) < 1800:
                    input_path = destination / f"input{path.suffix.lower()}"
                    image.resize((image.width * 3, image.height * 3), Image.Resampling.LANCZOS).save(input_path)
        environment = os.environ.copy()
        # Audiveris 自身的设置和日志也放到便携 data 目录。
        audiveris_data = ensure_data_dir() / "audiveris"
        audiveris_data.mkdir(parents=True, exist_ok=True)
        environment["APPDATA"] = str(audiveris_data)
        environment["LOCALAPPDATA"] = str(audiveris_data)
        environment["TEMP"] = str(temp_root)
        environment["TMP"] = str(temp_root)
        bundled_data = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "tools" / "tesseract" / "tessdata"
        conda_data = Path(sys.prefix) / "Library" / "share" / "tessdata"
        if bundled_data.is_dir():
            environment["TESSDATA_PREFIX"] = str(bundled_data)
        elif conda_data.is_dir():
            environment["TESSDATA_PREFIX"] = str(conda_data)
        command = [str(tool), "-batch", "-transcribe", "-export", "-output", str(destination), "--", str(input_path)]
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=300, env=environment,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ImportFailure(f"Audiveris 识谱失败：{exc}") from exc
        candidates = list(destination.rglob("*.mxl")) + list(destination.rglob("*.xml"))
        if process.returncode != 0 or not candidates:
            raise ImportFailure(f"Audiveris 未生成 MusicXML：{process.stderr[-500:]}")
        result = parse_musicxml(candidates[0], meter)
        result.warnings.append("五线谱由 Audiveris 自动识别，请核对音高、节奏和声部")
        return result


def _tesseract_path() -> str:
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "tools" / "tesseract" / "tesseract.exe"
    if bundled.is_file():
        return str(bundled)
    # Conda 的 tesseract 位于环境的 Library/bin；某些启动器不会把它加入 PATH。
    conda_executable = Path(sys.prefix) / "Library" / "bin" / "tesseract.exe"
    if conda_executable.is_file():
        return str(conda_executable)
    candidate = shutil.which("tesseract")
    if candidate:
        return candidate
    raise ImportFailure("缺少 Tesseract OCR；请安装到 DTmusic 环境或使用完整 Windows 程序包")


def _image_text(image_path: Path) -> str:
    executable = _tesseract_path()
    environment = os.environ.copy()
    from portable_library import ensure_data_dir
    temp_root = ensure_data_dir() / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(temp_root)
    environment["TMP"] = str(temp_root)
    bundled_data = Path(executable).parent / "tessdata"
    if bundled_data.is_dir():
        environment["TESSDATA_PREFIX"] = str(bundled_data)
    else:
        # Conda 将识别模型放在 Library/share/tessdata，而可执行文件位于 Library/bin。
        conda_data = Path(sys.prefix) / "Library" / "share" / "tessdata"
        if conda_data.is_dir():
            environment["TESSDATA_PREFIX"] = str(conda_data)
    command = [executable, str(image_path), "stdout", "--psm", "6", "-c",
               "tessedit_char_whitelist=01234567#bCDEFGAB|.=+-_/ "]
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=60, env=environment,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImportFailure(f"Tesseract OCR 失败：{exc}") from exc
    if process.returncode != 0:
        raise ImportFailure(f"Tesseract OCR 失败：{process.stderr[-400:]}")
    return process.stdout


def import_jianpu_image(path: Path, meter: tuple[int, int], key: str, force_key: bool = False) -> Result:
    if path.suffix.lower() != ".pdf":
        return parse_jianpu(_image_text(path), meter, key, from_ocr=True, force_key=force_key)
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImportFailure("PDF 简谱需要 pypdfium2") from exc
    chunks = []
    from portable_library import ensure_data_dir
    temp_root = ensure_data_dir() / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dtmusic-pdf-", dir=temp_root) as folder:
        document = pdfium.PdfDocument(str(path))
        try:
            for page_number in range(len(document)):
                page = document[page_number]
                try:
                    bitmap = page.render(scale=3)
                    image = bitmap.to_pil()
                    image_path = Path(folder) / f"page-{page_number + 1}.png"
                    image.save(image_path)
                    image.close()
                    bitmap.close()
                    chunks.append(_image_text(image_path))
                finally:
                    page.close()
        finally:
            document.close()
    return parse_jianpu("\n".join(chunks), meter, key, from_ocr=True, force_key=force_key)


def import_file(path: Path, notation: str, meter: tuple[int, int], key: str = "C",
                force_key: bool = False) -> Result:
    """按格式调用独立解析器，所有文件仅在本机读取。"""
    if not path.is_file():
        raise ImportFailure(f"文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in MUSICXML_SUFFIXES:
        return parse_musicxml(path, meter)
    if suffix == ".txt":
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            content = path.read_text(encoding="gb18030")
        return parse_jianpu(content, meter, key, force_key=force_key)
    if suffix in IMAGE_SUFFIXES:
        if notation == "五线谱":
            return import_staff_image(path, meter)
        if notation == "简谱":
            return import_jianpu_image(path, meter, key, force_key=force_key)
    raise ImportFailure("支持 TXT、MusicXML/XML/MXL、PNG/JPG/BMP/TIFF/PDF 曲谱")


def _pitch_symbols() -> dict[int, str]:
    symbols: dict[int, str] = {}
    for shift, suffix in ((0, ""), (-1, "-"), (1, "+")):
        for degree, semitone in enumerate(DEGREES, 1):
            for sharp in (0, 1):
                pitch = 72 + 12 * shift + semitone + sharp
                symbol = f"{'#' if sharp else ''}{degree}{suffix}"
                if pitch not in symbols or (sharp == 0 and shift == 0):
                    symbols[pitch] = symbol
    symbols[84] = "1+"  # 高音 do 使用逗号键。
    return symbols


PITCH_SYMBOLS = _pitch_symbols()


def make_song(result: Result, part_index: int, name: str, bpm: float,
              meter: tuple[int, int], key_override: str | None = None) -> dict:
    """选单旋律、全曲移八度、生成现有播放器可直接读取的 JSON。"""
    capacity = _beat_capacity(meter)
    if not 20 <= bpm <= 300:
        raise ImportFailure("拍速应在 20–300 BPM")
    if not 0 <= part_index < len(result.parts):
        raise ImportFailure("声部编号无效")
    if not name.strip():
        raise ImportFailure("曲目名称不能为空")
    part = result.parts[part_index]
    warnings = list(dict.fromkeys(result.warnings + part.warnings))
    pitched = [note.pitch for note in part.notes if note.pitch is not None]
    if not pitched:
        raise ImportFailure("所选声部没有可演奏音符")
    shifts = [shift for shift in range(-4, 5) if all(p + 12 * shift in PITCH_SYMBOLS for p in pitched)]
    if not shifts:
        raise ImportFailure("旋律跨度超出口琴音域，无法整曲移八度后完整演奏")
    shift = min(shifts, key=lambda n: (abs(n), n))
    if shift:
        warnings.append(f"整段旋律已移动 {shift:+d} 个八度以适配口琴")
    ordered = sorted(part.notes, key=lambda n: (n.start, n.pitch is None, -(n.pitch or 0)))
    selected: list[Note] = []
    for note in ordered:
        if selected and abs(note.start - selected[-1].start) < 0.001:
            if note.pitch is not None and (selected[-1].pitch is None or note.pitch > selected[-1].pitch):
                selected[-1] = note
            warnings.append(f"第 {note.measure} 小节同拍多音，取最高音")
        else:
            selected.append(note)
    events: list[list] = []
    cursor = 0.0
    for index, note in enumerate(selected):
        if note.start > cursor + 0.001:
            events.append(["-", round(note.start - cursor, 4)])
        elif note.start < cursor - 0.001:
            warnings.append(f"第 {note.measure} 小节存在重叠音，前一个音已截短")
        next_start = selected[index + 1].start if index + 1 < len(selected) else float("inf")
        duration = min(note.duration, next_start - note.start)
        if duration <= 0:
            continue
        symbol = "-" if note.pitch is None else PITCH_SYMBOLS[note.pitch + 12 * shift]
        events.append([symbol, round(duration, 4)])
        cursor = max(cursor, note.start + duration)
    if not events or all(symbol == "-" for symbol, _ in events):
        raise ImportFailure("转换后没有可演奏音符")
    # 首尾可能是弱起或不完整小节；MusicXML 的中间小节按原始时值核验。
    for number in sorted(part.measure_lengths)[1:-1]:
        if abs(part.measure_lengths[number] - capacity) > 0.05:
            warnings.append(f"第 {number} 小节时值可能与 {meter[0]}/{meter[1]} 不符")
    return {"name": name.strip(), "bpm": bpm, "notes": events,
            "time_signature": f"{meter[0]}/{meter[1]}",
            "key": normalize_key(key_override or result.key), "warnings": list(dict.fromkeys(warnings))}


def save_song(song: dict, directory: Path | None = None) -> Path:
    """独立文件原子写入，避免覆盖用户已有曲目。"""
    from harmonica import parse_song

    parse_song(song, "新曲谱")
    if directory is None:
        from portable_library import ensure_data_dir
        target_dir = ensure_data_dir() / "library"
    else:
        target_dir = directory
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^\w-]+", "-", song["name"], flags=re.UNICODE).strip("-")[:40] or "song"
    target = target_dir / f"{slug}-{stamp}-{uuid4().hex[:6]}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(song, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target
