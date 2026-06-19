import asyncio
import unittest

from utils import classify_applicant_danger, assess_roblox_account


class ClassifyDangerTests(unittest.TestCase):
    def test_banned_roblox_is_high(self):
        level, dangerous = classify_applicant_danger([], {"banned": True, "found": True, "flags": ["Roblox-аккаунт ЗАБАНЕН"]})
        self.assertEqual(level, "high")
        self.assertTrue(dangerous)

    def test_not_found_roblox_is_high(self):
        level, dangerous = classify_applicant_danger([], {"banned": False, "found": False, "flags": ["не найден"]})
        self.assertEqual(level, "high")
        self.assertTrue(dangerous)

    def test_many_flags_is_high(self):
        d = ["свежий Discord-аккаунт", "много цифр"]
        rb = {"banned": False, "found": True, "flags": ["молодой Roblox-аккаунт", "подозрительное описание"]}
        level, dangerous = classify_applicant_danger(d, rb)
        self.assertEqual(level, "high")
        self.assertTrue(dangerous)

    def test_medium(self):
        level, dangerous = classify_applicant_danger(["много цифр"], {"banned": False, "found": True, "flags": ["молодой Roblox-аккаунт"]})
        self.assertEqual(level, "medium")
        self.assertFalse(dangerous)

    def test_clean_is_low(self):
        level, dangerous = classify_applicant_danger([], {"banned": False, "found": True, "flags": []})
        self.assertEqual(level, "low")
        self.assertFalse(dangerous)


class RobloxAccountTests(unittest.TestCase):
    def test_empty_nick_returns_not_found_without_network(self):
        # Пустой логин не должен делать сетевой запрос и должен дать found=False.
        result = asyncio.run(assess_roblox_account(""))
        self.assertEqual(result["found"], False)
        self.assertIn("Roblox-логин не указан", result["flags"])

    def test_result_shape(self):
        result = asyncio.run(assess_roblox_account("   "))
        for key in ["query", "found", "user_id", "banned", "age_days", "flags", "profile_url"]:
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
