from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import moderation


class VideoModerationTests(IsolatedAsyncioTestCase):
    async def test_ffmpeg_preview_scale_has_complete_landscape_and_portrait_branches(self):
        captured_filter = ""

        def fake_run(command, **_kwargs):
            nonlocal captured_filter
            captured_filter = command[command.index("-vf") + 1]
            with open(command[-1], "wb") as output:
                output.write(b"preview")
            return SimpleNamespace(returncode=0)

        with patch.object(moderation.imageio_ffmpeg, "get_ffmpeg_exe", return_value="ffmpeg"), patch.object(
            moderation.subprocess,
            "run",
            side_effect=fake_run,
        ):
            previews = await moderation._extract_video_frames_ffmpeg("source.mp4", [0.5], max_side=720)

        self.assertEqual(len(previews), 1)
        self.assertIn("if(gt(iw,ih),min(iw,720),-2)", captured_filter)
        self.assertIn("if(gt(iw,ih),-2,min(ih,720))", captured_filter)

    async def test_video_download_enforces_actual_byte_limit(self):
        attachment = SimpleNamespace(
            size=0,
            filename="video.mp4",
            read=AsyncMock(return_value=b"12345"),
        )
        duration_probe = AsyncMock(return_value=1.0)

        with patch.object(moderation, "AI_MEDIA_MAX_BYTES", 4), patch.object(
            moderation,
            "_get_video_duration_ffmpeg",
            duration_probe,
        ):
            result = await moderation._attachment_video_to_data_urls(attachment)

        self.assertEqual(result, [])
        duration_probe.assert_not_awaited()
