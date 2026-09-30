"""核对新增古典乐片段可加载，并保留原有内置曲目的稳定编号。"""

import json
import unittest
from pathlib import Path

from harmonica import load_songs


SONGS_PATH = Path(__file__).resolve().parents[1] / "songs.json"


class BuiltinClassicsTests(unittest.TestCase):
    def test_classical_excerpt_motifs_and_meter(self) -> None:
        # 前四首的索引会被个人副本和隐藏记录引用，新增曲目只能追加。
        records = json.loads(SONGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual([item["name"] for item in records[:3]], ["小星星", "两只老虎", "欢乐颂"])
        self.assertTrue(records[3]["name"].startswith("两难"))
        self.assertEqual(len(load_songs(SONGS_PATH)), 18)

        elise, serenade, prelude = records[4:7]
        self.assertEqual(elise["notes"][:3], [["3+", 0.25], ["#2+", 0.25], ["3+", 0.25]])
        self.assertEqual(sum(beats for _, beats in elise["notes"]), 0.5 + 8 * 1.5)
        self.assertEqual(elise["time_signature"], "3/8")

        self.assertEqual(serenade["notes"][:4], [["5", 1], ["-", 0.5], ["2", 0.5], ["5", 1]])
        self.assertEqual(sum(beats for _, beats in serenade["notes"]), 4 * 4)
        self.assertIn(["#4", 0.5], serenade["notes"])

        self.assertEqual(prelude["notes"][:5], [["1", 0.25], ["3", 0.25], ["5", 0.25], ["1+", 0.25], ["3+", 0.25]])
        self.assertEqual(sum(beats for _, beats in prelude["notes"]), 4 * 4)

    def test_more_classical_excerpts_are_playable_and_documented(self) -> None:
        records = json.loads(SONGS_PATH.read_text(encoding="utf-8"))
        additions = records[7:]
        self.assertEqual(len(additions), 11)
        self.assertEqual(len({song["name"] for song in records}), len(records))

        # 四小天鹅的开头有半拍休止；核对节奏可避免转录时把它漏掉。
        swans = additions[0]
        self.assertTrue(swans["name"].startswith("四小天鹅舞曲"))
        self.assertEqual(swans["notes"][:8],
                         [["-", 0.5], ["6", 0.5], ["6", 0.5], ["6", 0.5],
                          ["6", 0.75], ["#5", 0.25], ["7", 0.5], ["6", 0.5]])

        # 曲目使用整数个小节，元数据保留可核查的原谱链接和改编说明。
        for song in additions:
            with self.subTest(song=song["name"]):
                numerator, denominator = map(int, song["time_signature"].split("/"))
                beats_per_measure = numerator * 4 / denominator
                total_beats = sum(beats for _, beats in song["notes"])
                self.assertAlmostEqual(total_beats / beats_per_measure,
                                       round(total_beats / beats_per_measure))
                self.assertTrue(song["source"].startswith("https://"))
                self.assertTrue(song["warnings"])


if __name__ == "__main__":
    unittest.main()
