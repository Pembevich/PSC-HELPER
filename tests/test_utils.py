import unittest

from utils import sanitize_discord_token


class SanitizeDiscordTokenTests(unittest.TestCase):
    def test_strips_quotes_and_whitespace(self):
        self.assertEqual(
            sanitize_discord_token('  "abc.def.ghi"  '),
            "abc.def.ghi",
        )

    def test_removes_newlines(self):
        self.assertEqual(
            sanitize_discord_token("abc\ndef\rghi"),
            "abcdefghi",
        )

    def test_handles_missing_token(self):
        self.assertIsNone(sanitize_discord_token(None))


if __name__ == "__main__":
    unittest.main()
