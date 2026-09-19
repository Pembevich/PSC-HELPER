"""Exercise the model/tool protocol without contacting Discord or AI providers."""
import copy
import json
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

import pos_ai
import tool_router
import storage
import automation_rules
from tool_router import ToolIntentPlan


def _call(name, arguments, *, call_id="provider-reused-id", **metadata):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
        **metadata,
    }


def _response(*calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(calls)}


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(id=900000000000000001, bot=True, name="P.OS", display_name="P.OS")
        self.owner = SimpleNamespace(id=pos_ai.POS_CREATOR_ID, bot=False, name="pumba", display_name="Pumba")
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 900000000000000002
        self.channel.name = "general"
        self.channel.threads = []
        self.channel.fetch_message = AsyncMock()
        self.channel.purge = AsyncMock(return_value=[])

        async def empty_history(**_kwargs):
            if False:
                yield None

        self.channel.history = empty_history
        self.guild = SimpleNamespace(
            id=900000000000000003,
            name="Test guild",
            me=SimpleNamespace(id=self.bot_user.id, guild_permissions=discord.Permissions.all()),
            channels=[self.channel], text_channels=[self.channel], threads=[], members=[], roles=[],
            get_channel=lambda ident: self.channel if ident == self.channel.id else None,
            get_thread=lambda _ident: None,
            get_channel_or_thread=lambda ident: self.channel if ident == self.channel.id else None,
        )
        self.channel.guild = self.guild
        self.bot = SimpleNamespace(
            user=self.bot_user, guilds=[self.guild],
            get_guild=lambda ident: self.guild if ident == self.guild.id else None,
        )
        self.message = SimpleNamespace(
            id=900000000000000099, author=self.owner,
            content="P.OS, найди нужное сообщение и удали его",
            guild=self.guild, channel=self.channel,
            mentions=[], raw_mentions=[], role_mentions=[], channel_mentions=[],
            reference=None, attachments=[],
        )
        self.state = {}
        # Real executor tests retain policy and dispatch; only persistence is stubbed.
        self.enterContext(patch.object(pos_ai, "_log_pos_tool_result", new=AsyncMock(return_value=True)))
        self.enterContext(patch.object(pos_ai, "capture_pre_state", new=AsyncMock(return_value={})))
        self.enterContext(patch.object(pos_ai, "record_pos_tool_action", new=AsyncMock()))

    async def _run(self, allowed, provider, *, executor=None, plan=None):
        with patch.object(pos_ai, "pos_chat_completion", new=provider):
            if executor is not None:
                self.enterContext(patch.object(pos_ai, "execute_pos_tool", new=executor))
            return await pos_ai.request_pos_reply(
                self.bot, self.message,
                [{"role": "user", "content": self.message.content}],
                state=self.state,
                tool_plan=plan or ToolIntentPlan.for_tools(self.message, set(allowed)),
            )

    async def test_read_observation_drives_write_then_final_response(self):
        target_id = "900000000000000010"
        observations = []
        signature = {"google": {"thought_signature": "opaque-provider-signature"}}

        async def complete(conversation, **kwargs):
            observations.append(copy.deepcopy((conversation, kwargs)))
            if len(observations) == 1:
                return _response(_call("read_messages", {"query": "нужное"}, extra_content=signature))
            last_result = json.loads(conversation[-1]["content"])["result"]
            if len(observations) == 2:
                self.assertIn(target_id, last_result)
                self.assertEqual(conversation[-2]["tool_calls"][0]["extra_content"], signature)
                return _response(_call("manage_message", {"action": "delete", "message_id": target_id}))
            self.assertIn("Удалено", last_result)
            return {"content": "Нужное сообщение удалено."}

        executor = AsyncMock(side_effect=[f"Найдено сообщение ID {target_id}.", "Удалено одно сообщение."])
        result = await self._run({"read_messages", "manage_message"}, AsyncMock(side_effect=complete), executor=executor)

        self.assertEqual(result, "Нужное сообщение удалено.")
        self.assertEqual([item.args[2]["function"]["name"] for item in executor.await_args_list], ["read_messages", "manage_message"])
        self.assertEqual([item[1]["tool_choice"] for item in observations], ["required", "auto", "auto"])
        tool_messages = [item for item in observations[-1][0] if item["role"] == "tool"]
        assistant_calls = [call for item in observations[-1][0] for call in item.get("tool_calls", [])]
        self.assertEqual([item["tool_call_id"] for item in tool_messages], [call["id"] for call in assistant_calls])
        self.assertEqual(len({call["id"] for call in assistant_calls}), 2)
        self.assertTrue(self.state["tools_executed"])

    async def test_capability_plan_does_not_force_unneeded_reads(self):
        executor = AsyncMock(return_value="Канал создан.")
        provider = AsyncMock(side_effect=[_response(_call("create_channel", {"name": "release"})), {"content": "Канал release создан."}])

        result = await self._run({"list_channels", "create_channel"}, provider, executor=executor)

        self.assertIn("release", result)
        executor.assert_awaited_once()
        self.assertEqual(executor.await_args.args[2]["function"]["name"], "create_channel")

    async def test_prose_and_textual_pseudo_calls_never_execute(self):
        self.message.content = "P.OS, забань <@900000000000000050>"
        self.message.mentions = [SimpleNamespace(id=900000000000000050)]
        for text in ("Готово, забанил.", 'tool_call: ban_user(user_id="900000000000000050")', '{"tool":"ban_user","arguments":{"user_id":"900000000000000050"}}'):
            with self.subTest(text=text):
                executor = AsyncMock()
                provider = AsyncMock(return_value={"content": text})
                result = await self._run({"ban_user"}, provider, executor=executor)
                executor.assert_not_awaited()
                self.assertIn("ничего не выполнено", result.lower())
                self.assertLessEqual(provider.await_count, 2)

    async def test_duplicate_calls_across_rounds_share_normalized_identity(self):
        executor = AsyncMock(return_value="Удалено сообщений: 2.")
        provider = AsyncMock(side_effect=[
            _response(_call("delete_messages", {"count": 2})),
            _response(_call("delete_messages", {"count": "2"})),
            {"content": "Удалены два сообщения."},
        ])

        result = await self._run({"delete_messages"}, provider, executor=executor)

        executor.assert_awaited_once()
        self.assertEqual(len(self.state["tool_results"]), 1)
        self.assertEqual(result, "Удалены два сообщения.")

    async def test_missing_final_response_returns_receipt_without_replaying_action(self):
        executor = AsyncMock(return_value="Удалено сообщений: 2.")
        provider = AsyncMock(side_effect=[_response(_call("delete_messages", {"count": "2"})), None])

        result = await self._run({"delete_messages"}, provider, executor=executor)

        executor.assert_awaited_once()
        self.assertTrue(self.state["tools_executed"])
        self.assertIn("Удалено сообщений: 2.", result)

    async def test_oversized_batch_is_bounded_and_cannot_claim_full_success(self):
        executor = AsyncMock(return_value="Тестовое сообщение отправлено.")
        provider = AsyncMock(side_effect=[
            _response(*[_call("send_message", {"text": f"test-{index}"}) for index in range(9)]),
            {"content": "Отправлены все девять сообщений."},
        ])

        result = await self._run({"send_message"}, provider, executor=executor)

        self.assertEqual(executor.await_count, 8)
        self.assertIn("часть операций отклонена", result)
        self.assertNotIn("все девять", result)

    async def test_action_limit_applies_across_model_rounds(self):
        executor = AsyncMock(return_value="Тестовое сообщение отправлено.")
        rounds = [
            _response(*[_call("send_message", {"text": f"test-{batch}-{index}"}) for index in range(2)])
            for batch in range(5)
        ]
        provider = AsyncMock(side_effect=[*rounds, {"content": "Все десять сообщений отправлены."}])

        result = await self._run({"send_message"}, provider, executor=executor)

        self.assertEqual(executor.await_count, 8)
        self.assertIn("оставшиеся операции не выполнены", result)
        self.assertNotIn("Все десять", result)

    async def test_ask_user_clarifies_without_side_effects(self):
        executor = AsyncMock()
        provider = AsyncMock(return_value=_response(_call("ask_user", {"question": "Какое сообщение удалить?"})))

        result = await self._run({"manage_message"}, provider, executor=executor)

        self.assertEqual(result, "Какое сообщение удалить?")
        executor.assert_not_awaited()
        self.assertFalse(self.state.get("tools_executed", False))

    async def test_ask_user_after_action_keeps_partial_result_visible(self):
        executor = AsyncMock(return_value="Канал release создан.")
        provider = AsyncMock(side_effect=[
            _response(_call("create_channel", {"name": "release"})),
            _response(_call("ask_user", {"question": "Какое сообщение перенести в новый канал?"})),
        ])

        result = await self._run({"create_channel", "send_message"}, provider, executor=executor)

        self.assertIn("Канал release создан.", result)
        self.assertIn("Какое сообщение перенести", result)
        executor.assert_awaited_once()

    async def test_plan_is_rechecked_before_followup_mutation(self):
        executor = AsyncMock(return_value="Найдено сообщение 900000000000000010.")
        rounds = 0

        async def complete(_conversation, **_kwargs):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return _response(_call("read_messages", {}))
            self.message.content = "P.OS, ничего не удаляй"
            return _response(_call("manage_message", {"action": "delete", "message_id": "900000000000000010"}))

        result = await self._run({"read_messages", "manage_message"}, AsyncMock(side_effect=complete), executor=executor)

        executor.assert_awaited_once()
        self.assertIn("остановлено", result)

    async def test_unselected_native_tool_does_not_expand_plan(self):
        executor = AsyncMock(return_value="Найдено одно сообщение.")
        provider = AsyncMock(side_effect=[
            _response(_call("read_messages", {}), _call("ban_user", {"user_id": "900000000000000050"})),
            {"content": "Найдено одно сообщение."},
        ])

        await self._run({"read_messages"}, provider, executor=executor)

        executor.assert_awaited_once()
        self.assertEqual(executor.await_args.args[2]["function"]["name"], "read_messages")
        self.assertEqual(executor.await_args.kwargs["allowed_tool_names"], frozenset({"read_messages"}))

    async def test_actor_capabilities_override_handmade_plan(self):
        self.message.author = SimpleNamespace(id=900000000000000080)
        provider = AsyncMock(side_effect=[_response(_call("read_messages", {})), {"content": "У тебя нет доступа к этой операции."}])
        performer = AsyncMock()

        with patch.object(pos_ai, "_perform_tool_action", new=performer):
            result = await self._run({"read_messages"}, provider)

        performer.assert_not_awaited()
        self.assertEqual(result, self.state["tool_results"][0]["result"])
        self.assertIn("недоступна", self.state["tool_results"][0]["result"])

    async def test_stale_plan_is_rejected_before_model_request(self):
        plan = ToolIntentPlan.for_tools(self.message, {"manage_message"})
        self.message.content = "P.OS, отмена"
        provider = AsyncMock()

        result = await self._run({"manage_message"}, provider, plan=plan)

        provider.assert_not_awaited()
        self.assertIn("не привязано", result)

    async def test_reply_to_pos_routes_and_deletes_exact_message(self):
        self.message.content = "P.OS, удали это сообщение"
        target = Mock(spec=discord.Message)
        target.id = 900000000000000010
        target.author = self.bot_user
        target.channel = self.channel
        target.guild = self.guild
        target.content = "Старый ответ P.OS."
        target.delete = AsyncMock()
        self.message.reference = SimpleNamespace(message_id=target.id, channel_id=self.channel.id, resolved=target)
        self.channel.fetch_message.return_value = target
        route = _response(_call("route_pos_request", {
            "decision": "tool", "tool_names": ["manage_message"], "confidence": 0.98,
            "explicit_request": True, "contextual_followup": False, "reason_code": "direct_request",
        }))
        planner = AsyncMock(return_value=route)
        with patch.object(tool_router, "pos_chat_completion", new=planner), patch.object(pos_ai, "list_recent_pos_tool_actions", new=AsyncMock(return_value=[])):
            plan, context, _journal = await pos_ai._plan_tool_intent_for_message(self.message, self.bot, target)

        references = json.loads(context.split("\n[SAME_USER_CONTEXT]", 1)[0].split("\n", 1)[1])
        self.assertEqual(references["reply"]["message_id"], str(target.id))
        self.assertTrue(references["reply"]["author_is_pos"])
        planner.assert_not_awaited()
        self.assertEqual(plan.decision, "agent")
        self.assertIn("manage_message", plan.tool_names)
        provider = AsyncMock(side_effect=[
            _response(_call("discover_tools", {"names": ["manage_message"]}, call_id="discover")),
            _response(_call("manage_message", {"action": "delete", "message_id": "reply"}, call_id="delete")),
            _response(_call("finish_response", {"text": "Этот ответ удалён.", "outcome": "completed", "evidence_call_ids": ["delete"]}, call_id="finish")),
        ])

        result = await self._run(plan.tool_names, provider, plan=plan)

        target.delete.assert_awaited_once()
        self.channel.fetch_message.assert_awaited_once_with(target.id)
        self.channel.purge.assert_not_awaited()
        self.assertEqual(result, "Этот ответ удалён.")

    async def test_native_agent_assigns_role_through_real_executor(self):
        self.message.content = "P.OS, выдай участнику alice роль Художник"
        role = SimpleNamespace(id=900000000000000030, name="Художник", position=2,
                               managed=False, is_default=lambda: False)
        member = SimpleNamespace(id=900000000000000040, name="alice", display_name="Alice",
                                 roles=[], top_role=SimpleNamespace(id=10, position=1),
                                 add_roles=AsyncMock())
        self.guild.roles = [role]
        self.guild.members = [member]
        self.guild.owner = self.owner
        self.guild.me.top_role = SimpleNamespace(id=900000000000000060, position=10)
        self.guild.get_member = lambda ident: member if ident == member.id else None
        self.guild.get_role = lambda ident: role if ident == role.id else None
        provider = AsyncMock(side_effect=[
            _response(_call("discover_tools", {"names": ["add_role"]}, call_id="discover")),
            _response(_call("add_role", {"user_identifier": "alice", "role_id_or_name": "Художник"}, call_id="assign")),
            _response(_call("finish_response", {"text": "Роль Художник выдана Alice.", "outcome": "completed", "evidence_call_ids": ["assign"]}, call_id="finish")),
        ])
        result = await self._run(set(), provider, plan=ToolIntentPlan.for_agent(self.message, pos_ai._eligible_tool_names_for_message(self.message)))
        member.add_roles.assert_awaited_once_with(role, reason="Выдано P.OS")
        self.assertEqual(result, "Роль Художник выдана Alice.")
        self.assertEqual(self.state["tool_results"][0]["status"], "success")

    async def test_native_agent_saves_rule_then_restart_delivers_join_and_leave_embeds(self):
        self.message.content = "P.OS, пиши здесь embed, когда кто-то заходит или выходит. Текст придумай сам."
        self.channel.permissions_for.return_value = discord.Permissions.all()
        self.channel.send = AsyncMock(side_effect=[SimpleNamespace(id=1001), SimpleNamespace(id=1002)])
        self.guild.member_count = 2
        member = SimpleNamespace(id=900000000000000080, name="alice", display_name="Alice",
                                 guild=self.guild, roles=[], bot=False,
                                 joined_at=datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc),
                                 created_at=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc))
        definition = {"name": "Входы и выходы", "channel_id": "current",
                      "trigger_events": ["member_join", "member_remove"],
                      "actions": [
                          {"type": "send_message", "events": ["member_join"], "embed": {"title": "Добро пожаловать", "description": "{member.display_name} вошёл в {guild.name}."}},
                          {"type": "send_message", "events": ["member_remove"], "embed": {"title": "До встречи", "description": "{member.display_name} покинул сервер."}},
                      ]}
        provider = AsyncMock(side_effect=[
            _response(_call("discover_tools", {"names": ["create_automation", "list_automations"]}, call_id="discover")),
            _response(_call("create_automation", definition, call_id="save-rule")),
            _response(_call("list_automations", {}, call_id="verify")),
            _response(_call("finish_response", {"text": "Сохранил правило: здесь будут embed о входах и выходах.", "outcome": "completed", "evidence_call_ids": ["save-rule"]}, call_id="finish")),
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "rules.db")
            original_get_conn = storage._get_conn

            async def test_connection(_path=storage.DEFAULT_DB_PATH):
                return await original_get_conn(path)

            await storage.init_db(path)
            try:
                with patch.object(storage, "_get_conn", new=test_connection):
                    result = await self._run(set(), provider, plan=ToolIntentPlan.for_agent(self.message, pos_ai._eligible_tool_names_for_message(self.message)))
                    self.assertIn("Сохранил правило", result)
                    rules = await storage.list_pos_automations(self.guild.id)
                    self.assertEqual(len(rules), 1)
                    self.assertEqual(rules[0]["channel_id"], self.channel.id)
                    await storage.close_all_connections()
                    await storage.init_db(path)
                    runtime = automation_rules.AutomationRuntime(self.bot)
                    with patch.object(automation_rules, "wait_for_join_security", new=AsyncMock(return_value=False)):
                        await runtime.handle_event("member_join", member)
                        await runtime.handle_event("member_join", member)
                        await runtime.handle_event("member_remove", member)
                    await runtime.close()
                    self.assertEqual(self.channel.send.await_count, 2)
                    self.assertEqual([call.kwargs["embed"].title for call in self.channel.send.await_args_list], ["Добро пожаловать", "До встречи"])
                    self.assertIn("Alice", self.channel.send.await_args_list[0].kwargs["embed"].description)
                    for call in self.channel.send.await_args_list:
                        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
                    runs = await storage.list_pos_automation_runs(self.guild.id)
                    self.assertEqual([run["status"] for run in runs], ["completed", "completed"])
            finally:
                await storage.close_all_connections()

    async def test_nonowner_cannot_install_persistent_authority(self):
        self.message.author = SimpleNamespace(id=900000000000000081)
        self.assertTrue(pos_ai._eligible_tool_names_for_message(self.message).isdisjoint(automation_rules.AUTOMATION_WRITE_TOOLS))
        with patch.object(pos_ai, "execute_automation_tool", new=AsyncMock()) as execute:
            result = await pos_ai.execute_pos_tool(self.bot, self.message, _call("create_automation", {}), allowed_tool_names=frozenset({"create_automation"}))
        self.assertIn("Отказано", result)
        execute.assert_not_awaited()

    async def test_explicit_username_is_not_replaced_by_reply_author(self):
        alice = SimpleNamespace(id=900000000000000040, name="alice", display_name="Alice")
        bob = SimpleNamespace(id=900000000000000050, name="bob", display_name="Bob")
        self.message.content = "P.OS, забань bob"
        self.message.reference = SimpleNamespace(resolved=SimpleNamespace(author=alice))
        self.guild.members = [alice, bob]
        self.guild.get_member = lambda ident: bob if ident == bob.id else alice if ident == alice.id else None
        self.guild.ban = AsyncMock()
        provider = AsyncMock(side_effect=[_response(_call("ban_user", {"user_identifier": "bob"})), {"content": "Bob забанен."}])

        with patch.object(pos_ai, "_member_hierarchy_error", return_value=None):
            await self._run({"ban_user"}, provider)

        self.guild.ban.assert_awaited_once()
        self.assertEqual(self.guild.ban.await_args.args[0].id, bob.id)

    async def test_bulk_delete_preserves_messages_arriving_after_request(self):
        command_id = self.message.id
        available = [SimpleNamespace(id=command_id + delta) for delta in (3, 2, 1, 0, -1, -2, -3)]
        removed = []

        async def purge(*, limit, before=None, check=lambda item: True):
            eligible = [item for item in available if before is None or item.id < before.id]
            removed.extend(item.id for item in eligible[:limit] if check(item))
            return [item for item in available if item.id in removed]

        self.channel.purge.side_effect = purge
        provider = AsyncMock(side_effect=[
            _response(_call("delete_messages", {"count": "2", "channel_id_or_name": str(self.channel.id)})),
            {"content": "Удалены два сообщения перед командой."},
        ])

        await self._run({"delete_messages"}, provider)

        self.assertEqual(removed, [command_id - 1, command_id - 2])
        self.channel.purge.assert_awaited_once()

    async def test_unresolved_reply_cannot_delete_an_arbitrary_message(self):
        provider = AsyncMock(side_effect=[
            _response(_call("manage_message", {"action": "delete", "message_id": "reply"})),
            _response(_call("ask_user", {"question": "На какое сообщение ты ссылаешься?"})),
        ])

        result = await self._run({"manage_message"}, provider)

        self.channel.fetch_message.assert_not_awaited()
        self.channel.purge.assert_not_awaited()
        self.assertIn("На какое сообщение", result)

    def test_role_and_channel_snowflakes_are_not_user_targets(self):
        self.message.content = "P.OS, посмотри роль <@&900000000000000021> в канале <#900000000000000022>"
        self.message.role_mentions = [SimpleNamespace(id=900000000000000021)]
        self.message.channel_mentions = [SimpleNamespace(id=900000000000000022)]
        self.assertIsNone(pos_ai._message_target_user_id(self.message, self.bot))

    def test_explicit_role_and_channel_arguments_survive_other_mentions(self):
        self.message.role_mentions = [SimpleNamespace(id=900000000000000021)]
        self.message.channel_mentions = [SimpleNamespace(id=900000000000000022)]
        args = pos_ai._augment_tool_arguments_from_message(
            "set_channel_permission",
            {"channel_id_or_name": "other-channel", "target_role_or_user": "other-role"},
            self.message, self.bot,
        )
        self.assertEqual(args["channel_id_or_name"], "other-channel")
        self.assertEqual(args["target_role_or_user"], "other-role")

    async def test_request_change_during_batch_stops_remaining_actions(self):
        async def execute(*_args, **_kwargs):
            self.message.content = "P.OS, отмена"
            return "Первое действие выполнено."

        executor = AsyncMock(side_effect=execute)
        provider = AsyncMock(return_value=_response(
            _call("create_channel", {"name": "first"}),
            _call("create_channel", {"name": "second"}),
        ))

        result = await self._run({"create_channel"}, provider, executor=executor)

        executor.assert_awaited_once()
        self.assertIn("Первое действие выполнено", result)
        self.assertIn("остановлено", result)

    async def test_channel_alias_cannot_replay_bulk_deletion(self):
        available = [SimpleNamespace(id=self.message.id - offset) for offset in range(1, 7)]
        removed = []

        async def purge(*, limit, before, **_kwargs):
            selected = [item for item in available if item.id < before.id][:limit]
            for item in selected:
                available.remove(item)
                removed.append(item.id)
            return selected

        self.channel.purge.side_effect = purge
        provider = AsyncMock(side_effect=[
            _response(_call("delete_messages", {"count": 2, "channel_id_or_name": "general"})),
            _response(_call("delete_messages", {"count": 2, "channel_id_or_name": str(self.channel.id)})),
            _response(_call("delete_messages", {"count": 2, "channel_id_or_name": f"<#{self.channel.id}>"})),
            {"content": "Удалены два сообщения."},
        ])

        await self._run({"delete_messages"}, provider)

        self.channel.purge.assert_awaited_once()
        self.assertEqual(removed, [self.message.id - 1, self.message.id - 2])

    async def test_reply_accepts_name_and_mention_of_its_actual_channel(self):
        target = Mock(spec=discord.Message)
        target.id = 900000000000000010
        target.author = self.bot_user
        target.channel = self.channel
        target.guild = self.guild
        target.content = "Старый ответ P.OS."
        target.delete = AsyncMock()
        self.message.reference = SimpleNamespace(
            message_id=target.id, channel_id=self.channel.id, resolved=target,
        )
        self.channel.fetch_message.return_value = target

        for channel_alias in ("general", f"<#{self.channel.id}>"):
            with self.subTest(channel_alias=channel_alias):
                target.delete.reset_mock()
                self.channel.fetch_message.reset_mock()
                provider = AsyncMock(side_effect=[
                    _response(_call("manage_message", {
                        "action": "delete", "message_id": "reply",
                        "channel_id_or_name": channel_alias,
                    })),
                    {"content": "Этот ответ удалён."},
                ])

                await self._run({"manage_message"}, provider)

                target.delete.assert_awaited_once()
                self.channel.fetch_message.assert_awaited_once_with(target.id)
                self.channel.purge.assert_not_awaited()

    async def test_failed_write_cannot_be_reported_as_successful(self):
        claimed_success = "Готово, сообщение удалено."
        provider = AsyncMock(side_effect=[
            _response(_call("manage_message", {"action": "delete", "message_id": "reply"})),
            {"content": claimed_success},
        ])

        result = await self._run({"manage_message"}, provider)

        self.channel.fetch_message.assert_not_awaited()
        self.channel.purge.assert_not_awaited()
        self.assertNotIn(claimed_success, result)
        self.assertEqual(result, self.state["tool_results"][0]["result"])
        self.assertTrue(result.startswith("Ошибка:"))

    async def test_repeated_read_refreshes_after_mutation(self):
        target = Mock(spec=discord.Message)
        target.id = 900000000000000010
        target.author = self.bot_user
        target.channel = self.channel
        target.guild = self.guild
        target.content = "Нужное сообщение."
        target.created_at = None
        available = [target]
        history_reads = 0
        observations = []

        async def history(**_kwargs):
            nonlocal history_reads
            history_reads += 1
            for item in list(available):
                yield item

        async def delete():
            available.remove(target)

        target.delete = AsyncMock(side_effect=delete)
        self.channel.history = history
        self.channel.fetch_message.return_value = target

        async def complete(conversation, **_kwargs):
            observations.append(copy.deepcopy(conversation))
            if len(observations) in (1, 3):
                return _response(_call("read_messages", {"query": "нужное"}))
            if len(observations) == 2:
                self.assertIn(str(target.id), json.loads(conversation[-1]["content"])["result"])
                return _response(_call("manage_message", {"action": "delete", "message_id": str(target.id)}))
            return {"content": "Сообщение удалено; проверка подтвердила отсутствие."}

        await self._run({"read_messages", "manage_message"}, AsyncMock(side_effect=complete))

        target.delete.assert_awaited_once()
        self.assertEqual(history_reads, 2)
        refreshed = json.loads(observations[-1][-1]["content"])["result"]
        self.assertIn("не найдено сообщений", refreshed)
        self.assertNotIn(str(target.id), refreshed)


if __name__ == "__main__":
    unittest.main()
