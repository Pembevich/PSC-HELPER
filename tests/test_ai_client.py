import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import ai_client
from ai_client import (
    _bounded_float,
    _bounded_int,
    _extract_gemini_grounded_result,
    _extract_interaction_text,
    _extract_gemini_text,
    _gemini_generate_content_url,
    _gemini_interactions_url,
    _is_safe_provider_url,
    _messages_have_visual_inputs,
    _parse_retry_after,
    ai_has_configured_media_provider,
    ai_provider_runtime_summary,
    extract_json_block,
)


class _ResponseContent:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    async def read(self, _limit: int) -> bytes:
        return self._raw


class _MediaResponse:
    def __init__(self, payload: dict):
        self.status = 200
        self.headers = {}
        self.charset = "utf-8"
        self.content = _ResponseContent(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _MediaSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _MediaResponse(self.payload)


class _RawContent:
    def __init__(self, raw: str):
        self._raw = raw.encode("utf-8")

    async def read(self, _limit: int) -> bytes:
        return self._raw


class _RawResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self.headers = {}
        self.charset = "utf-8"
        self.content = _RawContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


class ExtractJsonBlockTests(unittest.TestCase):
    def test_parses_plain_json(self):
        payload = extract_json_block('{"status":"ok","score":0.99}')
        self.assertEqual(payload, {"status": "ok", "score": 0.99})

    def test_parses_json_in_code_fence(self):
        payload = extract_json_block(
            """```json
            {"results":[{"url":"https://example.com","label":"allow"}]}
            ```"""
        )
        self.assertEqual(
            payload,
            {"results": [{"url": "https://example.com", "label": "allow"}]},
        )

    def test_parses_first_complete_object_when_provider_adds_another_object(self):
        payload = extract_json_block(
            'analysis {"decision":"tool"}\nextra {"ignored":true}'
        )
        self.assertEqual(payload, {"decision": "tool"})

    def test_returns_none_for_invalid_payload(self):
        self.assertIsNone(extract_json_block("not-json-at-all"))


class AIClientBoundaryTests(unittest.TestCase):
    def test_runtime_provider_summary_is_effective_and_secret_free(self):
        providers = [
            {
                "provider": "generic_openai_compatible",
                "model": "text-model",
                "api_key": "must-not-leak-text",
                "api_url": "https://text.example/v1/chat",
            },
            {
                "provider": "gemini",
                "model": "gemini-model\nforged-log",
                "api_key": "must-not-leak-gemini",
                "api_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            },
        ]

        with patch.object(ai_client, "_AI_PROVIDER_POOL", providers):
            summary = ai_provider_runtime_summary()

        self.assertEqual(
            summary,
            "gemini:gemini-model forged-log, "
            "generic_openai_compatible:text-model",
        )
        self.assertNotIn("must-not-leak", summary)
        self.assertNotIn("https://", summary)

    def test_visual_input_detection_requires_an_image_part(self):
        self.assertFalse(
            _messages_have_visual_inputs(
                [{"role": "user", "content": "Обычный текст"}]
            )
        )
        self.assertTrue(
            _messages_have_visual_inputs(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Что здесь?"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AA=="},
                            },
                        ],
                    }
                ]
            )
        )

    def test_native_gemini_function_call_is_normalized(self):
        message = ai_client._extract_message_from_payload(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "ban_user",
                                        "args": {"user_id": "123"},
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        )

        self.assertEqual(message["tool_calls"][0]["function"]["name"], "ban_user")
        self.assertEqual(
            json.loads(message["tool_calls"][0]["function"]["arguments"]),
            {"user_id": "123"},
        )

    def test_responses_api_function_call_is_normalized(self):
        message = ai_client._extract_message_from_payload(
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "list_roles",
                        "arguments": "{}",
                    }
                ]
            }
        )

        self.assertEqual(message["tool_calls"][0]["id"], "call-1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "list_roles")

    def test_provider_urls_require_https_except_loopback(self):
        self.assertTrue(_is_safe_provider_url("https://models.example.com/v1/chat"))
        self.assertTrue(_is_safe_provider_url("http://127.0.0.1:8080/v1/chat"))
        self.assertTrue(_is_safe_provider_url("http://localhost:8080/v1/chat"))
        self.assertFalse(_is_safe_provider_url("http://models.example.com/v1/chat"))
        self.assertFalse(_is_safe_provider_url("https://token@example.com/v1/chat"))
        self.assertFalse(_is_safe_provider_url("https://example.com/v1/chat#redirect"))
        self.assertFalse(_is_safe_provider_url("https://example.com:not-a-port/v1/chat"))

    def test_numeric_request_parameters_are_bounded(self):
        self.assertEqual(_bounded_int(-1, 10, 1, 100), 1)
        self.assertEqual(_bounded_int(1000, 10, 1, 100), 100)
        self.assertEqual(_bounded_int("invalid", 10, 1, 100), 10)
        self.assertEqual(_bounded_float(float("nan"), 0.5, 0.0, 1.0), 0.5)
        self.assertEqual(_bounded_float(float("inf"), 0.5, 0.0, 1.0), 0.5)
        self.assertEqual(_bounded_float(-4.0, 0.5, 0.0, 1.0), 0.0)

    def test_retry_after_is_finite_and_capped(self):
        self.assertEqual(_parse_retry_after({"Retry-After": "nan"}), None)
        self.assertEqual(_parse_retry_after({"Retry-After": "999999"}), 3600.0)

    def test_native_gemini_url_is_derived_without_putting_key_in_url(self):
        provider = {
            "api_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "model": "google/gemini-3.5-flash",
            "api_key": "secret",
        }

        endpoint = _gemini_generate_content_url(provider)

        self.assertEqual(
            endpoint,
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash:generateContent",
        )
        self.assertNotIn("secret", endpoint)

        interactions_endpoint = _gemini_interactions_url(provider)
        self.assertEqual(
            interactions_endpoint,
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertNotIn("secret", interactions_endpoint)

    def test_native_gemini_text_parser_joins_text_parts(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Первая часть."},
                            {"text": "Вторая часть."},
                        ]
                    }
                }
            ]
        }

        self.assertEqual(
            _extract_gemini_text(payload),
            "Первая часть.\nВторая часть.",
        )

    def test_grounded_result_uses_only_structured_sources(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    "Проверенный факт. Второй проверенный факт. "
                                    "https://invented.example/"
                                )
                            }
                        ]
                    },
                    "groundingMetadata": {
                        "webSearchQueries": ["проверенный запрос"],
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://source.example/article#part",
                                    "title": "Source",
                                }
                            }
                        ],
                        "groundingSupports": [
                            {
                                "segment": {
                                    "startIndex": 0,
                                    "endIndex": len("Проверенный факт.".encode("utf-8")),
                                    "text": "Проверенный факт.",
                                },
                                "groundingChunkIndices": [0],
                            },
                            {
                                "segment": {
                                    "startIndex": len("Проверенный факт. ".encode("utf-8")),
                                    "endIndex": len(
                                        "Проверенный факт. Второй проверенный факт.".encode(
                                            "utf-8"
                                        )
                                    ),
                                    "text": "Второй проверенный факт.",
                                },
                                "groundingChunkIndices": [0],
                            },
                        ],
                    },
                }
            ]
        }

        result = _extract_gemini_grounded_result(payload, max_sources=4)

        self.assertIsNotNone(result)
        self.assertEqual(
            result["sources"],
            [{"title": "Source", "url": "https://source.example/article"}],
        )
        self.assertGreaterEqual(result["answer"].count("[1]"), 2)
        self.assertNotIn("invented.example", result["sources"][0]["url"])

    def test_grounded_result_rejects_answer_without_supports(self):
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Неподтверждённый ответ."}]},
                    "groundingMetadata": {
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://source.example/",
                                    "title": "Source",
                                }
                            }
                        ],
                        "groundingSupports": [],
                    },
                }
            ]
        }

        self.assertIsNone(_extract_gemini_grounded_result(payload, max_sources=4))

    def test_grounding_offsets_are_interpreted_as_utf8_bytes(self):
        answer = "Русский подтверждённый факт."
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": answer}]},
                    "groundingMetadata": {
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://example.com/russian",
                                    "title": "Source",
                                }
                            }
                        ],
                        "groundingSupports": [
                            {
                                "segment": {
                                    "startIndex": 0,
                                    "endIndex": len(answer.encode("utf-8")),
                                },
                                "groundingChunkIndices": [0],
                            }
                        ],
                    },
                }
            ]
        }

        result = _extract_gemini_grounded_result(payload, max_sources=4)

        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], f"{answer} [1]")

    def test_interactions_text_parser_reads_model_output_steps(self):
        payload = {
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": "Точная расшифровка."},
                    ],
                }
            ],
        }
        self.assertEqual(
            _extract_interaction_text(payload),
            "Точная расшифровка.",
        )

    def test_native_media_capability_requires_authenticated_gemini(self):
        providers = [
            {"provider": "generic", "api_key": "key"},
            {"provider": "gemini", "api_key": ""},
        ]
        with patch.object(ai_client, "_AI_PROVIDER_POOL", providers):
            self.assertFalse(ai_has_configured_media_provider())

        providers.append({"provider": "gemini", "api_key": "configured"})
        with patch.object(ai_client, "_AI_PROVIDER_POOL", providers):
            self.assertTrue(ai_has_configured_media_provider())


class ChatProviderRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _provider(self, name, provider="generic_openai_compatible"):
        return {
            "name": name,
            "provider": provider,
            "api_url": "https://api.example.com/v1/chat/completions",
            "model": "test-model",
            "api_key": "test-key",
        }

    async def test_non_json_success_fails_over_to_next_provider(self):
        providers = [self._provider("broken"), self._provider("healthy")]
        session = _SequenceSession(
            [
                _RawResponse(200, "<html>temporary proxy page</html>"),
                _RawResponse(
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "Ответ резервного провайдера.",
                                    }
                                }
                            ]
                        }
                    ),
                ),
            ]
        )
        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", providers),
            patch.object(
                ai_client,
                "_reserve_provider_index",
                new=AsyncMock(side_effect=[0, 1]),
            ),
            patch.object(ai_client.aiohttp, "ClientSession", return_value=session),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_chat_completion(
                [{"role": "user", "content": "Привет"}]
            )

        self.assertEqual(result["content"], "Ответ резервного провайдера.")
        self.assertEqual(len(session.posts), 2)

    async def test_413_retries_once_with_bounded_visual_context(self):
        provider = self._provider("gemini", provider="gemini")
        messages = [
            {"role": "system", "content": "Правила"},
            *(
                {"role": "user", "content": f"Старая реплика {index}"}
                for index in range(40)
            ),
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Опиши все кадры"},
                    *(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + ("A" * 1000)
                            },
                        }
                        for _index in range(8)
                    ),
                ],
            },
        ]
        session = _SequenceSession(
            [
                _RawResponse(413, "request entity too large"),
                _RawResponse(
                    200,
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "Кадры проанализированы.",
                                    }
                                }
                            ]
                        }
                    ),
                ),
            ]
        )
        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", [provider]),
            patch.object(
                ai_client,
                "_reserve_exact_provider_index",
                new=AsyncMock(return_value=0),
            ),
            patch.object(ai_client.aiohttp, "ClientSession", return_value=session),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_chat_completion(messages)

        self.assertEqual(result["content"], "Кадры проанализированы.")
        self.assertEqual(len(session.posts), 2)
        retry_messages = session.posts[1][1]["json"]["messages"]
        self.assertLess(len(retry_messages), len(messages))
        retry_visuals = [
            part
            for part in retry_messages[-1]["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        ]
        self.assertLessEqual(len(retry_visuals), 4)


class ProviderRoutingRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.providers = []
        self.http = AsyncMock()
        self.success = (
            200,
            {},
            json.dumps({"choices": [{"message": {"content": "Ответ получен."}}]}),
        )
        for name, value in (
            ("_AI_PROVIDER_POOL", self.providers),
            ("_provider_cursor", 0),
            ("_provider_backoff_until", {}),
            ("_ai_backoff_until", 0.0),
            ("_ai_backoff_reason", ""),
            ("_provider_lock", asyncio.Lock()),
            ("_AI_REQUEST_SEMAPHORE", asyncio.Semaphore(2)),
            ("_post_ai_payload", self.http),
        ):
            self.enterContext(patch.object(ai_client, name, value))
        self.enterContext(patch.object(ai_client.aiohttp, "TCPConnector"))
        self.enterContext(
            patch.object(
                ai_client.aiohttp,
                "ClientSession",
                return_value=_SequenceSession([]),
            )
        )

    def _pool(self, *kinds):
        self.providers.extend(
            {
                "name": f"route-{index}",
                "provider": kind,
                "api_url": f"https://route-{index}.example.com/v1/chat/completions",
                "model": "test-model",
                "api_key": "test-key",
            }
            for index, kind in enumerate(kinds)
        )

    async def _chat(self, *, vision=False):
        content = (
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
            if vision
            else "Привет"
        )
        return await ai_client.pos_chat_completion([{"role": "user", "content": content}])

    def _requested_urls(self):
        return [call.args[1] for call in self.http.await_args_list]

    async def test_gemini_discovery_uses_supported_schema_without_weakening_local_validation(self):
        from agent_tools import make_discovery_tool, discover_tool_schemas
        tool = make_discovery_tool(frozenset({"add_role"}))
        self._pool("gemini")
        self.http.return_value = self.success
        await ai_client.pos_chat_completion([{"role": "user", "content": "Выдай роль"}], tools=[tool], tool_choice="required")
        payload = self.http.await_args.kwargs["payload"]
        self.assertEqual(payload["tool_choice"], "required")
        names = payload["tools"][0]["function"]["parameters"]["properties"]["names"]
        self.assertNotIn("uniqueItems", names)
        self.assertNotIn("enum", names["items"])
        self.assertEqual(tool["function"]["parameters"]["properties"]["names"]["items"]["enum"], ["add_role"])
        self.assertTrue(tool["function"]["parameters"]["properties"]["names"]["uniqueItems"])
        _schemas, error = discover_tool_schemas({"names": ["add_role", "add_role"]}, {}, frozenset({"add_role"}))
        self.assertIsNotNone(error)
        _schemas, error = discover_tool_schemas({"names": ["ban_user"]}, {}, frozenset({"add_role"}))
        self.assertIsNotNone(error)

    def test_schema_adapter_preserves_property_named_unique_items(self):
        tools = [{"function": {"parameters": {"type": "object", "properties": {
            "uniqueItems": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
        }}}}]
        adapted = ai_client._provider_tool_schemas(tools, {"provider": "gemini"})
        self.assertIn("uniqueItems", adapted[0]["function"]["parameters"]["properties"])
        self.assertNotIn("uniqueItems", adapted[0]["function"]["parameters"]["properties"]["uniqueItems"])
        self.assertEqual(ai_client._provider_tool_schemas(tools, {"provider": "github_models"}), tools)

    def test_nested_array_bounds_remain_local_and_are_described_on_gemini_wire(self):
        import copy
        from automation_rules import AUTOMATION_TOOLS
        tools = copy.deepcopy(AUTOMATION_TOOLS)
        original = copy.deepcopy(tools)
        adapted = ai_client._provider_tool_schemas(tools, {"provider": "gemini"})
        properties = adapted[0]["function"]["parameters"]["properties"]
        actions = properties["actions"]
        self.assertNotIn("maxItems", actions)
        self.assertIn("maxItems=8", actions["description"])
        fields = actions["items"]["properties"]["embed"]["properties"]["fields"]
        self.assertNotIn("maxItems", fields)
        self.assertIn("maxItems=10", fields["description"])
        self.assertEqual(actions["items"]["properties"]["type"]["enum"], ["send_message", "add_role", "remove_role"])
        self.assertEqual(tools, original)
        self.assertEqual(ai_client._provider_tool_schemas(tools, {"provider": "generic_openai_compatible"}), original)

    def test_schema_adapter_does_not_strip_properties_named_like_array_keywords(self):
        tools = [{"function": {"parameters": {"type": "object", "properties": {
            "maxItems": {"type": "integer"}, "minItems": {"type": "integer"},
        }}}}]
        adapted = ai_client._provider_tool_schemas(tools, {"provider": "gemini"})
        self.assertEqual(adapted, tools)

    def test_error_diagnostic_never_echoes_provider_content_or_secrets(self):
        body = '{"error":{"message":"Unknown uniqueItems in parameters; authorization=secret-token; private user message"}}'
        self.assertEqual(ai_client._upstream_error_diagnostic(body), "uniqueitems,parameters")
        self.assertEqual(ai_client._upstream_error_diagnostic("private-secret"), "unclassified")

    async def test_timeout_tries_healthy_lower_priority_provider(self):
        self._pool("gemini", "github_models")
        self.http.side_effect = [asyncio.TimeoutError(), self.success]

        result = await self._chat()

        self.assertEqual(result["content"], "Ответ получен.")
        self.assertEqual(
            self._requested_urls(), [provider["api_url"] for provider in self.providers]
        )

    async def test_request_specific_errors_try_each_route_once(self):
        self._pool("gemini", "gemini", "github_models")
        for status in (400, 413, 422):
            with self.subTest(status=status):
                ai_client._provider_cursor = 0
                self.http.reset_mock()
                self.http.side_effect = [(status, {}, "{}"), (status, {}, "{}"), self.success]

                result = await self._chat()

                self.assertEqual(result["content"], "Ответ получен.")
                self.assertEqual(
                    self._requested_urls(),
                    [provider["api_url"] for provider in self.providers],
                )
                self.assertEqual(ai_client._provider_backoff_until, {})

    async def test_failed_request_does_not_disable_routes_for_next_message(self):
        self._pool("gemini", "github_models")
        self.http.side_effect = [(400, {}, "{}"), (400, {}, "{}"), self.success]

        self.assertIsNone(await self._chat())
        self.assertFalse(ai_client.ai_is_temporarily_unavailable())
        result = await self._chat()

        self.assertEqual(result["content"], "Ответ получен.")
        self.assertEqual(
            self._requested_urls(),
            [self.providers[index]["api_url"] for index in (0, 1, 0)],
        )

    async def test_vision_failure_does_not_pause_healthy_text_provider(self):
        self._pool("gemini", "github_models")
        for status in (429, 503):
            with self.subTest(status=status):
                ai_client._provider_backoff_until.clear()
                ai_client._provider_cursor = 0
                ai_client._ai_backoff_until = 0.0
                self.http.reset_mock()
                self.http.side_effect = [(status, {"Retry-After": "60"}, "{}"), self.success]

                self.assertIsNone(await self._chat(vision=True))
                self.assertFalse(ai_client.ai_is_temporarily_unavailable())
                result = await self._chat()

                self.assertEqual(result["content"], "Ответ получен.")
                self.assertEqual(
                    self._requested_urls(),
                    [provider["api_url"] for provider in self.providers],
                )

    async def test_unavailable_vision_routes_leave_text_available(self):
        self._pool("gemini", "github_models")
        ai_client._provider_backoff_until[0] = ai_client.time.monotonic() + 60
        self.http.return_value = self.success

        self.assertIsNone(await self._chat(vision=True))
        self.http.assert_not_awaited()
        self.assertFalse(ai_client.ai_is_temporarily_unavailable())
        result = await self._chat()

        self.assertEqual(result["content"], "Ответ получен.")
        self.assertEqual(self._requested_urls(), [self.providers[1]["api_url"]])

    async def test_global_pause_requires_every_provider_to_be_limited(self):
        self._pool("gemini", "github_models")
        self.http.side_effect = [(429, {"Retry-After": "60"}, "{}")] * 2

        self.assertIsNone(await self._chat())
        self.assertTrue(ai_client.ai_is_temporarily_unavailable())
        self.assertEqual(self.http.await_count, 2)
        self.assertIsNone(await self._chat())
        self.assertEqual(self.http.await_count, 2)

    async def test_vision_failure_never_sends_images_to_text_only_route(self):
        self._pool("gemini", "github_models")
        self.http.return_value = (400, {}, "{}")

        self.assertIsNone(await self._chat(vision=True))

        self.assertEqual(self._requested_urls(), [self.providers[0]["api_url"]])
        self.assertFalse(ai_client.ai_is_temporarily_unavailable())


class ToolConversationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_native_gemini_signature_survives_response_conversion(self):
        for field in ("thoughtSignature", "thought_signature"):
            with self.subTest(field=field):
                response = ai_client._extract_message_from_payload({
                    "candidates": [{"content": {"role": "model", "parts": [{
                        "functionCall": {"name": "read_messages", "args": {"limit": "2"}},
                        field: "opaque-provider-signature",
                    }]}}],
                })

                call = response["tool_calls"][0]
                self.assertEqual(call["extra_content"]["google"]["thought_signature"], "opaque-provider-signature")
                self.assertEqual(json.loads(call["function"]["arguments"]), {"limit": "2"})

    async def test_413_compaction_preserves_current_tool_calls_and_observations(self):
        signature = {"google": {"thought_signature": "opaque-provider-signature"}}
        call = {
            "id": "step-0", "type": "function", "extra_content": signature,
            "function": {"name": "read_messages", "arguments": '{"limit":"2"}'},
        }
        current_turn = [
            {"role": "user", "content": "Найди нужное сообщение и удали его."},
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "step-0", "content": '{"message_id":"123456789012345678"}'},
        ]
        messages = [
            {"role": "system", "content": "Проверяй результат каждого вызова."},
            *[{"role": "user", "content": f"Предыдущая реплика {index}"} for index in range(40)],
            *current_turn,
        ]
        provider = {"name": "gemini", "provider": "gemini", "api_url": "https://api.example.com/v1/chat/completions", "model": "test-model", "api_key": "test-key"}
        session = _SequenceSession([
            _RawResponse(413, "request entity too large"),
            _RawResponse(200, json.dumps({"choices": [{"message": {"content": "Цель проверена."}}]})),
        ])
        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", [provider]),
            patch.object(ai_client, "_ai_backoff_until", 0.0),
            patch.object(ai_client, "_provider_backoff_until", {}),
            patch.object(ai_client.aiohttp, "ClientSession", return_value=session),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_chat_completion(messages)

        self.assertEqual(result["content"], "Цель проверена.")
        self.assertEqual(len(session.posts), 2)
        retry_messages = session.posts[1][1]["json"]["messages"]
        self.assertLess(len(retry_messages), len(messages))
        self.assertEqual(retry_messages[-3:], current_turn)


class GeminiMediaRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_grounded_search_uses_native_tool_and_header_key(self):
        provider = {
            "name": "gemini-search",
            "provider": "gemini",
            "api_url": (
                "https://generativelanguage.googleapis.com/"
                "v1beta/openai/chat/completions"
            ),
            "model": "google/gemini-3.1-flash-lite",
            "api_key": "search-secret",
        }
        session = _MediaSession(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Актуальный факт."}]},
                        "groundingMetadata": {
                            "webSearchQueries": ["актуальный факт"],
                            "groundingChunks": [
                                {
                                    "web": {
                                        "uri": "https://example.com/fact",
                                        "title": "Example",
                                    }
                                }
                            ],
                            "groundingSupports": [
                                {
                                    "segment": {
                                        "startIndex": 0,
                                        "endIndex": len(
                                            "Актуальный факт.".encode("utf-8")
                                        ),
                                        "text": "Актуальный факт.",
                                    },
                                    "groundingChunkIndices": [0],
                                }
                            ],
                        },
                    }
                ]
            }
        )

        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", [provider]),
            patch.object(
                ai_client,
                "_reserve_exact_provider_index",
                new=AsyncMock(return_value=0),
            ),
            patch.object(ai_client.aiohttp, "ClientSession", return_value=session),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_gemini_grounded_search("Что произошло?")

        self.assertIsNotNone(result)
        endpoint, kwargs = session.posts[0]
        self.assertTrue(endpoint.endswith("gemini-3.1-flash-lite:generateContent"))
        self.assertNotIn("search-secret", endpoint)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "search-secret")
        self.assertEqual(kwargs["json"]["tools"], [{"google_search": {}}])

    async def test_visual_chat_never_falls_back_to_text_only_provider(self):
        providers = [
            {
                "name": "text-only",
                "provider": "generic",
                "api_url": "https://api.example.com/v1/chat/completions",
                "model": "text-model",
                "api_key": "text-key",
            },
            {
                "name": "gemini-test",
                "provider": "gemini",
                "api_url": (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/openai/chat/completions"
                ),
                "model": "gemini-test",
                "api_key": "gemini-key",
            },
        ]
        session = _MediaSession(
            {"choices": [{"message": {"role": "assistant", "content": "Фото."}}]}
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Опиши."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AA=="},
                    },
                ],
            }
        ]

        reserve_exact = AsyncMock(return_value=1)
        reserve_regular = AsyncMock(return_value=0)
        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", providers),
            patch.object(
                ai_client,
                "_reserve_exact_provider_index",
                new=reserve_exact,
            ),
            patch.object(
                ai_client,
                "_reserve_provider_index",
                new=reserve_regular,
            ),
            patch.object(ai_client.aiohttp, "ClientSession", return_value=session),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_chat_completion(messages)

        self.assertEqual(result["content"], "Фото.")
        reserve_exact.assert_awaited_once_with("gemini", exclude_indices=frozenset())
        reserve_regular.assert_not_awaited()
        self.assertEqual(session.posts[0][0], providers[1]["api_url"])

    async def test_video_uses_interactions_payload_and_header_key(self):
        provider = {
            "name": "gemini-test",
            "provider": "gemini",
            "api_url": (
                "https://generativelanguage.googleapis.com/"
                "v1beta/openai/chat/completions"
            ),
            "model": "google/gemini-3.5-flash",
            "api_key": "secret-key",
        }
        session = _MediaSession(
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "text", "text": "Видео проверено."},
                        ],
                    }
                ],
            }
        )

        with (
            patch.object(ai_client, "_AI_PROVIDER_POOL", [provider]),
            patch.object(
                ai_client,
                "_reserve_exact_provider_index",
                new=AsyncMock(return_value=0),
            ),
            patch.object(
                ai_client.aiohttp,
                "ClientSession",
                return_value=session,
            ),
            patch.object(ai_client.aiohttp, "TCPConnector"),
        ):
            result = await ai_client.pos_gemini_media_analysis(
                b"\x00\x00\x00\x18ftypmp42",
                "video/mp4",
                prompt="Опиши видео.",
            )

        self.assertEqual(result, "Видео проверено.")
        self.assertEqual(len(session.posts), 1)
        endpoint, kwargs = session.posts[0]
        self.assertEqual(
            endpoint,
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertNotIn("secret-key", endpoint)
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "secret-key")
        self.assertEqual(kwargs["json"]["model"], "gemini-3.5-flash")
        self.assertEqual(kwargs["json"]["input"][0]["type"], "video")
        self.assertEqual(kwargs["json"]["input"][0]["mime_type"], "video/mp4")


if __name__ == "__main__":
    unittest.main()
