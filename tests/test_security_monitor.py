import asyncio
import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import security_monitor
import storage
from cogs.security import SecurityCog


def _snapshot():
    return {
        "schema": 3,
        "guild_id": 42,
        "guild_name": "Test",
        "mfa_level": 1,
        "verification_level": 3,
        "explicit_content_filter": 2,
        "default_notifications": 1,
        "everyone_permissions": [],
        "bot_permissions": list(security_monitor.POS_REQUIRED_PERMISSIONS),
        "roles": [
            {
                "id": 10,
                "name": "Moderator",
                "position": 5,
                "managed": False,
                "permissions": ["moderate_members"],
            }
        ],
        "channels": [
            {
                "id": 20,
                "name": "staff",
                "type": "text",
                "effective_permissions": [],
                "explicit_allows": [],
            }
        ],
        "admin_bots": [],
        "webhooks": {"available": True, "items": []},
        "automod": {
            "available": True,
            "items": [
                {
                    "id": 100,
                    "name": "Mention spam",
                    "enabled": True,
                    "event_type": 1,
                    "trigger_type": 5,
                    "trigger_hash": "trigger-a",
                    "actions": [1, 2],
                    "actions_hash": "actions-a",
                    "exempt_role_ids": [],
                    "exempt_channel_ids": [],
                }
            ],
        },
    }


