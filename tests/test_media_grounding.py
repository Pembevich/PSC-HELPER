import unittest
from unittest.mock import patch

from media_intelligence import MediaContext


with patch("storage.add_entry"), \
     patch("storage.get_ai_context"), \
     patch("storage.update_ai_context"):
    from pos_ai import _guard_unverified_media_reply


class MediaGroundingTests(unittest.TestCase):
    def test_unverified_audio_claim_is_replaced_with_honest_failure(self):
        context = MediaContext(audio_files=1)

        guarded = _guard_unverified_media_reply(
            "Аудиозапись говорит, что кодовое число 742.",
            context,
        )

        self.assertIn("не удалось достоверно расшифровать", guarded)
        self.assertNotIn("742", guarded)

    def test_visual_description_survives_unverified_video_audio(self):
        context = MediaContext(
            video_files=1,
            visual_inputs=["data:image/jpeg;base64,abc"],
        )

        guarded = _guard_unverified_media_reply(
            (
                "На видео виден цветной тестовый узор. "
                "Голос произносит кодовое число 891."
            ),
            context,
        )

        self.assertIn("цветной тестовый узор", guarded)
        self.assertIn("не удалось достоверно проверить", guarded)
        self.assertNotIn("891", guarded)

    def test_verified_media_analysis_is_left_untouched(self):
        context = MediaContext(
            audio_files=1,
            audio_analysis_count=1,
        )
        reply = "В аудио произнесено число 42."

        self.assertEqual(_guard_unverified_media_reply(reply, context), reply)

    def test_audio_only_numeric_guess_is_rejected_without_audio_words(self):
        context = MediaContext(audio_files=1)

        guarded = _guard_unverified_media_reply("Код: 742.", context)

        self.assertIn("не удалось достоверно расшифровать", guarded)
        self.assertNotIn("742", guarded)

    def test_audio_request_rejects_bare_video_guess(self):
        context = MediaContext(
            video_files=1,
            visual_inputs=["data:image/jpeg;base64,abc"],
        )

        guarded = _guard_unverified_media_reply(
            "Код: 891.",
            context,
            "Точно расшифруй речь в видео.",
        )

        self.assertIn("не удалось достоверно проверить", guarded)
        self.assertNotIn("891", guarded)
