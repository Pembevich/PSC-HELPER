import unittest

import ai_client


class GeminiPriorityTests(unittest.TestCase):
    def setUp(self):
        # Сохраняем и подменяем глобальный пул провайдеров.
        self._orig_pool = ai_client._AI_PROVIDER_POOL
        self._orig_cursor = ai_client._provider_cursor
        self._orig_backoff = dict(ai_client._provider_backoff_until)
        ai_client._provider_cursor = 0
        ai_client._provider_backoff_until = {}

    def tearDown(self):
        ai_client._AI_PROVIDER_POOL = self._orig_pool
        ai_client._provider_cursor = self._orig_cursor
        ai_client._provider_backoff_until = self._orig_backoff

    def _pool(self, *providers):
        return [
            {"name": f"p{i}", "api_key": "k", "api_url": "u", "model": "m", "provider": p}
            for i, p in enumerate(providers)
        ]

    def test_prefers_gemini_when_no_type_requested(self):
        # github_models первым в пуле, но Gemini должен быть выбран как первичный.
        ai_client._AI_PROVIDER_POOL = self._pool("github_models", "gemini")
        idx = ai_client._pick_provider_index(None)
        self.assertEqual(ai_client._AI_PROVIDER_POOL[idx]["provider"], "gemini")

    def test_falls_back_to_others_when_gemini_on_cooldown(self):
        ai_client._AI_PROVIDER_POOL = self._pool("github_models", "gemini")
        # Gemini (индекс 1) на кулдауне → берём другой доступный.
        ai_client._provider_backoff_until = {1: ai_client.time.monotonic() + 999}
        idx = ai_client._pick_provider_index(None)
        self.assertEqual(idx, 0)
        self.assertEqual(ai_client._AI_PROVIDER_POOL[idx]["provider"], "github_models")

    def test_explicit_type_takes_priority(self):
        ai_client._AI_PROVIDER_POOL = self._pool("gemini", "github_models")
        idx = ai_client._pick_provider_index("github_models")
        self.assertEqual(ai_client._AI_PROVIDER_POOL[idx]["provider"], "github_models")

    def test_no_gemini_in_pool_uses_any(self):
        ai_client._AI_PROVIDER_POOL = self._pool("github_models", "generic_openai_compatible")
        idx = ai_client._pick_provider_index(None)
        self.assertIsNotNone(idx)


if __name__ == "__main__":
    unittest.main()