class SecurityPostureDiffTests(unittest.TestCase):
    def test_stable_snapshot_has_no_alerts_and_hash_is_deterministic(self):
        snapshot = _snapshot()
        reordered = copy.deepcopy(snapshot)
        reordered["guild_name"] = "Test"
        self.assertEqual(
            security_monitor.security_snapshot_hash(snapshot),
            security_monitor.security_snapshot_hash(reordered),
        )
        self.assertEqual(
            security_monitor.diff_security_snapshots(snapshot, reordered),
            [],
        )

    def test_security_weakening_is_reported(self):
        previous = _snapshot()
        current = copy.deepcopy(previous)
        current["mfa_level"] = 0
        current["verification_level"] = 1
        current["everyone_permissions"] = ["mention_everyone"]
        current["roles"][0]["permissions"].append("administrator")
        current["bot_permissions"].remove("view_audit_log")
        current["admin_bots"].append({"id": 77, "name": "UnknownBot"})
        current["webhooks"]["items"].append(
            {"id": 88, "name": "incoming", "channel_id": 9}
        )
        current["automod"]["items"][0]["enabled"] = False

        alerts = security_monitor.diff_security_snapshots(previous, current)
        titles = {alert["title"] for alert in alerts}
        self.assertIn("Ослаблено требование 2FA для модерации", titles)
        self.assertIn("@everyone получил опасные права", titles)
        self.assertIn("Роль получила опасные права: Moderator", titles)
        self.assertIn("P.OS потерял права защиты", titles)
        self.assertIn("Новый бот с Administrator", titles)
        self.assertIn("Создан новый webhook", titles)
        self.assertIn("Отключено правило Discord AutoMod", titles)

    def test_automod_action_removal_is_reported(self):
        previous = _snapshot()
        current = copy.deepcopy(previous)
        current["automod"]["items"][0]["actions"] = [2]

        alerts = security_monitor.diff_security_snapshots(previous, current)
        self.assertTrue(
            any(alert["title"] == "Ослаблены действия Discord AutoMod" for alert in alerts)
        )

    def test_webhook_move_and_automod_weakening_are_reported(self):
        previous = _snapshot()
        previous["webhooks"]["items"] = [
            {"id": 88, "name": "incoming", "channel_id": 9}
        ]
        current = copy.deepcopy(previous)
        current["webhooks"]["items"][0]["channel_id"] = 10
        rule = current["automod"]["items"][0]
        rule["exempt_role_ids"] = [77]
        rule["trigger_hash"] = "trigger-b"
        rule["actions_hash"] = "actions-b"

        alerts = security_monitor.diff_security_snapshots(previous, current)
        titles = {alert["title"] for alert in alerts}
        self.assertIn("Webhook перенесён в другой канал", titles)
        self.assertIn("Расширены исключения Discord AutoMod", titles)
        self.assertIn("Изменены действия Discord AutoMod", titles)
        self.assertIn("Изменены условия Discord AutoMod", titles)

    def test_channel_permission_exposure_is_reported(self):
        previous = _snapshot()
        current = copy.deepcopy(previous)
        current["channels"][0]["effective_permissions"] = [
            "view_channel",
            "manage_webhooks",
        ]

        alerts = security_monitor.diff_security_snapshots(previous, current)
        titles = {alert["title"] for alert in alerts}
        self.assertIn("Канал стал доступен @everyone: staff", titles)
        self.assertIn("@everyone получил опасные права в канале: staff", titles)

    def test_schema_one_baseline_does_not_report_existing_channels_as_new(self):
        previous = _snapshot()
        previous.pop("channels")
        previous["schema"] = 1
        current = _snapshot()
        current["channels"][0]["explicit_allows"] = ["manage_webhooks"]

        alerts = security_monitor.diff_security_snapshots(previous, current)
        self.assertFalse(any("Новый канал" in alert["title"] for alert in alerts))

    def test_existing_insecure_posture_is_reported(self):
        snapshot = _snapshot()
        snapshot["mfa_level"] = 0
        snapshot["verification_level"] = 1
        snapshot["explicit_content_filter"] = 1
        snapshot["everyone_permissions"] = ["mention_everyone"]
        snapshot["bot_permissions"].remove("manage_guild")
        snapshot["channels"][0]["effective_permissions"] = [
            "view_channel",
            "manage_webhooks",
        ]
        snapshot["automod"]["items"] = []
        snapshot["admin_bots"] = [{"id": 77, "name": "UnknownBot"}]
        snapshot["webhooks"]["items"] = [
            {"id": 88, "name": "incoming", "channel_id": 9}
        ]

        alerts = security_monitor.assess_security_snapshot(snapshot)
        titles = {alert["title"] for alert in alerts}
        self.assertIn("Для действий модерации не требуется 2FA", titles)
        self.assertIn("@everyone уже имеет опасные права", titles)
        self.assertIn("P.OS не хватает прав для полного контура защиты", titles)
        self.assertIn("В каналах @everyone имеет опасные права", titles)
        self.assertIn("Нет активных правил Discord AutoMod", titles)
        self.assertIn("Сторонние боты имеют Administrator", titles)
        self.assertIn("На сервере есть webhook", titles)

    def test_malformed_legacy_snapshot_is_handled_without_crashing(self):
        malformed = {
            "schema": "unknown",
            "mfa_level": {},
            "verification_level": None,
            "explicit_content_filter": "invalid",
            "everyone_permissions": 7,
            "bot_permissions": "administrator",
            "roles": [{"id": 10, "permissions": {"administrator": True}}],
            "channels": [{"id": 20, "effective_permissions": 5}],
            "admin_bots": {},
            "webhooks": [],
            "automod": "invalid",
        }
        current = _snapshot()

        assessment = security_monitor.assess_security_snapshot(malformed)
        diff = security_monitor.diff_security_snapshots(malformed, current)
        summary = security_monitor.summarize_security_snapshot(malformed)

        self.assertIsInstance(assessment, list)
        self.assertIsInstance(diff, list)
        self.assertIn("2FA=", summary)


class SecurityPostureStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_round_trip_and_integrity_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/security.db"
            await storage.init_db(db_path)
            snapshot = _snapshot()
            saved_hash = await storage.set_security_posture(42, snapshot, db_path)
            restored = await storage.get_security_posture(42, db_path)
            self.assertEqual(restored, snapshot)
            self.assertEqual(saved_hash, security_monitor.security_snapshot_hash(snapshot))
            await storage.close_all_connections()


class SecurityPostureQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_burst_is_coalesced_into_one_scan(self):
        cog = SecurityCog(SimpleNamespace())
        cog._check_security_posture = AsyncMock()
        cog._posture_event_coalesce_seconds = 0.01
        guild = SimpleNamespace(id=42)

        cog._queue_security_posture_check(guild, "role_update")
        cog._queue_security_posture_check(guild, "channel_update")
        cog._queue_security_posture_check(guild, "webhooks_update")
        queued_task = cog._posture_tasks[guild.id]
        await asyncio.wait_for(queued_task, timeout=1.0)

        cog._check_security_posture.assert_awaited_once()
        source = cog._check_security_posture.await_args.kwargs["source"]
        self.assertEqual(source, "channel_update+role_update+webhooks_update")
        await cog.cog_unload()


if __name__ == "__main__":
    unittest.main()
