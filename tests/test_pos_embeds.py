import unittest
from types import SimpleNamespace

import pos_embeds


class PosEmbedTests(unittest.TestCase):
    def test_success_and_failure_results_use_distinct_statuses(self):
        guild = SimpleNamespace(name="Test server", id=123)
        success = pos_embeds.build_action_result_embed(
            "Кик пользователя",
            "Пользователь успешно кикнут.",
            guild=guild,
        )
        failure = pos_embeds.build_action_result_embed(
            "Кик пользователя",
            "Ошибка: Discord запретил операцию.",
            guild=guild,
        )

        self.assertIn("ВЫПОЛНЕНО", success.author.name)
        self.assertIn("НЕ ВЫПОЛНЕНО", failure.author.name)
        self.assertNotEqual(success.color.value, failure.color.value)
        self.assertIn("Test server", success.footer.text)

    def test_embed_content_respects_discord_limits(self):
        embed = pos_embeds.build_action_result_embed(
            "x" * 1000,
            "y" * 10_000,
        )
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description), 4096)


if __name__ == "__main__":
    unittest.main()
