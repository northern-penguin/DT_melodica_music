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
        self.assertEqual(len(load_songs(SONGS_PATH)), 7)

        elise, serenade, prelude = records[4:]
        self.assertEqual(elise["notes"][:3], [["3+", 0.25], ["#2+", 0.25], ["3+", 0.25]])
        self.assertEqual(sum(beats for _, beats in elise["notes"]), 0.5 + 8 * 1.5)
        self.assertEqual(elise["time_signature"], "3/8")

        self.assertEqual(serenade["notes"][:4], [["5", 1], ["-", 0.5], ["2", 0.5], ["5", 1]])
        self.assertEqual(sum(beats for _, beats in serenade["notes"]), 4 * 4)
        self.assertIn(["#4", 0.5], serenade["notes"])

        self.assertEqual(prelude["notes"][:5], [["1", 0.25], ["3", 0.25], ["5", 0.25], ["1+", 0.25], ["3+", 0.25]])
        self.assertEqual(sum(beats for _, beats in prelude["notes"]), 4 * 4)


if __name__ == "__main__":
    unittest.main()
