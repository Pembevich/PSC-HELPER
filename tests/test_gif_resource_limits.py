import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import commands
from PIL import Image


class FakeAttachment:
    def __init__(self, filename: str, payload: bytes, *, declared_size: int):
        self.filename = filename
        self.content_type = "image/png"
        self.size = declared_size
        self.payload = payload
        self.save_calls = 0

    async def save(self, path: Path):
        self.save_calls += 1
        path.write_bytes(self.payload)


class GifResourceLimitTests(IsolatedAsyncioTestCase):
    async def test_actual_total_size_is_enforced(self):
        attachments = [
            FakeAttachment("first.png", b"1234", declared_size=1),
            FakeAttachment("second.png", b"5678", declared_size=1),
        ]

        with patch.object(commands, "GIF_MAX_ATTACHMENT_BYTES", 10), patch.object(
            commands,
            "GIF_MAX_TOTAL_BYTES",
            6,
        ):
            with self.assertRaisesRegex(ValueError, "Фактический суммарный размер"):
                await commands.generate_gif_from_attachments(attachments)

    async def test_attachment_download_has_a_deadline(self):
        attachment = FakeAttachment("stalled.png", b"", declared_size=1)

        async def stalled_save(_path: Path):
            await asyncio.Event().wait()

        attachment.save = stalled_save
        with patch.object(commands, "GIF_ATTACHMENT_DOWNLOAD_SECONDS", 0.01):
            with self.assertRaisesRegex(RuntimeError, "превысила"):
                await commands.generate_gif_from_attachments([attachment])


class GifQualityTests(TestCase):
    def test_uses_highest_video_profile_that_fits(self):
        calls: list[tuple[int, int]] = []

        def fake_encode(
            video_path: str,
            output_path: str,
            *,
            fps: int,
            max_duration: float,
            max_dim: int,
            timeout: float,
        ) -> None:
            del video_path, max_duration, timeout
            calls.append((max_dim, fps))
            payload_size = 20 if max_dim > 560 else 5
            Path(output_path).write_bytes(b"x" * payload_size)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / "result.gif")
            with patch.object(commands, "_run_ffmpeg_gif_encode", side_effect=fake_encode):
                commands._build_gif_from_video(
                    "source.mp4",
                    output_path,
                    fps=15,
                    max_output_bytes=10,
                )

            self.assertEqual(Path(output_path).read_bytes(), b"x" * 5)
            self.assertEqual(calls, [(720, 15), (640, 15), (560, 15)])

    def test_uses_guild_upload_limit_with_headroom_and_hard_cap(self):
        guild_limit = 10 * 1024 * 1024
        guild = SimpleNamespace(filesize_limit=guild_limit)
        self.assertEqual(
            commands.gif_output_limit_for_guild(guild),
            guild_limit - commands.GIF_UPLOAD_HEADROOM_BYTES,
        )

        boosted = SimpleNamespace(filesize_limit=100 * 1024 * 1024)
        self.assertEqual(
            commands.gif_output_limit_for_guild(boosted),
            commands.GIF_MAX_OUTPUT_BYTES,
        )

    def test_preserves_per_frame_delays_from_animated_gif(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.gif"
            first = Image.new("RGB", (16, 16), "red")
            second = Image.new("RGB", (16, 16), "blue")
            first.save(
                source_path,
                format="GIF",
                save_all=True,
                append_images=[second],
                duration=[40, 120],
                loop=0,
            )

            frames, durations, animated = commands._load_image_frames(
                [str(source_path)],
                duration=None,
            )

            self.assertTrue(animated)
            self.assertEqual(len(frames), 2)
            self.assertEqual(durations, [40, 120])
