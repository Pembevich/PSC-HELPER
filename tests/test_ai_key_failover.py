import asyncio
import copy
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import ai_client
from agent_runtime import run_agent_turn


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL = "gemini-test"
REQUEST = [{"role": "user", "content": "Отправь одно тестовое сообщение."}]
SEND_MESSAGE = {"type": "function", "function": {
    "name": "send_message",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
}}


def provider(key, *, url=GEMINI_URL, model=MODEL, kind="gemini"):
    return {"name": f"route-{key}", "api_key": key, "api_url": url,
            "model": model, "provider": kind}


def tool_reply(name="send_message", args=None, call_id="write"):
    message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(
            {"text": "Проверка."} if args is None else args,
            ensure_ascii=False,
        )},
        "extra_content": {"google": {"thought_signature": f"opaque-{call_id}"}},
    }]}
    return 200, {}, json.dumps({"choices": [{"message": message}]})


def continuation(message):
    return REQUEST + [message, {"role": "tool", "tool_call_id": "write",
                                "content": '{"status":"success","message_id":"123"}'}]


class ProviderPoolDeduplicationTests(unittest.TestCase):
    def build_pool(self, keys, urls, models):
        with (
            patch.object(ai_client, "POS_AI_PROVIDER_KEYS", keys),
            patch.object(ai_client, "POS_AI_PROVIDER_URLS", urls),
            patch.object(ai_client, "POS_AI_PROVIDER_MODELS", models),
            patch.object(ai_client, "POS_AI_API_URL", GEMINI_URL),
            patch.object(ai_client, "POS_AI_MODEL", MODEL),
        ):
            return ai_client._build_provider_pool()

    def test_duplicate_credentials_do_not_create_independent_cooldowns(self):
        pool = self.build_pool(
            ["dummy-a", " dummy-a ", "dummy-b"],
            [GEMINI_URL] * 3,
            [MODEL, f" {MODEL} ", MODEL],
        )
        self.assertEqual([route["api_key"] for route in pool], ["dummy-a", "dummy-b"])
        self.assertEqual([route["model"] for route in pool], [MODEL, MODEL])

    def test_same_credential_for_different_model_or_endpoint_is_not_discarded(self):
        second_url = "https://another.example/chat/completions"
        pool = self.build_pool(
            ["dummy-a"] * 3,
            [GEMINI_URL, GEMINI_URL, second_url],
            [MODEL, "gemini-other", MODEL],
        )
        self.assertEqual([(route["api_url"], route["model"]) for route in pool], [
            (GEMINI_URL, MODEL), (GEMINI_URL, "gemini-other"), (second_url, MODEL),
        ])

    def test_default_url_and_model_are_used_before_deduplication(self):
        pool = self.build_pool(["dummy-a", "dummy-a", "dummy-b"], [], [])
        self.assertEqual([route["api_key"] for route in pool], ["dummy-a", "dummy-b"])

    def test_duplicate_keys_do_not_hide_misaligned_configuration(self):
        for urls, models in [([GEMINI_URL], [MODEL, MODEL]), ([GEMINI_URL] * 2, [MODEL])]:
            with self.subTest(url_count=len(urls), model_count=len(models)):
                self.assertEqual(self.build_pool(["dummy-a", "dummy-a"], urls, models), [])


class GeminiCredentialFailoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.providers = [provider("dummy-a"), provider("dummy-b")]
        self.http = AsyncMock(return_value=tool_reply())
        self.cooldowns = {}
        self.now = 1000.0
        real_sleep = asyncio.sleep

        async def advance_clock(delay):
            self.now += delay
            await real_sleep(0)

        self.sleep = AsyncMock(side_effect=advance_clock)
        self.enterContext(patch.object(ai_client, "time", SimpleNamespace(
            monotonic=lambda: self.now, time=time.time,
        )))
        self.enterContext(patch.object(ai_client.asyncio, "sleep", self.sleep))
        for name, value in [
            ("_AI_PROVIDER_POOL", self.providers), ("_provider_cursor", 0),
            ("_provider_backoff_until", self.cooldowns),
            ("_transient_provider_backoff_until", {}),
            ("_ai_backoff_until", 0), ("_ai_backoff_reason", ""),
            ("_provider_lock", asyncio.Lock()),
            ("_AI_REQUEST_SEMAPHORE", asyncio.Semaphore(4)),
            ("_post_ai_payload", self.http),
        ]:
            self.enterContext(patch.object(ai_client, name, value))
        self.enterContext(patch.object(ai_client.aiohttp, "TCPConnector"))
        self.enterContext(patch.object(ai_client.aiohttp, "ClientSession", return_value=AsyncMock()))

    async def complete(self, messages=None):
        return await asyncio.wait_for(ai_client.pos_chat_completion(
            REQUEST if messages is None else messages, tools=[SEND_MESSAGE],
        ), 2)

    def credentials(self):
        return [call.kwargs["headers"]["Authorization"] for call in self.http.await_args_list]

    def add_incompatible_routes(self):
        self.providers.extend([
            provider("dummy-model", model="gemini-other"),
            provider("dummy-url", url="https://different.example/chat"),
            provider("dummy-kind", kind="generic_openai_compatible"),
            provider("", model=MODEL),
        ])

    async def test_only_exact_gemini_endpoint_and_model_are_compatible(self):
        self.add_incompatible_routes()
        self.assertEqual(ai_client._compatible_toolchain_indices(0), frozenset({0, 1}))
        self.assertEqual(ai_client._compatible_toolchain_indices(4), frozenset({4}))
        self.assertEqual(ai_client._compatible_toolchain_indices(-1), frozenset())
        self.assertEqual(ai_client._compatible_toolchain_indices(99), frozenset())

    async def test_reservation_prefers_pin_then_healthy_untried_sibling(self):
        self.add_incompatible_routes()
        ai_client._provider_cursor = 3
        self.assertEqual(await ai_client._reserve_toolchain_provider_index(0), 0)
        self.cooldowns[0] = self.now + 75
        self.assertEqual(await ai_client._reserve_toolchain_provider_index(0), 1)
        self.assertIsNone(await ai_client._reserve_toolchain_provider_index(
            0, exclude_indices=frozenset({1}),
        ))

    async def test_long_rate_limit_uses_sibling_and_retains_signed_transcript(self):
        self.http.side_effect = [tool_reply(), (429, {"Retry-After": "75"}, "quota"), tool_reply()]
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            transcript = continuation(first)
            original = copy.deepcopy(transcript)
            self.assertIsNotNone(await self.complete(transcript))
            self.assertIsNone(ai_client.ai_completion_retry_delay())
            self.assertEqual(ai_client._toolchain_provider_affinity.get().provider_index, 1)
        self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-b"])
        self.assertEqual(ai_client._provider_cooldown_remaining(0), 75)
        self.sleep.assert_not_awaited()
        self.assertEqual(transcript, original)
        calls = self.http.await_args_list
        self.assertEqual(calls[-2].kwargs["payload"], calls[-1].kwargs["payload"])
        self.assertEqual(calls[-1].kwargs["payload"]["messages"], original)

    async def test_repeated_503_switches_credential_and_next_round_uses_it(self):
        self.http.side_effect = [tool_reply(), (503, {}, "busy"), (503, {}, "busy"), tool_reply(), tool_reply()]
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            self.assertIsNotNone(await self.complete(continuation(first)))
            self.cooldowns.clear()  # Returning capacity does not make the next step switch back.
            self.assertIsNotNone(await self.complete(continuation(first)))
        self.assertEqual(self.credentials(), [
            "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-b", "Bearer dummy-b",
        ])
        self.assertEqual(self.http.await_args_list[1].kwargs["payload"],
                         self.http.await_args_list[3].kwargs["payload"])

    async def test_cooling_pin_is_skipped_without_sending_another_request(self):
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            self.cooldowns[0] = self.now + 75
            self.assertIsNotNone(await self.complete(continuation(first)))
        self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-b"])

    async def test_exhausted_family_never_sends_signatures_to_different_model_or_provider(self):
        self.add_incompatible_routes()
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            await ai_client._mark_provider_backoff(0, 8, retryable=True)
            await ai_client._mark_provider_backoff(1, 75, retryable=True)
            self.assertIsNone(await self.complete(continuation(first)))
            self.assertEqual(ai_client.ai_completion_retry_delay(), 8)
        self.http.assert_awaited_once()
        self.assertFalse(ai_client.ai_is_temporarily_unavailable())

    async def test_family_retry_hint_does_not_come_from_an_unrelated_route(self):
        self.add_incompatible_routes()
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            await ai_client._mark_provider_backoff(0, 75, retryable=True)
            await ai_client._mark_provider_backoff(1, 75, retryable=True)
            await ai_client._mark_provider_backoff(2, 1, retryable=True)
            self.assertIsNone(await self.complete(continuation(first)))
            self.assertIsNone(ai_client.ai_completion_retry_delay())
        self.http.assert_awaited_once()

    async def test_short_sibling_cooldown_can_recover_a_long_cooling_pin(self):
        self.add_incompatible_routes()
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            await ai_client._mark_provider_backoff(0, 75, retryable=True)
            await ai_client._mark_provider_backoff(1, 8, retryable=True)
            self.assertIsNone(await self.complete(continuation(first)))
            self.assertEqual(ai_client.ai_completion_retry_delay(), 8)
            self.now += 8
            self.assertIsNotNone(await self.complete(continuation(first)))
        self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-b"])

    async def test_invalid_credential_can_use_sibling_without_retrying_failed_key(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.http.reset_mock()
                self.cooldowns.clear()
                ai_client._provider_cursor = 0
                self.http.side_effect = [tool_reply(), (status, {}, "access denied"), tool_reply()]
                async with ai_client.toolchain_provider_affinity():
                    first = await self.complete()
                    self.assertIsNotNone(await self.complete(continuation(first)))
                self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-b"])
                self.assertEqual(ai_client._provider_cooldown_remaining(0), 3600)

    async def test_request_errors_and_explicit_refusals_are_not_replayed_with_another_key(self):
        refused = (200, {}, json.dumps({"choices": [{"message": {
            "role": "assistant", "content": None, "refusal": "declined",
        }}]}))
        for failure in [(400, {}, "invalid signature"), (413, {}, "too large"),
                        (422, {}, "invalid payload"), refused]:
            with self.subTest(status=failure[0], body=failure[2]):
                self.http.reset_mock()
                self.cooldowns.clear()
                ai_client._provider_cursor = 0
                self.http.side_effect = [tool_reply(), failure]
                async with ai_client.toolchain_provider_affinity():
                    first = await self.complete()
                    self.assertIsNone(await self.complete(continuation(first)))
                    self.assertIsNone(ai_client.ai_completion_retry_delay())
                self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-a"])

    async def test_timeout_uses_compatible_credential_without_changing_request(self):
        self.http.side_effect = [tool_reply(), asyncio.TimeoutError(), asyncio.TimeoutError(), tool_reply()]
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            self.assertIsNotNone(await self.complete(continuation(first)))
        self.assertEqual(self.credentials(), [
            "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-b",
        ])
        self.assertEqual(self.http.await_args_list[1].kwargs["payload"],
                         self.http.await_args_list[-1].kwargs["payload"])

    async def test_cancelled_completion_does_not_try_backup_key(self):
        self.http.side_effect = [tool_reply(), asyncio.CancelledError()]
        async with ai_client.toolchain_provider_affinity():
            first = await self.complete()
            with self.assertRaises(asyncio.CancelledError):
                await self.complete(continuation(first))
        self.assertEqual(self.credentials(), ["Bearer dummy-a", "Bearer dummy-a"])

    async def test_failover_and_repeated_model_write_execute_mutation_only_once(self):
        self.http.side_effect = [
            tool_reply("discover_tools", {"names": ["send_message"]}, "discover"),
            tool_reply(call_id="write"),
            (429, {"Retry-After": "75"}, "quota"),
            tool_reply(call_id="repeated-write"),
            tool_reply("finish_response", {
                "text": "Сообщение отправлено один раз.", "outcome": "completed",
                "evidence_call_ids": ["write", "repeated-write"],
            }, "finish"),
        ]
        execute = AsyncMock(return_value={"status": "success", "result": "Отправлено сообщение 123."})
        answer = await run_agent_turn(
            REQUEST, schemas={"send_message": SEND_MESSAGE},
            eligible_names=frozenset({"send_message"}), mutating_names=frozenset({"send_message"}),
            ask_user_schema=SEND_MESSAGE, complete=ai_client.pos_chat_completion,
            execute=execute, is_current=lambda: True,
            prepare_text=lambda text: text, has_internal_syntax=lambda text: False,
        )
        self.assertEqual(answer, "Сообщение отправлено один раз.")
        execute.assert_awaited_once()
        self.assertEqual(self.credentials(), [
            "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-a", "Bearer dummy-b", "Bearer dummy-b",
        ])
        calls = self.http.await_args_list
        self.assertEqual(calls[2].kwargs["payload"], calls[3].kwargs["payload"])
        final_messages = calls[-1].kwargs["payload"]["messages"]
        repeated_result = next(message for message in final_messages
                               if message.get("tool_call_id") == "repeated-write")
        self.assertTrue(json.loads(repeated_result["content"])["duplicate"])
        signed_calls = [call for message in final_messages for call in message.get("tool_calls", [])]
        self.assertEqual([call["extra_content"]["google"]["thought_signature"] for call in signed_calls],
                         ["opaque-discover", "opaque-write", "opaque-repeated-write"])
