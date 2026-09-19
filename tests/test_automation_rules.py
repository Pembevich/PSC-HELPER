import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import discord

import automation_rules as rules
import storage
from config import POS_CREATOR_ID


class FakeRole:
    def __init__(self, role_id, name, position, *, managed=False):
        self.id, self.name, self.position = role_id, name, position
        self.managed = managed

    def is_default(self):
        return self.id == 1

    def __ge__(self, other):
        return self.position >= other.position


class AutomationRulesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "rules.db")
        await storage.init_db(self.db_path)
        methods = (
            "list_pos_automations", "upsert_pos_automation", "set_pos_automation_enabled",
            "delete_pos_automation", "claim_pos_automation_run", "finish_pos_automation_run",
            "list_pos_automation_runs",
        )
        self.db = SimpleNamespace(**{name: partial(getattr(storage, name), db_path=self.db_path) for name in methods})
        self.patches = [
            patch.object(rules, "storage", self.db),
            patch.object(rules, "wait_for_join_security", AsyncMock(return_value=False)),
            patch.object(rules, "wait_for_moderation", AsyncMock(return_value=False)),
            patch.object(rules, "is_log_channel", return_value=False),
        ]
        for patched in self.patches:
            patched.start()
        self.everyone = FakeRole(1, "@everyone", 0)
        self.role = FakeRole(20, "Новичок", 2)
        self.top_role = FakeRole(99, "P.OS", 10)
        self.guild = SimpleNamespace(id=1, name="Тестовый сервер", owner_id=800, member_count=15)
        self.guild.me = SimpleNamespace(id=900, top_role=self.top_role, guild_permissions=SimpleNamespace(manage_roles=True))
        self.channel = SimpleNamespace(id=10, name="здесь", guild=self.guild,
                                       permissions_for=lambda _: SimpleNamespace(view_channel=True, send_messages=True, embed_links=True),
                                       send=AsyncMock(return_value=SimpleNamespace(id=500)))
        self.guild.channels = [self.channel]
        self.guild.text_channels = [self.channel]
        self.guild.roles = [self.everyone, self.role, self.top_role]
        self.guild.get_channel = lambda ident: next((channel for channel in self.guild.channels if channel.id == ident), None)
        self.guild.get_thread = lambda _: None
        self.guild.get_channel_or_thread = self.guild.get_channel
        self.guild.get_role = lambda ident: next((role for role in self.guild.roles if role.id == ident), None)
        self.member = SimpleNamespace(id=200, name="Участник", display_name="Имя участника", bot=False,
                                      guild=self.guild, roles=[self.everyone], top_role=self.everyone,
                                      joined_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
                                      created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))

        async def add_role(role, **kwargs):
            self.member.roles.append(role)

        async def remove_role(role, **kwargs):
            self.member.roles = [existing for existing in self.member.roles if existing.id != role.id]

        self.member.add_roles = AsyncMock(side_effect=add_role)
        self.member.remove_roles = AsyncMock(side_effect=remove_role)
        self.guild.fetch_member = AsyncMock(return_value=self.member)
        self.owner = SimpleNamespace(id=POS_CREATOR_ID)
        self.request = SimpleNamespace(id=300, author=self.owner, guild=self.guild, channel=self.channel)
        self.bot = SimpleNamespace(user=self.guild.me)
        self.runtime = rules.AutomationRuntime(self.bot)

    async def asyncTearDown(self):
        await self.runtime.close()
        for patched in reversed(self.patches):
            patched.stop()
        await storage.close_all_connections()
        self.tempdir.cleanup()

    async def tool(self, name, args, *, message=None):
        return await rules.execute_automation_tool(self.bot, message or self.request, name, args)

    async def create(self, **overrides):
        args = {
            "name": "Входы и выходы",
            "trigger_events": ["member_join", "member_remove"],
            "actions": [
                {"type": "send_message", "events": ["member_join"], "embed": {
                    "title": "Добро пожаловать", "description": "{member.mention} вошёл в {guild.name}", "color": 0x11AA44,
                }},
                {"type": "send_message", "events": ["member_remove"], "embed": {
                    "title": "До встречи", "description": "{member.display_name} покинул сервер", "color": 0xAA3322,
                }},
            ],
        }
        args.update(overrides)
        result = await self.tool("create_automation", args)
        self.assertFalse(result.startswith("Ошибка:"), result)
        return json.loads(result)["automation"]

    async def test_create_reopen_and_join_leave_send_model_authored_embeds_here(self):
        rule = await self.create()
        await storage.close_all_connections()
        await storage.init_db(self.db_path)
        self.runtime = rules.AutomationRuntime(self.bot)
        await self.runtime.handle_event("member_join", self.member)
        await self.runtime.handle_event("member_remove", self.member)
        self.assertEqual(self.channel.send.await_count, 2)
        join, leave = self.channel.send.await_args_list
        self.assertEqual(join.kwargs["embed"].title, "Добро пожаловать")
        self.assertEqual(join.kwargs["embed"].description, "<@200> вошёл в Тестовый сервер")
        self.assertEqual(leave.kwargs["embed"].description, "Имя участника покинул сервер")
        self.assertEqual(join.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        runs = json.loads(await self.tool("automation_runs", {"automation_id": rule["id"]}))["runs"]
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run["status"] == "completed" for run in runs))
        self.assertTrue(all(run["result"]["steps"][0]["message_id"] == 500 for run in runs))

    async def test_duplicate_delivery_and_restarts_do_not_repeat_effect(self):
        await self.create()
        await asyncio.gather(*(self.runtime.handle_event("member_join", self.member) for _ in range(5)))
        await storage.close_all_connections()
        await storage.init_db(self.db_path)
        await rules.AutomationRuntime(self.bot).handle_event("member_join", self.member)
        self.channel.send.assert_awaited_once()
        self.member.joined_at += timedelta(days=1)
        await self.runtime.handle_event("member_join", self.member)
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_multi_step_chain_refreshes_roles_and_records_every_effect(self):
        rule = await self.create(trigger_events=["member_join"], actions=[
            {"type": "add_role", "role_id": "Новичок"},
            {"type": "send_message", "content": "{member.display_name}, роль выдана"},
            {"type": "remove_role", "role_id": "20"},
        ])
        await self.runtime.handle_event("member_join", self.member)
        self.member.add_roles.assert_awaited_once()
        self.member.remove_roles.assert_awaited_once()
        self.assertEqual(self.guild.fetch_member.await_count, 2)
        self.assertNotIn(self.role, self.member.roles)
        run = (await self.db.list_pos_automation_runs(1, rule["id"]))[0]
        self.assertEqual([step["action"] for step in run["result"]["steps"]], ["add_role", "send_message", "remove_role"])
        self.assertEqual(run["status"], "completed")

    async def test_owner_and_bot_role_protection_is_checked_at_execution(self):
        rule = await self.create(trigger_events=["member_join"], actions=[{"type": "add_role", "role_id": "20"}])
        self.member.id = POS_CREATOR_ID
        await self.runtime.handle_event("member_join", self.member)
        self.member.add_roles.assert_not_awaited()
        run = (await self.db.list_pos_automation_runs(1, rule["id"]))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("владельца", run["result"]["steps"][0]["reason"])

    async def test_antiraid_suppresses_role_chain_without_granting(self):
        await self.create(trigger_events=["member_join"], actions=[
            {"type": "add_role", "role_id": "20"}, {"type": "send_message", "content": "Готово"},
        ])
        for decision in (True, None):
            with self.subTest(decision=decision):
                rules.wait_for_join_security.return_value = decision
                self.member.joined_at += timedelta(days=1)
                await self.runtime.handle_event("member_join", self.member)
        self.member.add_roles.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        runs = await self.db.list_pos_automation_runs(1)
        self.assertTrue(all(run["status"] == "skipped" for run in runs))

    async def test_message_condition_moderation_and_cooldown(self):
        await self.create(trigger_events=["message_create"], conditions={"content_contains": "привет"},
                          actions=[{"type": "send_message", "content": "Вижу: {message.content}"}])
        message = SimpleNamespace(id=600, author=self.member, guild=self.guild, channel=self.channel,
                                  content="ПРИВЕТ!", jump_url="https://discord.com/channels/1/10/600", webhook_id=None)
        rules.wait_for_moderation.return_value = True
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.channel.send.assert_not_awaited()
        rules.wait_for_moderation.return_value = None
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.channel.send.assert_not_awaited()
        rules.wait_for_moderation.return_value = False
        message.content = "Другая фраза"
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.channel.send.assert_not_awaited()
        message.content = "ПРИВЕТ!"
        await self.runtime.handle_event("message_create", self.member, message=message)
        message.id += 1
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.send.await_args.kwargs["content"], "Вижу: ПРИВЕТ!")

    async def test_messages_from_bots_webhooks_and_log_channels_never_loop(self):
        await self.create(trigger_events=["message_create"], conditions={"ignore_bots": False},
                          actions=[{"type": "send_message", "content": "Ответ"}])
        message = SimpleNamespace(id=600, channel=self.channel, webhook_id=None, content="Привет")
        self.member.bot = True
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.member.bot = False
        message.webhook_id = 701
        await self.runtime.handle_event("message_create", self.member, message=message)
        message.webhook_id = None
        rules.is_log_channel.return_value = True
        await self.runtime.handle_event("message_create", self.member, message=message)
        self.channel.send.assert_not_awaited()
        self.assertEqual(await self.db.list_pos_automation_runs(1), [])

    async def test_nonowner_cannot_create_update_or_delete(self):
        rule = await self.create()
        request = SimpleNamespace(author=SimpleNamespace(id=400), guild=self.guild, channel=self.channel)
        for name, args in (("create_automation", {}), ("update_automation", {"automation_id": rule["id"], "enabled": False}),
                           ("delete_automation", {"automation_id": rule["id"]})):
            with self.subTest(name=name):
                self.assertTrue((await self.tool(name, args, message=request)).startswith("Ошибка:"))
        self.assertTrue((await self.db.list_pos_automations(1))[0]["enabled"])

    async def test_pause_remains_possible_after_channel_and_role_deleted(self):
        rule = await self.create(trigger_events=["member_join"], actions=[{"type": "add_role", "role_id": "20"}])
        self.guild.channels.clear()
        self.guild.roles.clear()
        result = await self.tool("update_automation", {"automation_id": rule["id"], "enabled": False})
        self.assertEqual(json.loads(result)["status"], "paused")
        self.assertFalse((await self.db.list_pos_automations(1))[0]["enabled"])
        result = await self.tool("update_automation", {"automation_id": rule["id"], "enabled": True})
        self.assertTrue(result.startswith("Ошибка:"))
        self.assertFalse((await self.db.list_pos_automations(1))[0]["enabled"])

    async def test_delete_retains_owner_visible_run_history(self):
        rule = await self.create()
        await self.runtime.handle_event("member_join", self.member)
        await self.tool("delete_automation", {"automation_id": rule["id"]})
        self.assertEqual(await self.db.list_pos_automations(1), [])
        filtered = json.loads(await self.tool("automation_runs", {"automation_id": rule["id"]}))["runs"]
        all_runs = json.loads(await self.tool("automation_runs", {}))["runs"]
        self.assertEqual(filtered, all_runs)
        self.assertEqual(len(filtered), 1)
        await self.runtime.handle_event("member_remove", self.member)
        self.channel.send.assert_awaited_once()

    async def test_same_second_rule_edit_stops_queued_chain(self):
        rule = await self.create(trigger_events=["member_join"], actions=[
            {"type": "send_message", "content": "Первый"}, {"type": "send_message", "content": "Второй"},
        ])

        async def edit_after_send(**kwargs):
            await self.tool("update_automation", {"automation_id": rule["id"], "name": "Изменено"})
            return SimpleNamespace(id=500)

        self.channel.send.side_effect = edit_after_send
        await self.runtime.handle_event("member_join", self.member)
        self.channel.send.assert_awaited_once()
        run = (await self.db.list_pos_automation_runs(1))[0]
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["result"]["steps"][1]["status"], "skipped")

    async def test_stale_rule_snapshot_is_not_claimed(self):
        rule = await self.create()
        await self.tool("update_automation", {"automation_id": rule["id"], "name": "Новая версия"})
        await self.runtime._run_rule(rule, "member_join", self.member, None, suppress_roles=False)
        self.channel.send.assert_not_awaited()
        self.assertEqual(await self.db.list_pos_automation_runs(1), [])

    async def test_network_failure_records_unknown_and_does_not_retry(self):
        await self.create()
        self.channel.send.side_effect = aiohttp.ClientConnectionError("connection lost")
        await self.runtime.handle_event("member_join", self.member)
        await self.runtime.handle_event("member_join", self.member)
        self.channel.send.assert_awaited_once()
        run = (await self.db.list_pos_automation_runs(1))[0]
        self.assertEqual(run["status"], "unknown")
        self.assertIn("могло выполниться", run["result"]["steps"][0]["reason"])

    async def test_missing_destination_stops_chain_with_truthful_failure(self):
        await self.create()
        self.guild.channels.clear()
        await self.runtime.handle_event("member_join", self.member)
        self.channel.send.assert_not_awaited()
        run = (await self.db.list_pos_automation_runs(1))[0]
        self.assertEqual(run["status"], "failed")
        self.assertIn("удалён", run["result"]["steps"][0]["reason"])

    async def test_shutdown_records_uncertain_effect_and_never_replays(self):
        await self.create()
        started = asyncio.Event()

        async def hang(**kwargs):
            started.set()
            await asyncio.Event().wait()

        self.channel.send.side_effect = hang
        task = asyncio.create_task(self.runtime.handle_event("member_join", self.member))
        await started.wait()
        await self.runtime.close()
        self.assertTrue(task.cancelled())
        self.assertEqual((await self.db.list_pos_automation_runs(1))[0]["status"], "unknown")
        self.channel.send.side_effect = None
        await rules.AutomationRuntime(self.bot).handle_event("member_join", self.member)
        self.channel.send.assert_awaited_once()

    async def test_cross_guild_tool_ids_are_rejected(self):
        rule = await self.create()
        other = SimpleNamespace(id=2)
        request = SimpleNamespace(author=self.owner, guild=other, channel=self.channel)
        for name in ("list_automations", "update_automation", "delete_automation"):
            with self.subTest(name=name):
                result = await self.tool(name, {"automation_id": rule["id"]}, message=request)
                self.assertTrue(result.startswith("Ошибка:"), result)
        history = json.loads(await self.tool("automation_runs", {"automation_id": rule["id"]}, message=request))
        self.assertEqual(history["runs"], [])
        self.assertEqual(len(await self.db.list_pos_automations(1)), 1)

    async def test_workflow_validation_rejects_unsupported_or_unsafe_arguments(self):
        invalid = [
            {"trigger_events": ["role_update"]},
            {"trigger_events": ["member_remove"], "actions": [{"type": "add_role", "role_id": "20"}]},
            {"actions": [{"type": "python", "content": "print(1)"}]},
            {"actions": [{"type": "send_message", "content": "{secret.token}"}]},
            {"actions": [{"type": "send_message", "embed": {"title": "Ок", "url": "https://example.org"}}]},
            {"trigger_events": ["message_create"], "cooldown_seconds": 0},
            {"conditions": {"ignore_bots": "false"}},
            {"actions": [{"type": "add_role", "role_id": "99", "events": ["member_join"]},
                         {"type": "send_message", "events": ["member_remove"], "content": "Пока"}]},
        ]
        for bad in invalid:
            with self.subTest(args=bad):
                args = {"name": "Проверка", "trigger_events": ["member_join", "member_remove"],
                        "actions": [{"type": "send_message", "content": "Текст"}], **bad}
                result = await self.tool("create_automation", args)
                self.assertTrue(result.startswith("Ошибка:"), result)
        self.assertEqual(await self.db.list_pos_automations(1), [])

    def test_template_values_are_not_recursively_evaluated(self):
        result = rules.render_template("Имя: {member.name}", {"member.name": "{guild.name} @everyone", "guild.name": "Секрет"})
        self.assertEqual(result, "Имя: {guild.name} @everyone")

    def test_expanded_embeds_respect_discord_component_and_total_limits(self):
        action = {"type": "send_message", "content": "{message.content}", "embed": {
            "title": "{message.content}", "description": "{message.content}",
            "fields": [{"name": "{channel.name}", "value": "{message.content}"} for _ in range(10)],
            "footer": "{message.content}",
        }}
        rendered = rules._render_action(action, {"message.content": "x" * 10000, "channel.name": ""})
        self.assertEqual(len(rendered["content"]), 2000)
        embed = rendered["embed"]
        self.assertIsInstance(embed, discord.Embed)
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description), 4096)
        for field in embed.fields:
            self.assertTrue(1 <= len(field.name) <= 256)
            self.assertTrue(1 <= len(field.value) <= 1024)


if __name__ == "__main__":
    unittest.main()
