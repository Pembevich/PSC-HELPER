import unittest

from utils import strip_leading_enumeration


class StripLeadingEnumerationTests(unittest.TestCase):
    def test_strips_dot_number(self):
        self.assertEqual(strip_leading_enumeration("1. MyNick"), "MyNick")

    def test_strips_paren_number(self):
        self.assertEqual(strip_leading_enumeration("2) CoolGuy"), "CoolGuy")

    def test_strips_dash_and_colon(self):
        self.assertEqual(strip_leading_enumeration("3 - nick"), "nick")
        self.assertEqual(strip_leading_enumeration("1: nick"), "nick")

    def test_keeps_nick_starting_with_digits(self):
        # Нет разделителя после цифр — это часть ника, не номер пункта.
        self.assertEqual(strip_leading_enumeration("123gamer"), "123gamer")

    def test_no_enumeration_unchanged(self):
        self.assertEqual(strip_leading_enumeration("PlainNick"), "PlainNick")

    def test_trims_surrounding_space(self):
        self.assertEqual(strip_leading_enumeration("  1.   Nick  "), "Nick")

    def test_empty(self):
        self.assertEqual(strip_leading_enumeration(""), "")
        self.assertEqual(strip_leading_enumeration(None), "")


if __name__ == "__main__":
    unittest.main()
