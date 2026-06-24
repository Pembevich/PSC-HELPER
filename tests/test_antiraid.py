import unittest
from datetime import timedelta
from types import SimpleNamespace

import discord

import antiraid


def _member(uid, *, age_hours=1000.0, avatar=True, name="user", guild_id=1):
    created = discord.utils.utcnow() - timedelta(hours=age_hours)
    return SimpleNamespace(
        id=uid,
        guild=SimpleNamespace(id=guild_id),
        name=name,
        created_at=created,
        avatar=object() if avatar else None,
        bot=False,
    )


class RegisterJoinTests(unittest.TestCase):
    def setUp(self):
        antiraid.clear()

    def tearDown(self):
        antiraid.clear()

    def test_threshold_triggers(self):
        triggered_last = False
        for i in range(5):
            _, triggered_last = antiraid.register_join(1, now=100 + i, window=60, threshold=5)
        self.assertTrue(triggered_last)

    def test_below_threshold_no_trigger(self):
        _, triggered = antiraid.register_join(1, now=100, window=60, threshold=5)
        self.assertFalse(triggered)

    def test_window_evicts_old(self):
        antiraid.register_join(1, now=0, window=10, threshold=3)
        antiraid.register_join(1, now=5, window=10, threshold=3)
        # 100s later — старые выпали, счётчик=1, не рейд
        count, triggered = antiraid.register_join(1, now=100, window=10, threshold=3)
        self.assertEqual(count, 1)
        self.assertFalse(triggered)


class RaidModeTests(unittest.TestCase):
    def setUp(self):
        antiraid.clear()

    def test_set_and_expire(self):
        antiraid.set_raid_mode(7, now=100, cooldown=60)
        self.assertTrue(antiraid.is_raid_mode(7, now=120))
        self.assertFalse(antiraid.is_raid_mode(7, now=200))


class AccountScoringTests(unittest.TestCase):
    def test_fresh_account_flagged(self):
        m = _member(1, age_hours=2, avatar=True)
        sigs = antiraid.suspicious_join_signals(m, 72)
        self.assertTrue(any("свеж" in s or "час" in s for s in sigs))

    def test_no_avatar_flagged(self):
        m = _member(1, age_hours=5000, avatar=False)
        self.assertIn("нет аватара", antiraid.suspicious_join_signals(m, 72))

    def test_invite_in_name(self):
        m = _member(1, age_hours=5000, avatar=True, name="free nitro discord.gg/x")
        sigs = antiraid.suspicious_join_signals(m, 72)
        self.assertTrue(any("инвайт" in s for s in sigs))

    def test_trailing_digits(self):
        m = _member(1, age_hours=5000, avatar=True, name="bob12345")
        sigs = antiraid.suspicious_join_signals(m, 72)
        self.assertTrue(any("цифр" in s for s in sigs))

    def test_clean_account_no_signals(self):
        m = _member(1, age_hours=5000, avatar=True, name="Александр")
        self.assertEqual(antiraid.suspicious_join_signals(m, 72), [])


class EvaluateJoinTests(unittest.TestCase):
    def setUp(self):
        antiraid.clear()

    def tearDown(self):
        antiraid.clear()

    def _settings(self, **over):
        s = {
            "filter_raid": True,
            "raid_join_window_seconds": 60,
            "raid_join_threshold": 4,
            "min_account_age_hours": 72,
            "raid_action": "kick",
        }
        s.update(over)
        return s

    def test_raid_kicks_fresh_account(self):
        s = self._settings()
        last = None
        for i in range(4):
            m = _member(100 + i, age_hours=1, avatar=False, name=f"u{i}", guild_id=9)
            last = antiraid.evaluate_join(m, s, now=1000 + i)
        self.assertTrue(last["raid"])
        self.assertEqual(last["action"], "kick")

    def test_raid_only_alerts_established_account(self):
        s = self._settings()
        # 3 свежих заводят счётчик, 4-й — старый чистый аккаунт пересекает порог
        for i in range(3):
            antiraid.evaluate_join(_member(200 + i, age_hours=1, avatar=False, guild_id=11), s, now=2000 + i)
        clean = _member(299, age_hours=9000, avatar=True, name="Виктория", guild_id=11)
        res = antiraid.evaluate_join(clean, s, now=2003)
        self.assertTrue(res["raid_mode"])
        self.assertEqual(res["action"], "alert")  # легитимного не наказываем

    def test_no_raid_clean_account_no_action(self):
        s = self._settings()
        res = antiraid.evaluate_join(_member(1, age_hours=9000, avatar=True, name="Иван", guild_id=12), s, now=5000)
        self.assertEqual(res["action"], "none")

    def test_filter_off_disables(self):
        s = self._settings(filter_raid=False)
        for i in range(10):
            res = antiraid.evaluate_join(_member(300 + i, age_hours=1, avatar=False, guild_id=13), s, now=6000 + i)
        self.assertEqual(res["action"], "none")
        self.assertFalse(res["raid"])

    def test_quarantine_is_default_action_for_fresh(self):
        s = self._settings(raid_action="quarantine")
        last = None
        for i in range(4):
            m = _member(400 + i, age_hours=1, avatar=False, guild_id=21, name=f"u{i}")
            last = antiraid.evaluate_join(m, s, now=7000 + i)
        self.assertTrue(last["raid"])
        self.assertEqual(last["action"], "quarantine")

    def test_deactivate_raid_mode(self):
        s = self._settings(raid_action="quarantine")
        for i in range(4):
            antiraid.evaluate_join(_member(500 + i, age_hours=1, avatar=False, guild_id=22), s, now=8000 + i)
        self.assertTrue(antiraid.is_raid_mode(22, now=8003))
        self.assertTrue(antiraid.deactivate_raid_mode(22, now=8003))
        self.assertFalse(antiraid.is_raid_mode(22, now=8003))
        # повторно — уже не активен
        self.assertFalse(antiraid.deactivate_raid_mode(22, now=8003))


if __name__ == "__main__":
    unittest.main()
