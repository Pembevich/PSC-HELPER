import asyncio
import json
import unittest
from unittest.mock import patch

import guild_config


class CoerceValueTests(unittest.TestCase):
    def test_bool_true_variants(self):
        for v in ["true", "1", "да", "on", "вкл", True]:
            ok, val = guild_config.coerce_value("filter_ads", v)
            self.assertTrue(ok)
            self.assertIs(val, True)

    def test_bool_false_variants(self):
        for v in ["false", "0", "нет", "off", "выкл", False]:
            ok, val = guild_config.coerce_value("filter_flood", v)
            self.assertTrue(ok)
            self.assertIs(val, False)

    def test_bool_invalid(self):
        ok, _ = guild_config.coerce_value("filter_ads", "может быть")
        self.assertFalse(ok)

    def test_int_clamped_to_bounds(self):
        ok, val = guild_config.coerce_value("spam_duplicates_threshold", "9999")
        self.assertTrue(ok)
        self.assertEqual(val, 50)  # верхняя граница
        ok, val = guild_config.coerce_value("spam_duplicates_threshold", "1")
        self.assertTrue(ok)
        self.assertEqual(val, 2)  # нижняя граница

    def test_int_invalid(self):
        ok, _ = guild_config.coerce_value("flood_window_seconds", "abc")
        self.assertFalse(ok)

    def test_unknown_key(self):
        ok, _ = guild_config.coerce_value("nonexistent", "1")
        self.assertFalse(ok)


class MergeDefaultsTests(unittest.TestCase):
    def test_merge_keeps_defaults_for_missing(self):
        merged = guild_config._merge_with_defaults({"filter_ads": False})
        self.assertIs(merged["filter_ads"], False)
        self.assertIn("filter_spam", merged)
        self.assertIs(merged["filter_spam"], True)

    def test_merge_ignores_unknown_keys(self):
        merged = guild_config._merge_with_defaults({"bogus": 1})
        self.assertNotIn("bogus", merged)

    def test_defaults_allow_profanity(self):
        # Маты/оскорбления разрешены по умолчанию.
        self.assertIs(guild_config.defaults()["allow_profanity"], True)


class GuildSettingsConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        guild_config.invalidate()
        self.rows: dict[int, str] = {}

    def tearDown(self):
        guild_config.invalidate()

    async def _read(self, guild_id: int):
        await asyncio.sleep(0)
        return self.rows.get(guild_id)

    async def _write(self, guild_id: int, raw: str):
        await asyncio.sleep(0)
        self.rows[guild_id] = raw

    async def test_parallel_updates_do_not_overwrite_each_other(self):
        with patch.object(guild_config, "get_guild_settings_raw", self._read), patch.object(
            guild_config,
            "set_guild_settings_raw",
            self._write,
        ):
            await asyncio.gather(
                guild_config.update_settings(123, {"filter_ads": False}),
                guild_config.update_settings(123, {"mention_limit": 12}),
            )

        stored = json.loads(self.rows[123])
        self.assertIs(stored["filter_ads"], False)
        self.assertEqual(stored["mention_limit"], 12)

    async def test_stored_values_are_coerced_and_unknown_keys_ignored(self):
        self.rows[123] = json.dumps(
            {
                "filter_ads": "false",
                "mention_limit": 9999,
                "timeout_hours": "not-a-number",
                "unknown": True,
            }
        )
        with patch.object(guild_config, "get_guild_settings_raw", self._read):
            settings = await guild_config.get_settings(123)

        self.assertIs(settings["filter_ads"], False)
        self.assertEqual(settings["mention_limit"], 50)
        self.assertEqual(settings["timeout_hours"], 24)
        self.assertNotIn("unknown", settings)


if __name__ == "__main__":
    unittest.main()
