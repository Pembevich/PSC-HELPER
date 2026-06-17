import unittest

from pos_ai import resolve_role_smart, _normalize_role_name


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


class FakeGuild:
    def __init__(self, roles):
        # эмулируем @everyone, как на реальном сервере
        self.roles = [FakeRole(0, "@everyone")] + roles

    def get_role(self, role_id):
        for r in self.roles:
            if r.id == role_id:
                return r
        return None


class ResolveRoleSmartTests(unittest.TestCase):
    def setUp(self):
        self.guild = FakeGuild([
            FakeRole(111, "Наблюдатель"),
            FakeRole(222, "Arbeiter"),
            FakeRole(333, "🎮 Игрок"),
            FakeRole(444, "Офицер PSC"),
        ])

    def test_resolve_by_id(self):
        self.assertEqual(resolve_role_smart(self.guild, "111").id, 111)

    def test_resolve_by_mention_format(self):
        self.assertEqual(resolve_role_smart(self.guild, "<@&222>").id, 222)

    def test_resolve_exact_name(self):
        self.assertEqual(resolve_role_smart(self.guild, "Наблюдатель").id, 111)

    def test_resolve_case_insensitive(self):
        self.assertEqual(resolve_role_smart(self.guild, "наблюдатель").id, 111)

    def test_resolve_ignores_emoji_and_spaces(self):
        # "Игрок" должен найти "🎮 Игрок"
        self.assertEqual(resolve_role_smart(self.guild, "Игрок").id, 333)

    def test_resolve_fuzzy_typo(self):
        # опечатка "Наблюдатил" → "Наблюдатель"
        self.assertEqual(resolve_role_smart(self.guild, "Наблюдатил").id, 111)

    def test_resolve_substring(self):
        self.assertEqual(resolve_role_smart(self.guild, "Офицер").id, 444)

    def test_unknown_role_returns_none(self):
        self.assertIsNone(resolve_role_smart(self.guild, "Несуществующая Роль XYZ"))

    def test_empty_returns_none(self):
        self.assertIsNone(resolve_role_smart(self.guild, ""))

    def test_normalize_strips_decoration(self):
        self.assertEqual(_normalize_role_name("🎮 Игрок!"), "игрок")
        self.assertEqual(_normalize_role_name("Офицер PSC"), "офицерpsc")


if __name__ == "__main__":
    unittest.main()
