import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import ai_client
from agent_runtime import run_agent_turn


TOOLS = [{"type": "function", "function": {"name": "send_message", "parameters": {
    "type": "object", "properties": {"text": {"type": "string"}},
}}}]
REQUEST = [{"role": "user", "content": "Выполни поручение."}]


def reply(name="send_message", args=None, call_id="call"):
    message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {"text": "OK"})},
        "extra_content": {"google": {"thought_signature": "opaque"}},
    }]}
    return 200, {}, json.dumps({"choices": [{"message": message}]})


class ProviderAffinityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.providers = [{"name": f"p-{i}", "provider": kind,
                           "api_url": f"https://p-{i}.example/chat", "model": "test", "api_key": "test"}
                          for i, kind in enumerate(["gemini", "generic_openai_compatible"])]
        self.http = AsyncMock(return_value=reply())
        self.cooldowns = {}
        for name, value in [
            ("_AI_PROVIDER_POOL", self.providers), ("_provider_cursor", 0),
            ("_provider_backoff_until", self.cooldowns), ("_ai_backoff_until", 0),
            ("_provider_lock", asyncio.Lock()), ("_AI_REQUEST_SEMAPHORE", asyncio.Semaphore(4)),
            ("_post_ai_payload", self.http),
        ]:
            self.enterContext(patch.object(ai_client, name, value))
        self.enterContext(patch.object(ai_client.aiohttp, "TCPConnector"))
        self.enterContext(patch.object(ai_client.aiohttp, "ClientSession", return_value=AsyncMock()))

    async def complete(self, **kwargs):
        return await asyncio.wait_for(ai_client.pos_chat_completion(REQUEST, tools=TOOLS, **kwargs), 2)

    def routes(self):
        return [call.args[1] for call in self.http.await_args_list]

    async def test_fallback_before_native_response_then_pin_survives_wait_for(self):
        self.http.side_effect = [(503, {}, "temporary"), reply(), reply()]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNotNone(await self.complete())
            self.cooldowns.clear()  # Gemini is healthy again, but the generic transcript stays put.
            self.assertIsNotNone(await self.complete())
        self.assertEqual(self.routes(), [p["api_url"] for p in self.providers] + [self.providers[1]["api_url"]])
        self.assertIsNone(ai_client._toolchain_provider_affinity.get())

    async def test_pinned_failure_never_sends_transcript_to_other_provider(self):
        self.http.side_effect = [reply(), (500, {}, "down"), (500, {}, "still down")]
        async with ai_client.toolchain_provider_affinity():
            await self.complete()
            self.assertIsNone(await self.complete())
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 3)

    async def test_retry_after_is_respected_instead_of_retrying_early(self):
        self.http.side_effect = [reply(), (503, {"Retry-After": "30"}, "busy")]
        async with ai_client.toolchain_provider_affinity():
            await self.complete()
            self.assertIsNone(await self.complete())
        self.assertEqual(len(self.routes()), 2)
        self.assertGreater(ai_client._provider_cooldown_remaining(0), 29)

    async def test_auxiliary_plain_request_does_not_change_pin(self):
        async with ai_client.toolchain_provider_affinity():
            await self.complete(provider_type="generic_openai_compatible")
            await ai_client.pos_chat_completion(REQUEST)  # Separate tool-internal summary.
            await self.complete()
        self.assertEqual(self.routes(), [self.providers[i]["api_url"] for i in [1, 0, 1]])

    async def test_concurrent_turns_have_independent_pins_and_nested_scopes_share(self):
        barrier = asyncio.Event()
        ready = 0

        async def turn(kind):
            nonlocal ready
            async with ai_client.toolchain_provider_affinity():
                own = ai_client._toolchain_provider_affinity.get()
                await self.complete(provider_type=kind)
                async with ai_client.toolchain_provider_affinity():
                    self.assertIs(ai_client._toolchain_provider_affinity.get(), own)
                ready += 1
                if ready == 2:
                    barrier.set()
                await barrier.wait()
                await self.complete()
                return own.provider_index

        self.assertEqual(await asyncio.gather(turn("gemini"), turn("generic_openai_compatible")), [0, 1])

    async def test_scope_is_closed_on_cancellation(self):
        ready = asyncio.Event()
        scopes = []

        async def turn():
            async with ai_client.toolchain_provider_affinity():
                await self.complete()
                scopes.append(ai_client._toolchain_provider_affinity.get())
                ready.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(turn())
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(scopes[0].active)
        self.assertIsNone(ai_client._toolchain_provider_affinity.get())

    async def test_live_loop_reports_effect_once_after_provider_failure(self):
        self.http.side_effect = [
            reply("discover_tools", {"names": ["send_message"]}, "discover"),
            reply(call_id="write"), (503, {}, "down"), (503, {}, "still down"),
        ]
        execute = AsyncMock(return_value={"status": "success", "result": "Сообщение отправлено."})
        answer = await run_agent_turn(
            REQUEST, schemas={"send_message": TOOLS[0]}, eligible_names=frozenset({"send_message"}),
            mutating_names=frozenset({"send_message"}), ask_user_schema=TOOLS[0],
            complete=ai_client.pos_chat_completion, execute=execute, is_current=lambda: True,
            prepare_text=lambda text: text, has_internal_syntax=lambda text: False,
        )
        self.assertIn("Сообщение отправлено", answer)
        self.assertIn("не смог", answer)
        execute.assert_awaited_once()
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 4)

    async def test_transient_retry_finishes_without_repeating_successful_effect(self):
        self.http.side_effect = [
            reply("discover_tools", {"names": ["send_message"]}, "discover"),
            reply(call_id="write"), (503, {}, "busy"),
            reply("finish_response", {"text": "Отправлено.", "outcome": "completed", "evidence_call_ids": ["write"]}, "finish"),
        ]
        execute = AsyncMock(return_value={"status": "success", "result": "Сообщение отправлено."})
        answer = await run_agent_turn(
            REQUEST, schemas={"send_message": TOOLS[0]}, eligible_names=frozenset({"send_message"}),
            mutating_names=frozenset({"send_message"}), ask_user_schema=TOOLS[0],
            complete=ai_client.pos_chat_completion, execute=execute, is_current=lambda: True,
            prepare_text=lambda text: text, has_internal_syntax=lambda text: False,
        )
        self.assertEqual(answer, "Отправлено.")
        execute.assert_awaited_once()
        calls = self.http.await_args_list
        self.assertEqual(calls[-2].kwargs["payload"], calls[-1].kwargs["payload"])
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 4)
