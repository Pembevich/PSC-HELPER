import os
import unittest

import pos_ai


class RedactSecretsTests(unittest.TestCase):
    def test_redacts_env_token(self):
        os.environ["DISCORD_TOKEN"] = "supersecrettoken1234567890"
        try:
            out = pos_ai._redact_secrets("Мой токен: supersecrettoken1234567890 вот так")
            self.assertNotIn("supersecrettoken1234567890", out)
            self.assertIn("[удалено]", out)
        finally:
            os.environ.pop("DISCORD_TOKEN", None)

    def test_redacts_secret_like_token_pattern(self):
        out = pos_ai._redact_secrets("ключ sk-abcdefghij1234567890ABCDEFG конец")
        self.assertNotIn("sk-abcdefghij1234567890ABCDEFG", out)

    def test_redacts_google_api_key_pattern(self):
        out = pos_ai._redact_secrets("AIzaSyA1234567890abcdefghijklmnopqrstu")
        self.assertNotIn("AIzaSyA1234567890abcdefghijklmnopqrstu", out)

    def test_leaves_normal_text_untouched(self):
        text = "Обычный ответ P.OS без секретов."
        self.assertEqual(pos_ai._redact_secrets(text), text)

    def test_handles_empty(self):
        self.assertEqual(pos_ai._redact_secrets(""), "")


if __name__ == "__main__":
    unittest.main()
