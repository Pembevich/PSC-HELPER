import asyncio
import json
import time
from types import SimpleNamespace
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
        self.now = 1000.0
        real_sleep = asyncio.sleep

        async def advance_clock(delay):
            self.now += delay
            await real_sleep(0)

        self.sleep = AsyncMock(side_effect=advance_clock)
        self.enterContext(patch.object(ai_client, "time", SimpleNamespace(monotonic=lambda: self.now, time=time.time)))
        self.enterContext(patch.object(ai_client.asyncio, "sleep", self.sleep))
        for name, value in [
            ("_AI_PROVIDER_POOL", self.providers), ("_provider_cursor", 0),
            ("_provider_backoff_until", self.cooldowns), ("_ai_backoff_until", 0),
            ("_transient_provider_backoff_until", {}),
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
        self.http.side_effect = [(503, {}, "temporary"), (503, {}, "still busy"), reply(), reply()]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNotNone(await self.complete())
            self.cooldowns.clear()  # Gemini is healthy again, but the generic transcript stays put.
            self.assertIsNotNone(await self.complete())
        self.assertEqual(self.routes(), [self.providers[index]["api_url"] for index in (0, 0, 1, 1)])
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
            reply(call_id="write"), *[(503, {}, "down")] * 4,
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
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 6)

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

    async def test_initial_transient_failure_retries_same_route_and_payload(self):
        self.http.side_effect = [(503, {}, "busy"), reply()]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNotNone(await self.complete())
            self.assertIsNone(ai_client.ai_completion_retry_delay())
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 2)
        self.assertEqual(self.http.await_args_list[0].kwargs, self.http.await_args_list[1].kwargs)

    async def test_transient_hint_survives_broken_fallback_and_wait_for_child(self):
        self.http.side_effect = [(503, {}, "busy"), (503, {}, "busy"), (404, {}, "model missing"), reply()]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
            self.assertEqual(ai_client.ai_completion_retry_delay(), 8.0)
            self.assertIsNone(ai_client._toolchain_provider_affinity.get().provider_index)
            self.now += 8
            self.assertIsNotNone(await self.complete())
            self.assertIsNone(ai_client.ai_completion_retry_delay())
        self.assertEqual(self.routes(), [self.providers[index]["api_url"] for index in (0, 0, 1, 0)])
        self.assertIsNone(ai_client.ai_completion_retry_delay())

    async def test_new_turn_can_wait_for_short_shared_transient_cooldown(self):
        self.http.side_effect = [(503, {}, "busy"), (503, {}, "busy"), (404, {}, "model missing")]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
            self.assertEqual(ai_client.ai_completion_retry_delay(), 8.0)
        self.assertEqual(self.http.await_count, 3)

    async def test_long_provider_backoff_is_not_shortened_by_a_later_transient_failure(self):
        await ai_client._mark_provider_backoff(0, 300)
        await ai_client._mark_provider_backoff(0, 8, retryable=True)
        await ai_client._mark_provider_backoff(1, 300)
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
            self.assertIsNone(ai_client.ai_completion_retry_delay())
        self.http.assert_not_awaited()

    async def test_permanent_errors_are_not_retried_or_given_retry_hints(self):
        self.providers[:] = self.providers[:1]
        for status in (400, 401, 403, 404, 413, 422, 501, 505):
            with self.subTest(status=status):
                self.cooldowns.clear()
                ai_client._ai_backoff_until = 0
                self.http.reset_mock()
                self.http.side_effect = None
                self.http.return_value = (status, {}, "{}")
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNone(await self.complete())
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.http.assert_awaited_once()

    async def test_long_rate_limit_and_overload_retry_after_do_not_hot_loop(self):
        self.providers[:] = self.providers[:1]
        for status in (429, 503):
            with self.subTest(status=status):
                self.cooldowns.clear()
                ai_client._ai_backoff_until = 0
                self.http.reset_mock()
                self.http.return_value = (status, {"Retry-After": "75"}, "busy")
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNone(await self.complete())
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.assertEqual(ai_client._provider_cooldown_remaining(0), 75)
                self.http.assert_awaited_once()

    async def test_short_explicit_rate_limit_waits_before_same_route_retry(self):
        self.http.side_effect = [(429, {"Retry-After": "1"}, "busy"), reply()]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNotNone(await self.complete())
        self.sleep.assert_awaited_once_with(1.0)
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 2)

    async def test_transient_hint_is_reset_by_a_subsequent_permanent_failure(self):
        self.providers[:] = self.providers[:1]
        self.http.side_effect = [(503, {}, "busy"), (503, {}, "busy"), (400, {}, "invalid")]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
            self.assertEqual(ai_client.ai_completion_retry_delay(), 8)
            self.now += 8
            self.assertIsNone(await self.complete())
            self.assertIsNone(ai_client.ai_completion_retry_delay())

    async def test_empty_transport_response_can_recover_without_switching_provider(self):
        bodies = ['<html>proxy error</html>', '{"choices": []}']
        bodies.extend(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content,
        }}]}) for content in (None, "", " \n ", [], [{"type": "text", "text": "  "}]))
        for body in bodies:
            with self.subTest(body=body):
                self.http.reset_mock()
                self.http.side_effect = [(200, {}, body), reply()]
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNotNone(await self.complete())
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 2)

    async def test_consecutive_empty_messages_return_a_transient_hint(self):
        self.providers[:] = self.providers[:1]
        for content in (None, " \n ", []):
            with self.subTest(content=content):
                self.http.reset_mock()
                self.http.return_value = (200, {}, json.dumps({"choices": [{"message": {
                    "role": "assistant", "content": content,
                }}]}))
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNone(await self.complete())
                    self.assertEqual(ai_client.ai_completion_retry_delay(), 0.5)
                self.assertEqual(self.http.await_count, 2)

    async def test_refusal_with_empty_message_never_retries_or_switches_provider(self):
        bodies = [
            {"choices": [{"finish_reason": "content_filter", "message": {
                "role": "assistant", "content": None,
            }}]},
            {"choices": [{"message": {
                "role": "assistant", "content": "", "refusal": "Request declined.",
            }}]},
            {"choices": [{"message": {"role": "assistant", "content": [
                {"type": "refusal", "refusal": "Request declined."},
            ]}}]},
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.http.reset_mock()
                self.http.return_value = (200, {}, json.dumps(body))
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNone(await self.complete())
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.http.assert_awaited_once()

    async def test_explicit_filtered_response_is_not_treated_as_transport_failure(self):
        self.providers[:] = self.providers[:1]
        bodies = [
            {"promptFeedback": {"blockReason": "SAFETY"}},
            {"candidates": [{"finishReason": "SAFETY"}]},
            {"choices": [{"finish_reason": "content_filter"}]},
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.http.reset_mock()
                self.http.return_value = (200, {}, json.dumps(body))
                async with ai_client.toolchain_provider_affinity():
                    self.assertIsNone(await self.complete())
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.http.assert_awaited_once()

    async def test_fallback_refusal_clears_an_earlier_transient_retry_hint(self):
        self.http.side_effect = [
            (503, {}, "busy"), (503, {}, "still busy"),
            (200, {}, json.dumps({"choices": [{"message": {
                "role": "assistant", "content": None, "refusal": "Request declined.",
            }}]})),
        ]
        async with ai_client.toolchain_provider_affinity():
            self.assertIsNone(await self.complete())
            self.assertIsNone(ai_client.ai_completion_retry_delay())
        self.assertEqual(self.routes(), [self.providers[index]["api_url"] for index in (0, 0, 1)])

    async def test_turn_level_retry_retains_tool_receipt_and_does_not_repeat_effect(self):
        self.http.side_effect = [
            reply("discover_tools", {"names": ["send_message"]}, "discover"),
            reply(call_id="write"), (503, {}, "busy"), (503, {}, "busy"),
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
        payloads = [call.kwargs["payload"] for call in self.http.await_args_list[-3:]]
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[1], payloads[2])
        self.assertTrue(any(message.get("tool_call_id") == "write" for message in payloads[2]["messages"]))
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 5)

    async def test_turn_level_retry_recovers_two_empty_messages_after_a_write(self):
        empty = (200, {}, json.dumps({"choices": [{"message": {
            "role": "assistant", "content": None,
        }}]}))
        self.http.side_effect = [
            reply("discover_tools", {"names": ["send_message"]}, "discover"),
            reply(call_id="write"), empty, empty,
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
        payloads = [call.kwargs["payload"] for call in self.http.await_args_list[-3:]]
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[1], payloads[2])
        self.assertEqual(self.routes(), [self.providers[0]["api_url"]] * 5)
