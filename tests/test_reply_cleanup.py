import unittest
from unittest.mock import patch

# pos_ai imports storage/discord; patch DB side-effects on import.
with patch("storage.add_entry"), \
     patch("storage.get_ai_context"), \
     patch("storage.update_ai_context"):
    from pos_ai import _strip_address_prefix_from_reply


class StripAddressPrefixTests(unittest.TestCase):
    def test_strips_verb_with_id(self):
        self.assertEqual(
            _strip_address_prefix_from_reply(
                "Отвечаю Васе (@vasya, ID: 123456789012): привет, что хотел?"
            ),
            "привет, что хотел?",
        )

    def test_strips_name_login_id_prefix(self):
        self.assertEqual(
            _strip_address_prefix_from_reply("Вася (@vasya, ID: 123456789012): да всё нормально"),
            "да всё нормально",
        )

    def test_strips_bracketed_history_marker(self):
        self.assertEqual(
            _strip_address_prefix_from_reply(
                "[Ответ пользователю Петя (@petya, ID: 987654321098)]\nне лезь сюда"
            ),
            "не лезь сюда",
        )

    def test_strips_bare_login_id(self):
        self.assertEqual(
            _strip_address_prefix_from_reply("(@login, ID: 123456789012): текст ответа"),
            "текст ответа",
        )

    def test_strips_bare_verb(self):
        self.assertEqual(
            _strip_address_prefix_from_reply("Отвечаю: смотри в документации."),
            "смотри в документации.",
        )

    def test_keeps_plain_reply(self):
        text = "Просто нормальный ответ без префиксов."
        self.assertEqual(_strip_address_prefix_from_reply(text), text)

    def test_keeps_time_colon(self):
        text = "Время: 12:30, всё по плану."
        self.assertEqual(_strip_address_prefix_from_reply(text), text)

    def test_keeps_otvet_noun_phrase(self):
        # "Ответ на твой вопрос: да." — существительное, не адресный префикс.
        text = "Ответ на твой вопрос: да."
        self.assertEqual(_strip_address_prefix_from_reply(text), text)

    def test_keeps_identity_line(self):
        text = "Я P.OS, Provision Operating System."
        self.assertEqual(_strip_address_prefix_from_reply(text), text)

    def test_empty(self):
        self.assertEqual(_strip_address_prefix_from_reply(""), "")


if __name__ == "__main__":
    unittest.main()
