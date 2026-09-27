"""A failed native search must not discard a completed fallback research tool."""

import asyncio
import json
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

import ai_client
import web_research
from agent_runtime import run_agent_turn
from cogs.ai_tools import POS_AI_TOOLS


def _native_reply(name, arguments, call_id):
    message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
        "extra_content": {"google": {"thought_signature": "search-test-signature"}},
    }]}
    return 200, {}, json.dumps({"choices": [{"message": message}]})


class _GroundingResponse:
    def __init__(self, failure, headers):
        self.failure = failure
        self.status = failure if isinstance(failure, int) else 503
        self.headers = headers
        self.charset = "utf-8"
        self.content = SimpleNamespace(read=AsyncMock(return_value=b'{"error":"unavailable"}'))

    async def __aenter__(self):
        if isinstance(self.failure, Exception):
            raise self.failure
        return self

    async def __aexit__(self, *_args):
        return False


class _GroundingSession:
    def __init__(self, failure, headers):
        self.failure = failure
        self.headers = headers
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _GroundingResponse(self.failure, self.headers)


class GroundedSearchRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = {
            "name": "gemini-test", "provider": "gemini", "model": "gemini-test",
            "api_key": "synthetic-key",
            "api_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        }
        for name, value in [
            ("_AI_PROVIDER_POOL", [self.provider]),
            ("_provider_cursor", 0),
            ("_provider_lock", asyncio.Lock()),
            ("_provider_backoff_until", {}),
            ("_transient_provider_backoff_until", {}),
            ("_grounding_provider_backoff_until", {}),
            ("_ai_backoff_until", 0),
            ("_AI_REQUEST_SEMAPHORE", asyncio.Semaphore(4)),
        ]:
            self.enterContext(patch.object(ai_client, name, value, create=True))
        self.enterContext(patch.object(ai_client.aiohttp, "TCPConnector"))
        self.enterContext(patch.object(web_research, "_RESEARCH_CACHE", OrderedDict()))

    async def _run_fallback_workflow(self, failure, headers=None):
        session = _GroundingSession(failure, headers or {})
        self.enterContext(patch.object(ai_client.aiohttp, "ClientSession", return_value=session))
        source = web_research.SearchResult(
            title="Проверенный источник", url="https://example.com/verified",
            snippet="Подтверждённый факт", provider="fallback search",
        )
        search = self.enterContext(patch.object(
            web_research, "search_web", new=AsyncMock(return_value=([source], "fallback search")),
        ))
        self.enterContext(patch.object(
            web_research, "fetch_public_page", new=AsyncMock(return_value=web_research.FetchedPage(
                title=source.title, url=source.url, text=source.snippet,
            )),
        ))
        self.enterContext(patch.object(
            web_research, "_grounded_summary", new=AsyncMock(return_value="Подтверждённый факт [1]."),
        ))
        answer = "Подтверждённый факт — https://example.com/verified"
        http = self.enterContext(patch.object(ai_client, "_post_ai_payload", new=AsyncMock(side_effect=[
            _native_reply("discover_tools", {"names": ["research_web"]}, "discover"),
            _native_reply("research_web", {"query": "Проверяемый факт"}, "research"),
            _native_reply("finish_response", {
                "text": answer, "outcome": "answered", "evidence_call_ids": ["research"],
            }, "finish"),
        ])))
        receipts = []

        async def execute(call):
            arguments = json.loads(call["function"]["arguments"])
            result = await web_research.research_web(**arguments)
            receipts.append(result)
            # Search endpoint backoff must suppress a second search, while the
            # already pinned chat route remains usable for the final answer.
            self.assertIsNone(await ai_client.pos_gemini_grounded_search("Другой запрос"))
            return {"status": "success", "result": result}

        schema = next(tool for tool in POS_AI_TOOLS if tool["function"]["name"] == "research_web")
        result = await run_agent_turn(
            [{"role": "user", "content": "Найди проверяемый факт в интернете."}],
            schemas={"research_web": schema}, eligible_names=frozenset({"research_web"}),
            mutating_names=frozenset(), ask_user_schema=schema,
            complete=ai_client.pos_chat_completion, execute=execute, is_current=lambda: True,
            prepare_text=lambda text: text, has_internal_syntax=lambda text: False,
        )
        self.assertEqual(result, answer)
        search.assert_awaited_once()
        self.assertEqual(len(receipts), 1)
        self.assertIn(source.url, receipts[0])
        self.assertEqual(len(session.posts), 1)
        self.assertEqual([call.args[1] for call in http.await_args_list], [self.provider["api_url"]] * 3)
        self.assertEqual(ai_client._provider_cooldown_remaining(0), 0)
        final_messages = http.await_args_list[-1].kwargs["payload"]["messages"]
        tool_results = [json.loads(item["content"]) for item in final_messages if item.get("role") == "tool"]
        self.assertEqual(tool_results[-1]["result"], receipts[0])
        self.assertEqual(tool_results[-1]["call_id"], "research")
        return session

    async def test_grounding_503_keeps_fallback_result_and_finishes_pinned_chat(self):
        await self._run_fallback_workflow(503)

    async def test_grounding_timeout_keeps_fallback_result_and_finishes_pinned_chat(self):
        await self._run_fallback_workflow(asyncio.TimeoutError())

    async def test_grounding_connection_failure_keeps_fallback_result_and_finishes_pinned_chat(self):
        await self._run_fallback_workflow(aiohttp.ClientConnectionError("synthetic network failure"))

    async def test_grounding_quota_wait_is_local_to_search_endpoint(self):
        await self._run_fallback_workflow(429, {"Retry-After": "60"})
        self.assertGreater(ai_client._grounding_provider_backoff_until[0] - ai_client.time.monotonic(), 59)
