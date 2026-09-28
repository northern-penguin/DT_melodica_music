"""使用生成的印刷简谱检查图片与 PDF 导入链路。"""

import tempfile
import unittest
from pathlib import Path

from score_import import ImportFailure, _tesseract_path, import_file, make_song


class OcrTests(unittest.TestCase):
    def test_printed_jianpu_image_and_pdf(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("需要 DTmusic 环境中的 Pillow 与 pypdfium2")
        try:
            _tesseract_path()
        except ImportFailure:
            self.skipTest("需要 Tesseract")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 64)
            image = Image.new("RGB", (1500, 300), "white")
            ImageDraw.Draw(image).text((50, 80), "1=C  |  1  2  3  4  |", fill="black", font=font)
            png = base / "sample.png"
            pdf = base / "sample.pdf"
            image.save(png)
            image.save(pdf, "PDF", resolution=150)
            for path in (png, pdf):
                with self.subTest(suffix=path.suffix):
                    result = import_file(path, "简谱", (4, 4))
                    song = make_song(result, 0, "OCR 测试", 96, (4, 4))
                    self.assertEqual(song["notes"], [["1", 1], ["2", 1], ["3", 1], ["4", 1]])


if __name__ == "__main__":
    unittest.main()
