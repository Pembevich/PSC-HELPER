import unittest

from commands import format_gif_error_for_user, parse_gif_options_from_text


class ParseGifOptionsFromTextTests(unittest.TestCase):
    def test_parses_fps(self):
        opts = parse_gif_options_from_text("fps=15")
        self.assertEqual(opts.get("fps"), 15)

        opts = parse_gif_options_from_text("сделай фпс 12")
        self.assertEqual(opts.get("fps"), 12)

        opts = parse_gif_options_from_text("10fps")
        self.assertEqual(opts.get("fps"), 10)

    def test_parses_delay_ms(self):
        opts = parse_gif_options_from_text("delay=250")
        self.assertEqual(opts.get("duration"), 250)

        opts = parse_gif_options_from_text("200ms пожалуйста")
        self.assertEqual(opts.get("duration"), 200)

        opts = parse_gif_options_from_text("задержка=400мс")
        self.assertEqual(opts.get("duration"), 400)

    def test_parses_seconds(self):
        opts = parse_gif_options_from_text("duration=2.5s")
        self.assertEqual(opts.get("max_video_seconds"), 2.5)
        self.assertEqual(opts.get("duration"), 2500)

        opts = parse_gif_options_from_text("время 5сек")
        self.assertEqual(opts.get("max_video_seconds"), 5.0)
        self.assertEqual(opts.get("duration"), 5000)

    def test_parses_standalone_number_fps(self):
        # Supported integer values are treated as FPS.
        opts = parse_gif_options_from_text("12")
        self.assertEqual(opts.get("fps"), 12)
        self.assertNotIn("duration", opts)

    def test_unsupported_fps_is_not_silently_clamped(self):
        self.assertNotIn("fps", parse_gif_options_from_text("fps=30"))

    def test_parses_standalone_number_delay_ms(self):
        # 50 to 3000 is treated as delay in ms
        opts = parse_gif_options_from_text("300")
        self.assertEqual(opts.get("duration"), 300)
        self.assertNotIn("fps", opts)

    def test_parses_standalone_number_seconds(self):
        # 0.1 to 5.0 is treated as seconds
        opts = parse_gif_options_from_text("0.5")
        self.assertEqual(opts.get("duration"), 500)
        self.assertEqual(opts.get("max_video_seconds"), 0.5)

    def test_empty_or_no_match(self):
        opts = parse_gif_options_from_text("просто гифка")
        self.assertEqual(opts, {})

    def test_internal_gif_error_is_not_exposed(self):
        result = format_gif_error_for_user(
            RuntimeError("ffmpeg failed at /private/tmp/secret/input.mov")
        )
        self.assertNotIn("/private/tmp", result)
        self.assertIn("Проверь формат", result)


if __name__ == "__main__":
    unittest.main()
