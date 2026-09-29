import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from discord.http import handle_message_parameters

from cogs.ai_chat import AIChatCog
from pos_ai import _send_plain_response


def incoming(message_id):
    return SimpleNamespace(
        id=message_id, guild=SimpleNamespace(id=10),
        author=SimpleNamespace(id=20, bot=False), content="P.OS, привет",
        reply=AsyncMock(),
    )


class AIMessageDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AIChatCog(SimpleNamespace())
        self.moderation = AsyncMock(return_value=False)
        self.vacation = AsyncMock(return_value=False)
        self.handler = AsyncMock(return_value=True)
        # Extension loading tests may reload the module; patch this class's
        # own globals, rather than a possibly newer sys.modules instance.
        self.enterContext(patch.dict(AIChatCog.on_message.__globals__, {
            "wait_for_moderation": self.moderation,
            "handle_owner_vacation_ping": self.vacation,
            "handle_pos_ai": self.handler,
        }))

    async def test_concurrent_delivery_is_claimed_before_waiting_for_moderation(self):
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def moderate(_message_id):
            waiting.set()
            await release.wait()
            return False

        self.moderation.side_effect = moderate
        first = asyncio.create_task(self.cog.on_message(incoming(100)))
        await waiting.wait()
        try:
            await self.cog.on_message(incoming(100))
            self.moderation.assert_awaited_once_with(100)
            self.handler.assert_not_awaited()
        finally:
            release.set()
            await first
        self.handler.assert_awaited_once()

    async def test_late_duplicate_is_ignored_but_distinct_message_ids_are_handled(self):
        await self.cog.on_message(incoming(100))
        await self.cog.on_message(incoming(100))
        await self.cog.on_message(incoming(101))
        self.assertEqual([call.args[0].id for call in self.handler.await_args_list], [100, 101])

    async def test_exception_after_reply_does_not_replay_delivery_or_actions(self):
        message = incoming(100)

        async def reply_then_fail(current, _bot):
            await current.reply("Готово.")
            raise RuntimeError("failure after delivery")

        self.handler.side_effect = reply_then_fail
        with self.assertLogs(AIChatCog.on_message.__globals__["logger"], level="ERROR"):
            await self.cog.on_message(message)
        await self.cog.on_message(message)
        message.reply.assert_awaited_once_with("Готово.")
        self.handler.assert_awaited_once()

    async def test_cancelled_turn_is_not_replayed_with_unknown_effects(self):
        entered = asyncio.Event()

        async def pending(_message, _bot):
            entered.set()
            await asyncio.Event().wait()

        self.handler.side_effect = pending
        first = asyncio.create_task(self.cog.on_message(incoming(100)))
        await entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await self.cog.on_message(incoming(100))
        self.handler.assert_awaited_once()
        self.assertNotIn(100, self.cog._in_flight)

    async def test_vacation_reply_is_also_deduplicated(self):
        self.vacation.return_value = True
        await self.cog.on_message(incoming(100))
        await self.cog.on_message(incoming(100))
        self.vacation.assert_awaited_once()
        self.handler.assert_not_awaited()

    async def test_bounded_completed_cache_never_evicts_an_active_turn(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handle(message, _bot):
            if message.id == 100:
                entered.set()
                await release.wait()
            return True

        self.handler.side_effect = handle
        with patch.dict(AIChatCog.on_message.__globals__, {"_MAX_HANDLED_MESSAGES": 2}):
            first = asyncio.create_task(self.cog.on_message(incoming(100)))
            await entered.wait()
            try:
                for message_id in (101, 102, 103):
                    await self.cog.on_message(incoming(message_id))
                await self.cog.on_message(incoming(100))
                self.assertEqual(len(self.cog._handled), 2)
                self.assertEqual(self.handler.await_count, 4)
            finally:
                release.set()
                await first
            self.assertEqual(len(self.cog._handled), 2)


class ReplyDeliveryNonceTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_have_stable_distinct_nonces_and_other_messages_do_not_collide(self):
        message = SimpleNamespace(
            id=2**64 - 1, guild=None, reply=AsyncMock(),
            channel=SimpleNamespace(send=AsyncMock()),
        )
        text = "Первый абзац. " * 400
        await _send_plain_response(message, text)
        first = [message.reply.await_args, *message.channel.send.await_args_list]
        first_nonces = [call.kwargs["nonce"] for call in first]
        self.assertGreater(len(first_nonces), 1)
        self.assertEqual(len(set(first_nonces)), len(first_nonces))
        self.assertTrue(all(len(nonce) <= 25 for nonce in first_nonces))
        for call in first:
            with handle_message_parameters(content=call.args[0], nonce=call.kwargs["nonce"]) as params:
                self.assertTrue(params.payload["enforce_nonce"])
                self.assertEqual(params.payload["nonce"], call.kwargs["nonce"])

        message.reply.reset_mock()
        message.channel.send.reset_mock()
        await _send_plain_response(message, text)
        repeated = [message.reply.await_args, *message.channel.send.await_args_list]
        self.assertEqual([call.kwargs["nonce"] for call in repeated], first_nonces)

        message.id -= 1
        message.reply.reset_mock()
        message.channel.send.reset_mock()
        await _send_plain_response(message, text)
        different = [message.reply.await_args, *message.channel.send.await_args_list]
        self.assertTrue(set(first_nonces).isdisjoint(call.kwargs["nonce"] for call in different))
