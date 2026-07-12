import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import pos_ai


class AIMemoryConcurrencyTests(IsolatedAsyncioTestCase):
    def setUp(self):
        pos_ai.reset_ai_runtime_caches_after_restore()

    async def test_concurrent_cold_guild_load_uses_one_shared_list(self):
        async def delayed_load(_user_id, _guild_id):
            await asyncio.sleep(0.01)
            return "[]"

        loader = AsyncMock(side_effect=delayed_load)
        with patch.object(pos_ai, "get_ai_context", loader):
            first, second = await asyncio.gather(
                pos_ai._load_guild_memory(123),
                pos_ai._load_guild_memory(123),
            )

        self.assertIs(first, second)
        loader.assert_awaited_once_with(0, 123)

    async def test_concurrent_cold_user_load_uses_one_shared_list(self):
        async def delayed_load(_user_id, _guild_id):
            await asyncio.sleep(0.01)
            return "[]"

        loader = AsyncMock(side_effect=delayed_load)
        with patch.object(pos_ai, "get_ai_context", loader):
            first, second = await asyncio.gather(
                pos_ai._load_user_ctx(77, 123),
                pos_ai._load_user_ctx(77, 123),
            )

        self.assertIs(first, second)
        loader.assert_awaited_once_with(77, 123)
