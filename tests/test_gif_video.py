import os
import shutil
import subprocess
import tempfile
import unittest

import imageio_ffmpeg

from commands import _build_gif_from_video


def _make_test_video(path: str, size: str, seconds: int = 1) -> bool:
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    res = subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=10", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return res.returncode == 0 and os.path.exists(path)


class GifFromVideoTests(unittest.TestCase):
    """Регрессия: горизонтальные видео падали в ffmpeg-фильтре из-за отсутствия
    else-ветки в выражении высоты scale. Теперь обе ориентации дают GIF."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _gif_ok(self, size: str):
        vid = os.path.join(self.d, "in.mp4")
        if not _make_test_video(vid, size):
            self.skipTest("ffmpeg недоступен для генерации тестового видео")
        out = os.path.join(self.d, "out.gif")
        _build_gif_from_video(vid, out, fps=10, max_duration=1)
        self.assertTrue(os.path.exists(out), f"GIF не создан для {size}")
        self.assertGreater(os.path.getsize(out), 0, f"GIF пустой для {size}")

    def test_landscape_video(self):
        self._gif_ok("320x180")

    def test_portrait_video(self):
        self._gif_ok("180x320")

    def test_square_video(self):
        self._gif_ok("240x240")


if __name__ == "__main__":
    unittest.main()
