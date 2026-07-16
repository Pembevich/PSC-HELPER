import unittest

import discord

from logging_utils import _build_log_embed


class LogEmbedBoundaryTests(unittest.TestCase):
    def test_embed_is_truncated_to_discord_limits(self):
        embed = _build_log_embed(
            "security",
            "T" * 1000,
            "D" * 10_000,
            color=discord.Color.red(),
            fields=[("N" * 1000, "V" * 5000, False) for _ in range(40)],
            footer="F" * 5000,
        )

        self.assertLessEqual(len(embed.title or ""), 256)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertLessEqual(len(embed.fields), 25)
        self.assertLessEqual(len(embed.footer.text or ""), 2048)
        self.assertLessEqual(len(embed), 6000)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)

    def test_empty_parts_receive_valid_fallbacks(self):
        embed = _build_log_embed(
            "server",
            "",
            "",
            color=discord.Color.blue(),
            fields=[("", "", False)],
            footer=None,
        )
        self.assertEqual(embed.title, "Журнал P.OS")
        self.assertEqual(embed.fields[0].name, "—")
        self.assertEqual(embed.fields[0].value, "—")


if __name__ == "__main__":
    unittest.main()
