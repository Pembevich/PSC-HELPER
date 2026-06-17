import unittest

from utils import sanitize_discord_token, extract_clean_keyword


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


class ExtractCleanKeywordTests(unittest.TestCase):
    def test_keeps_latin(self):
        self.assertEqual(extract_clean_keyword("Arbaiter!"), "arbaiter")

    def test_keeps_cyrillic(self):
        # #10: раньше [^a-z] вырезал кириллицу и возвращал пустую строку
        self.assertEqual(extract_clean_keyword("Отряд: Арбайтер"), "отрядарбайтер")

    def test_strips_digits_and_symbols(self):
        self.assertEqual(extract_clean_keyword("a1b2 c-3!"), "abc")


if __name__ == "__main__":
    unittest.main()

