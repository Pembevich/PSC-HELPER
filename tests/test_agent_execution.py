"""Exercise the native model-driven loop without Discord or provider requests."""

import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock

from agent_runtime import run_agent_turn
from agent_tools import compact_tool_catalog, make_discovery_tool
from cogs.ai_tools import POS_AI_TOOLS


def _call(name, arguments, call_id, **metadata):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        **metadata,
    }


def _response(*calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(calls)}


def _discover(*names, call_id="discovery"):
    return _response(_call("discover_tools", {"names": list(names)}, call_id))


def _finish(text, outcome="answered", evidence=(), call_id="finish"):
    return _response(_call("finish_response", {
        "text": text, "outcome": outcome, "evidence_call_ids": list(evidence),
    }, call_id))


def _observations(messages):
    return {
        message["tool_call_id"]: json.loads(message["content"])
        for message in messages if message.get("role") == "tool"
    }


class AgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.schemas = {tool["function"]["name"]: copy.deepcopy(tool) for tool in POS_AI_TOOLS}
        self.eligible = frozenset({"list_members", "list_roles", "read_messages", "add_role", "remove_role"})
        self.mutating = frozenset({"add_role", "remove_role"})
        self.complete = AsyncMock()
        self.execute = AsyncMock(return_value={"status": "success", "result": "Роль выдана."})
        self.state = {}
        self.messages = [{"role": "system", "content": "Ты P.OS."},
                         {"role": "user", "content": "Найди участника и выдай роль."}]
        self.ask_user = {
            "type": "function",
            "function": {
                "name": "ask_user", "description": "Уточни цель.",
                "parameters": {
                    "type": "object", "properties": {"question": {"type": "string"}},
                    "required": ["question"], "additionalProperties": False,
                },
            },
        }

    async def run_turn(self, *responses, **overrides):
        if responses:
            self.complete.side_effect = list(responses)
        options = dict(
            schemas=self.schemas, eligible_names=self.eligible, mutating_names=self.mutating,
            ask_user_schema=self.ask_user, complete=self.complete, execute=self.execute,
            is_current=lambda: True, prepare_text=lambda text: text,
            has_internal_syntax=lambda _text: False, state=self.state, timeout=5.0,
        )
        options.update(overrides)
        return await run_agent_turn(self.messages, **options)

    def messages_seen(self, call_index=-1):
        return self.complete.await_args_list[call_index].args[0]

    async def test_discovery_read_write_and_verified_finish_use_real_results(self):
        self.execute.side_effect = [
            {"status": "success", "result": "Пользователь: 900000000000000001, роль: 900000000000000002."},
            {"status": "success", "result": "Участнику назначена роль Художник."},
        ]
        answer = await self.run_turn(
            _discover("list_members", "add_role"),
            _response(_call("list_members", {"query": "Ваня"}, "read-member")),
            _response(_call("add_role", {
                "user_id": "900000000000000001", "role_id_or_name": "900000000000000002",
            }, "write-role")),
            _finish("Выдал Ване роль Художник.", "completed", ["write-role"]),
        )
        self.assertEqual(answer, "Выдал Ване роль Художник.")
        self.assertEqual([c.args[0]["function"]["name"] for c in self.execute.await_args_list],
                         ["list_members", "add_role"])
        read_result = _observations(self.messages_seen(2))["read-member"]
        self.assertIn("900000000000000001", read_result["result"])
        self.assertIs(read_result["mutating"], False)
        self.assertEqual(_observations(self.messages_seen())["write-role"]["status"], "success")
        self.assertTrue(self.state["tools_executed"])
        self.assertEqual(len(self.state["tool_results"]), 2)

    async def test_verification_read_is_refetched_after_write(self):
        self.execute.side_effect = [
            {"status": "success", "result": "Участник 123: роли A нет."},
            {"status": "success", "result": "Участнику 123 назначена роль A."},
            {"status": "success", "result": "Участник 123: роль A присутствует."},
        ]
        answer = await self.run_turn(
            _discover("list_members", "add_role"),
            _response(_call("list_members", {"query": "123"}, "before")),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "assign")),
            _response(_call("list_members", {"query": "123"}, "verified")),
            _finish("Роль A выдана; наличие проверено.", "completed", ["assign", "verified"]),
        )
        self.assertEqual(answer, "Роль A выдана; наличие проверено.")
        self.assertEqual(self.execute.await_count, 3)
        observations = _observations(self.messages_seen())
        self.assertIn("нет", observations["before"]["result"])
        self.assertIn("присутствует", observations["verified"]["result"])
        self.assertFalse(observations["verified"]["duplicate"])

    async def test_model_can_chain_more_than_eight_independent_effects(self):
        calls = [
            _response(_call("add_role", {"user_id": str(900000000000000000 + i),
                                         "role_id_or_name": "Художник"}, f"role-{i}"))
            for i in range(9)
        ]
        answer = await self.run_turn(
            _discover("add_role"), *calls,
            _finish("Выдал роль девяти участникам.", "completed", [f"role-{i}" for i in range(9)]),
        )
        self.assertEqual(answer, "Выдал роль девяти участникам.")
        self.assertEqual(self.execute.await_count, 9)
        self.assertEqual(self.complete.await_count, 11)
        self.assertEqual(len(self.state["tool_results"]), 9)
        # Each model step receives the preceding completed operation.
        for index in range(1, 9):
            self.assertIn(f"role-{index - 1}", _observations(self.messages_seen(index + 1)))

    async def test_chat_finishes_without_actions_and_initial_payload_is_compact(self):
        answer = await self.run_turn(_finish("Привет!"), eligible_names=frozenset(self.schemas))
        self.assertEqual(answer, "Привет!")
        self.execute.assert_not_awaited()
        self.assertFalse(self.state.get("tools_executed", False))
        first_options = self.complete.await_args.kwargs
        initial_names = [tool["function"]["name"] for tool in first_options["tools"]]
        self.assertEqual(initial_names, ["discover_tools", "ask_user", "finish_response"])
        self.assertEqual(first_options["tool_choice"], "required")
        self.assertEqual(first_options["tools"][0], make_discovery_tool(frozenset(self.schemas)))
        catalog = compact_tool_catalog(self.schemas, frozenset(self.schemas), self.mutating)
        self.assertIn(catalog, self.messages_seen()[-1]["content"])
        self.assertIn("add_role [write]", catalog)
        self.assertNotIn('"role_id_or_name"', self.messages_seen()[-1]["content"])

    async def test_false_completion_before_action_is_rejected_then_model_executes(self):
        answer = await self.run_turn(
            _finish("Уже выдал роль.", "completed", call_id="unproved-finish"),
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "Художник"}, "actual-write")),
            _finish("Теперь роль действительно выдана.", "completed", ["actual-write"]),
        )
        self.assertEqual(answer, "Теперь роль действительно выдана.")
        self.execute.assert_awaited_once()
        observation = _observations(self.messages_seen(1))["unproved-finish"]
        self.assertEqual(observation["status"], "error")
        self.assertIn("не подтверждено", observation["result"])

    async def test_hidden_and_unloaded_tools_never_reach_executor(self):
        answer = await self.run_turn(
            _response(
                _call("add_role", {"user_id": "123", "role_id_or_name": "Художник"}, "unloaded"),
                _call("ban_user", {"user_id": "123"}, "hidden"),
            ),
            _discover("ban_user", call_id="hidden-discovery"),
            _finish("Запрошенная возможность сейчас недоступна.", "blocked", ["unloaded", "hidden"]),
        )
        self.assertIn("недоступна", answer)
        self.execute.assert_not_awaited()
        self.assertFalse(self.state.get("tools_executed", False))
        observations = _observations(self.messages_seen())
        for call_id in ["unloaded", "hidden", "hidden-discovery"]:
            self.assertEqual(observations[call_id]["status"], "error")
        for provider_call in self.complete.await_args_list:
            self.assertNotIn("ban_user", [t["function"]["name"] for t in provider_call.kwargs["tools"]])

    async def test_failed_pending_or_unknown_write_requires_partial_outcome(self):
        for status in ["error", "pending", "unknown"]:
            with self.subTest(status=status):
                self.complete.reset_mock()
                self.execute.reset_mock()
                self.state.clear()
                self.execute.side_effect = [
                    {"status": "success", "result": "Первая роль выдана."},
                    {"status": status, "result": "Вторая операция не подтверждена."},
                ]
                answer = await self.run_turn(
                    _discover("add_role"),
                    _response(
                        _call("add_role", {"user_id": "1", "role_id_or_name": "A"}, "done"),
                        _call("add_role", {"user_id": "2", "role_id_or_name": "A"}, "unconfirmed"),
                    ),
                    _finish("Все сделано.", "completed", ["done", "unconfirmed"], "wrong-summary"),
                    _finish("Первую роль выдал, вторую операцию подтвердить не удалось.", "partial",
                            ["done", "unconfirmed"]),
                )
                self.assertIn("вторую операцию подтвердить не удалось", answer)
                self.assertEqual(self.execute.await_count, 2)
                self.assertEqual(_observations(self.messages_seen())["wrong-summary"]["status"], "error")

    async def test_changed_request_stops_remaining_actions_and_preserves_first_receipt(self):
        binding = {"current": True}

        async def execute_once(_call):
            binding["current"] = False
            return {"status": "success", "result": "Первая роль выдана."}

        self.execute.side_effect = execute_once
        answer = await self.run_turn(
            _discover("add_role"),
            _response(
                _call("add_role", {"user_id": "1", "role_id_or_name": "A"}, "first"),
                _call("add_role", {"user_id": "2", "role_id_or_name": "A"}, "second"),
            ),
            is_current=lambda: binding["current"],
        )
        self.assertIn("Запрос изменился", answer)
        self.assertIn("Первая роль выдана", answer)
        self.execute.assert_awaited_once()
        self.assertEqual([r["call_id"] for r in self.state["tool_results"]], ["first"])

    async def test_timeout_and_exception_do_not_repeat_identical_write(self):
        for failure in [asyncio.TimeoutError(), RuntimeError("simulated transport failure")]:
            with self.subTest(failure=type(failure).__name__):
                self.complete.reset_mock()
                self.execute.reset_mock()
                self.execute.side_effect = failure
                self.state.clear()
                args = {"user_id": "123", "role_id_or_name": "Художник"}
                answer = await self.run_turn(
                    _discover("add_role"),
                    _response(_call("add_role", args, "uncertain")),
                    _response(_call("add_role", args, "repeated")),
                    _finish("Результат операции неизвестен; повторять её не стал.", "blocked",
                            ["uncertain", "repeated"]),
                )
                self.assertIn("неизвестен", answer)
                self.execute.assert_awaited_once()
                self.assertTrue(self.state["tools_executed"])
                observations = _observations(self.messages_seen())
                self.assertEqual(observations["uncertain"]["status"], "unknown")
                self.assertTrue(observations["repeated"]["duplicate"])
                self.assertEqual(observations["repeated"]["status"], "unknown")

    async def test_discovery_has_no_effect_and_does_not_modify_original_schemas_or_messages(self):
        original_schemas = copy.deepcopy(self.schemas)
        original_messages = copy.deepcopy(self.messages)
        answer = await self.run_turn(_discover("add_role"), _finish("Инструмент назначения роли доступен."))
        self.assertIn("доступен", answer)
        self.execute.assert_not_awaited()
        self.assertNotIn("tools_executed", self.state)
        self.assertNotIn("tool_results", self.state)
        self.assertEqual(self.schemas, original_schemas)
        self.assertEqual(self.messages, original_messages)
        tools = self.complete.await_args_list[1].kwargs["tools"]
        self.assertEqual([t["function"]["name"] for t in tools],
                         ["discover_tools", "ask_user", "finish_response", "add_role"])
        tools[-1]["function"]["parameters"]["properties"].clear()
        self.assertEqual(self.schemas, original_schemas)

    async def test_native_thought_signature_and_tool_call_identity_are_preserved(self):
        signature = {"google": {"thought_signature": "opaque-provider-signature"}}
        answer = await self.run_turn(
            _response(_call("discover_tools", {"names": ["read_messages"]}, "signed-call",
                            extra_content=signature)),
            _response(_call("read_messages", {"limit": "5"}, "read-latest", extra_content=signature)),
            _finish("Прочитал последние сообщения."),
        )
        self.assertEqual(answer, "Прочитал последние сообщения.")
        assistant_calls = [call for message in self.messages_seen() if message.get("role") == "assistant"
                           for call in message.get("tool_calls", [])]
        self.assertEqual([call["id"] for call in assistant_calls], ["signed-call", "read-latest"])
        self.assertTrue(all(call["extra_content"] == signature for call in assistant_calls))
        self.assertEqual(self.execute.await_args.args[0]["extra_content"], signature)
        self.assertEqual(set(_observations(self.messages_seen())), {"signed-call", "read-latest"})

    async def test_malformed_finish_outcome_is_rejected_without_crashing(self):
        for malformed_outcome in [[], {}, None, 1]:
            with self.subTest(outcome=malformed_outcome):
                self.complete.reset_mock()
                answer = await self.run_turn(
                    _finish("Привет!", malformed_outcome, call_id="bad-finish"),
                    _finish("Привет!"),
                )
                self.assertEqual(answer, "Привет!")
                self.assertEqual(_observations(self.messages_seen())["bad-finish"]["status"], "error")
                self.execute.assert_not_awaited()

    async def test_newly_discovered_tool_waits_until_model_has_seen_full_schema(self):
        answer = await self.run_turn(
            _response(
                _call("discover_tools", {"names": ["add_role"]}, "discover"),
                _call("add_role", {"user_id": "1", "role_id_or_name": "A"}, "too-early"),
            ),
            _response(_call("add_role", {"user_id": "1", "role_id_or_name": "A"}, "after-schema")),
            _finish("Роль выдана.", "partial", ["too-early", "after-schema"]),
        )
        self.assertEqual(answer, "Роль выдана.")
        self.execute.assert_awaited_once()
        self.assertEqual(self.execute.await_args.args[0]["id"], "after-schema")
        self.assertEqual(_observations(self.messages_seen())["too-early"]["status"], "error")

    async def test_completion_rejects_invented_or_missing_write_evidence(self):
        answer = await self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "real-write")),
            _finish("Роль выдана.", "completed", ["invented-call"], "invented-evidence"),
            _finish("Роль выдана.", "completed", [], "missing-evidence"),
            _finish("Роль выдана.", "answered", ["real-write"], "hidden-write-outcome"),
            _finish("Роль выдана.", "completed", ["real-write"]),
        )
        self.assertEqual(answer, "Роль выдана.")
        self.execute.assert_awaited_once()
        observations = _observations(self.messages_seen())
        for call_id in ["invented-evidence", "missing-evidence", "hidden-write-outcome"]:
            self.assertEqual(observations[call_id]["status"], "error")

    async def test_provider_failure_after_successful_effect_returns_receipt_without_retry(self):
        answer = await self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "write")),
            RuntimeError("provider unavailable"),
        )
        self.assertIn("AI не смог завершить ответ", answer)
        self.assertIn("Роль выдана", answer)
        self.execute.assert_awaited_once()
        self.assertTrue(self.state["tools_executed"])
        self.assertEqual(self.state["tool_results"][0]["status"], "success")

    async def test_invalid_json_control_calls_do_not_execute_or_terminate_the_turn(self):
        malformed_discovery = _call("discover_tools", {}, "bad-discovery")
        malformed_discovery["function"]["arguments"] = '{"names": ["add_role"'
        malformed_finish = _call("finish_response", {}, "bad-finish")
        malformed_finish["function"]["arguments"] = '{"text": "Готово",'
        answer = await self.run_turn(
            _response(malformed_discovery), _response(malformed_finish),
            _finish("Не удалось разобрать вызовы; изменений не было.", "blocked"),
        )
        self.assertIn("изменений не было", answer)
        self.execute.assert_not_awaited()
        self.assertFalse(self.state.get("tools_executed", False))
        observations = _observations(self.messages_seen())
        self.assertEqual(observations["bad-discovery"]["status"], "error")
        self.assertEqual(observations["bad-finish"]["status"], "error")

    async def test_invalid_or_non_object_domain_arguments_never_reach_executor(self):
        for raw in ['{"user_id": ', '[]', 'null', '"123"', '42']:
            with self.subTest(raw=raw):
                self.complete.reset_mock()
                self.execute.reset_mock()
                self.state.clear()
                malformed = _call("add_role", {}, "malformed-write")
                malformed["function"]["arguments"] = raw
                answer = await self.run_turn(
                    _discover("add_role"), _response(malformed),
                    _finish("Некорректные аргументы; роль не выдана.", "blocked", ["malformed-write"]),
                )
                self.assertIn("не выдана", answer)
                self.execute.assert_not_awaited()
                self.assertFalse(self.state.get("tools_executed", False))
                self.assertEqual(_observations(self.messages_seen())["malformed-write"]["status"], "error")
                self.assertEqual(self.state["tool_results"][0]["status"], "error")

    async def test_real_deadline_cancels_uncertain_write_and_preserves_no_replay_state(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_write(_call):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.execute.side_effect = slow_write
        answer = await self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "uncertain-write")),
            timeout=0.05,
        )
        self.assertTrue(entered.is_set())
        self.assertTrue(cancelled.is_set())
        self.execute.assert_awaited_once()
        self.assertTrue(self.state["tools_executed"])
        self.assertEqual(self.state["tool_results"][0]["status"], "unknown")
        self.assertIn("Не удалось подтвердить", answer)
        self.assertNotIn("Серверные изменения не выполнялись", answer)

    async def test_outer_cancellation_during_write_propagates_with_no_replay_marker(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_write(_call):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.execute.side_effect = pending_write
        task = asyncio.create_task(self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "write")),
        ))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertTrue(self.state["tools_executed"])
        self.execute.assert_awaited_once()
        self.assertEqual(self.complete.await_count, 2)

    async def test_large_tool_result_remains_valid_json_with_receipt_metadata(self):
        self.execute.return_value = {"status": "success", "result": '"\\\n' * 8000}
        answer = await self.run_turn(
            _discover("read_messages"),
            _response(_call("read_messages", {"limit": "50"}, "large-result")),
            _finish("Прочитал сообщения."),
        )
        self.assertEqual(answer, "Прочитал сообщения.")
        observation = _observations(self.messages_seen())["large-result"]
        self.assertEqual(observation["status"], "success")
        self.assertEqual(observation["name"], "read_messages")
        self.assertEqual(len(observation["result"]), 12000)
        self.assertFalse(observation["mutating"])

    async def test_duplicate_native_ids_cannot_overwrite_prior_observations(self):
        answer = await self.run_turn(
            _response(
                _call("discover_tools", {"names": ["list_members"]}, "duplicate"),
                _call("discover_tools", {"names": ["list_roles"]}, "pos-agent-1-0"),
            ),
            _response(_call("list_members", {}, "duplicate")),
            _finish("Список участников получен."),
        )
        self.assertIn("получен", answer)
        observations = _observations(self.messages_seen())
        self.assertEqual(set(observations), {"duplicate", "pos-agent-1-0", "pos-agent-1-0-duplicate"})
        self.assertEqual(self.execute.await_args.args[0]["id"], "pos-agent-1-0-duplicate")

    async def test_plaintext_actions_are_never_executed_or_accepted_as_completion(self):
        answer = await self.run_turn(
            {"role": "assistant", "content": 'add_role({"user_id": "123"}); Готово.'},
            _finish("Для назначения роли нужно уточнить участника.", "blocked"),
        )
        self.assertIn("уточнить", answer)
        self.execute.assert_not_awaited()
        self.assertFalse(self.state.get("tools_executed", False))

    async def test_pending_approval_cannot_be_described_as_completed(self):
        self.execute.return_value = {"status": "pending", "result": "Запрос передан владельцу на подтверждение."}
        answer = await self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "approval")),
            _finish("Роль выдана.", "completed", ["approval"], "premature-completion"),
            _finish("Ожидаю подтверждения владельца; роль ещё не выдана.", "blocked", ["approval"]),
        )
        self.assertIn("ещё не выдана", answer)
        self.assertEqual(_observations(self.messages_seen())["premature-completion"]["status"], "error")
        self.execute.assert_awaited_once()

    async def test_unloaded_write_refusal_is_part_of_partial_result(self):
        answer = await self.run_turn(
            _discover("add_role"),
            _response(_call("add_role", {"user_id": "123", "role_id_or_name": "A"}, "added")),
            _response(_call("remove_role", {"user_id": "123", "role_id_or_name": "B"}, "not-removed")),
            _finish("Роль A выдал и роль B снял.", "completed", ["added"], "ignored-refusal"),
            _finish("Роль A выдал; снять B не удалось.", "partial", ["added", "not-removed"]),
        )
        self.assertEqual(answer, "Роль A выдал; снять B не удалось.")
        self.execute.assert_awaited_once()
        self.assertEqual(_observations(self.messages_seen())["ignored-refusal"]["status"], "error")
        self.assertEqual([(item["call_id"], item["status"]) for item in self.state["tool_results"]],
                         [("added", "success"), ("not-removed", "error")])

    async def test_action_budget_refusal_cannot_disappear_from_completion_evidence(self):
        calls = [
            _call("add_role", {"user_id": str(index), "role_id_or_name": "A"}, f"write-{index}")
            for index in range(24)
        ]
        evidence = [call["id"] for call in calls] + ["over-budget"]
        answer = await self.run_turn(
            _discover("add_role"), _response(*calls),
            _response(_call("add_role", {"user_id": "24", "role_id_or_name": "A"}, "over-budget")),
            _finish("Все 25 ролей выданы.", "completed", evidence[:-1], "ignored-limit"),
            _finish("Выдал 24 роли; последняя операция не выполнена из-за лимита.", "partial", evidence),
        )
        self.assertEqual(answer, "Выдал 24 роли; последняя операция не выполнена из-за лимита.")
        self.assertEqual(self.execute.await_count, 24)
        self.assertEqual(_observations(self.messages_seen())["ignored-limit"]["status"], "error")
        self.assertEqual(self.state["tool_results"][-1]["call_id"], "over-budget")
        self.assertEqual(self.state["tool_results"][-1]["status"], "error")

    async def test_fallback_for_large_partial_results_is_bounded_and_factual(self):
        self.execute.return_value = {"status": "success", "result": "Роль выдана: " + "я" * 20000}
        answer = await self.run_turn(
            _discover("add_role"),
            _response(
                _call("add_role", {"user_id": "1", "role_id_or_name": "A"}, "first"),
                _call("add_role", {"user_id": "2", "role_id_or_name": "A"}, "second"),
            ),
            RuntimeError("provider unavailable"),
        )
        self.assertLessEqual(len(answer), 12000)
        self.assertIn("Роль выдана", answer)
        self.assertIn("AI не смог завершить ответ", answer)
        self.assertEqual(self.execute.await_count, 2)


if __name__ == "__main__":
    unittest.main()
