"""曲谱导入核心规则的回归检查。"""

import tempfile
import unittest
from pathlib import Path

from harmonica import parse_song
from score_import import ImportFailure, make_song, parse_jianpu, parse_musicxml, save_song


class ImportTests(unittest.TestCase):
    def test_jianpu_duration_rest_accidental(self):
        result = parse_jianpu("1=C | 1/1 #2_. 0/0.5 3./1.5 |", (4, 4))
        song = make_song(result, 0, "测试", 96, (4, 4))
        self.assertEqual(song["notes"], [["1", 1.0], ["#2", 0.75], ["-", 0.5], ["3", 2.25]])
        parse_song(song, "test")

    def test_measure_warning_and_saved_json(self):
        result = parse_jianpu("1=C | 1 2 ? | 3 4 5 6 | 7 |", (4, 4))
        song = make_song(result, 0, "测试", 96, (4, 4))
        self.assertTrue(any("第 1 小节" in text for text in song["warnings"]))
        self.assertTrue(any("未识别记号" in text for text in song["warnings"]))
        with tempfile.TemporaryDirectory() as folder:
            path = save_song(song, Path(folder))
            self.assertEqual(load_name(path), "测试")

    def test_musicxml_tie_chord_part_selection(self):
        xml = """<?xml version='1.0'?>
<score-partwise version='4.0'><part-list>
<score-part id='P1'><part-name>主旋律</part-name></score-part>
<score-part id='P2'><part-name>伴奏</part-name></score-part></part-list>
<part id='P1'><measure number='1'><attributes><divisions>4</divisions><key><fifths>0</fifths></key></attributes>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><tie type='start'/></note>
<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><tie type='stop'/></note>
<note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration></note>
<note><chord/><pitch><step>G</step><octave>5</octave></pitch><duration>4</duration></note>
<note><rest/><duration>4</duration></note></measure></part>
<part id='P2'><measure number='1'><attributes><divisions>4</divisions></attributes>
<note><pitch><step>C</step><octave>3</octave></pitch><duration>16</duration></note></measure></part>
</score-partwise>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.musicxml"
            path.write_text(xml, encoding="utf-8")
            result = parse_musicxml(path, (4, 4))
        self.assertEqual(len(result.parts), 2)
        song = make_song(result, 0, "旋律", 100, (4, 4))
        self.assertEqual(song["notes"], [["1", 2.0], ["5", 1.0], ["-", 1.0]])
        self.assertTrue(any("最高音" in warning for warning in song["warnings"]))
        accompaniment = make_song(result, 1, "低音", 100, (4, 4))
        self.assertTrue(any("八度" in warning for warning in accompaniment["warnings"]))


def load_name(path: Path) -> str:
    import json
    return json.loads(path.read_text(encoding="utf-8"))["name"]


if __name__ == "__main__":
    unittest.main()
