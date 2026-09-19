import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class AutomationStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "automations.db")
        await storage.init_db(self.db_path)

    async def asyncTearDown(self):
        await storage.close_all_connections()
        self.tempdir.cleanup()

    async def rule(self, **kwargs):
        args = {
            "guild_id": 1,
            "owner_id": 2,
            "name": "Приветствие",
            "trigger_events": ["member_join", "member_remove"],
            "channel_id": 3,
            "definition": {
                "actions": [{"type": "send_message", "embed": {"description": "Привет, {member.name}!"}}],
            },
            "db_path": self.db_path,
        }
        args.update(kwargs)
        return await storage.upsert_pos_automation(**args)

    async def claim(self, rule, event_key="join:10:2026-09-14", **kwargs):
        return await storage.claim_pos_automation_run(
            rule["guild_id"], rule["id"], event_key, db_path=self.db_path, **kwargs,
        )

    async def test_rules_and_reserved_runs_survive_reopen_and_migration(self):
        rule = await self.rule()
        run_id = await self.claim(rule)
        await storage.close_all_connections()
        await storage.init_db(self.db_path)
        self.assertEqual(await storage.list_pos_automations(1, db_path=self.db_path), [rule])
        runs = await storage.list_pos_automation_runs(1, db_path=self.db_path)
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["status"], "reserved")
        self.assertIsNone(await self.claim(rule))

    async def test_migration_preserves_old_database_content(self):
        old_path = str(Path(self.tempdir.name) / "old.db")
        with sqlite3.connect(old_path) as conn:
            conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, description TEXT)")
            conn.execute("INSERT INTO entries (title, description) VALUES ('keep', 'old data')")
        await storage.init_db(old_path)
        await storage.init_db(old_path)
        self.assertEqual(await storage.list_entries(db_path=old_path), [(1, "keep", "old data")])
        created = await self.rule(db_path=old_path)
        self.assertEqual(created["name"], "Приветствие")

    async def test_revision_migration_preserves_existing_rule_and_claim(self):
        rule = await self.rule()
        run_id = await self.claim(rule)
        await storage.close_all_connections()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("ALTER TABLE pos_automations DROP COLUMN revision")
        await storage.init_db(self.db_path)
        await storage.init_db(self.db_path)
        migrated = (await storage.list_pos_automations(1, db_path=self.db_path))[0]
        self.assertEqual(migrated, rule)
        self.assertEqual(migrated["revision"], 1)
        self.assertEqual((await storage.list_pos_automation_runs(1, db_path=self.db_path))[0]["id"], run_id)
        self.assertIsNone(await self.claim(migrated, expected_revision=migrated["revision"]))

    async def test_same_name_update_preserves_identity_owner_and_disabled_state(self):
        with patch.object(storage.time, "time", return_value=1000):
            old = await self.rule()
        await storage.set_pos_automation_enabled(1, old["id"], False, self.db_path)
        with patch.object(storage.time, "time", return_value=2000):
            updated = await self.rule(name="ПРИВЕТСТВИЕ", definition={"actions": []})
        self.assertEqual(updated["id"], old["id"])
        self.assertEqual(updated["owner_id"], old["owner_id"])
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["created_at"], 1000)
        self.assertEqual(updated["updated_at"], 2000)
        self.assertEqual(updated["definition"], {"actions": []})
        self.assertEqual(len(await storage.list_pos_automations(1, db_path=self.db_path)), 1)

    async def test_definition_and_enabled_state_are_saved_together(self):
        paused = await self.rule(enabled=False)
        self.assertFalse(paused["enabled"])
        self.assertIsNone(await self.claim(paused))
        resumed = await self.rule(automation_id=paused["id"], enabled=True, definition={"actions": ["new"]})
        self.assertTrue(resumed["enabled"])
        self.assertEqual(resumed["definition"], {"actions": ["new"]})
        self.assertEqual(resumed["revision"], paused["revision"] + 1)
        self.assertIsNotNone(await self.claim(resumed, expected_revision=resumed["revision"]))
        stopped = await self.rule(automation_id=paused["id"], enabled=False, definition={"actions": ["stopped"]})
        self.assertFalse(stopped["enabled"])
        self.assertEqual(stopped["definition"], {"actions": ["stopped"]})
        self.assertIsNone(await self.claim(stopped, "another", expected_revision=stopped["revision"]))
        await storage.close_all_connections()
        await storage.init_db(self.db_path)
        self.assertEqual(await storage.list_pos_automations(1, db_path=self.db_path), [stopped])

    async def test_explicit_rule_id_cannot_cross_guild_or_change_owner(self):
        rule = await self.rule()
        for args in ({"guild_id": 4}, {"owner_id": 5}):
            with self.subTest(**args), self.assertRaises(ValueError):
                await self.rule(automation_id=rule["id"], **args)
        self.assertFalse(await storage.set_pos_automation_enabled(4, rule["id"], False, self.db_path))
        self.assertFalse(await storage.delete_pos_automation(4, rule["id"], self.db_path))
        self.assertIsNone(await storage.claim_pos_automation_run(4, rule["id"], "same", db_path=self.db_path))
        self.assertEqual(await storage.list_pos_automations(4, db_path=self.db_path), [])
        self.assertEqual(await storage.list_pos_automations(1, db_path=self.db_path), [rule])

    async def test_names_are_scoped_to_guild_and_owner(self):
        rules = [await self.rule(), await self.rule(owner_id=5), await self.rule(guild_id=4)]
        self.assertEqual(len({rule["id"] for rule in rules}), 3)

    async def test_rename_conflict_rolls_back_without_breaking_next_write(self):
        first = await self.rule()
        second = await self.rule(name="Вторая")
        with self.assertRaises(ValueError):
            await self.rule(name=first["name"], automation_id=second["id"])
        self.assertTrue(await storage.set_pos_automation_enabled(1, first["id"], False, self.db_path))
        rules = await storage.list_pos_automations(1, db_path=self.db_path)
        self.assertEqual([rule["name"] for rule in rules], [first["name"], second["name"]])

    async def test_listing_filters_triggers_and_enabled_and_claim_checks_enabled(self):
        first = await self.rule()
        second = await self.rule(name="Сообщения", trigger_events=["message_create"])
        await storage.set_pos_automation_enabled(1, first["id"], False, self.db_path)
        self.assertIsNone(await self.claim(first))
        self.assertEqual(await storage.list_pos_automations(1, enabled_only=True, db_path=self.db_path), [second])
        matching = await storage.list_pos_automations(1, trigger_event="member_join", db_path=self.db_path)
        self.assertEqual([rule["id"] for rule in matching], [first["id"]])
        await storage.set_pos_automation_enabled(1, first["id"], True, self.db_path)
        self.assertIsNotNone(await self.claim(first))

    async def test_concurrent_claims_reserve_same_event_once(self):
        rule = await self.rule()
        claims = await asyncio.gather(*(self.claim(rule) for _ in range(20)))
        self.assertEqual(sum(run_id is not None for run_id in claims), 1)
        self.assertEqual(len(await storage.list_pos_automation_runs(1, db_path=self.db_path)), 1)

    async def test_same_second_update_invalidates_queued_revision_before_claim(self):
        with patch.object(storage.time, "time", return_value=1000):
            original = await self.rule()
            updated = await self.rule(definition={"actions": [{"type": "changed"}]})
        self.assertEqual(original["updated_at"], updated["updated_at"])
        self.assertEqual(updated["revision"], original["revision"] + 1)
        self.assertIsNone(await self.claim(original, expected_revision=original["revision"]))
        self.assertEqual(await storage.list_pos_automation_runs(1, db_path=self.db_path), [])
        self.assertIsNotNone(await self.claim(updated, expected_revision=updated["revision"]))

    async def test_pause_and_resume_invalidates_old_revision_even_in_same_second(self):
        with patch.object(storage.time, "time", return_value=1000):
            original = await self.rule()
            await storage.set_pos_automation_enabled(1, original["id"], False, self.db_path)
            await storage.set_pos_automation_enabled(1, original["id"], True, self.db_path)
        resumed = (await storage.list_pos_automations(1, db_path=self.db_path))[0]
        self.assertEqual(original["updated_at"], resumed["updated_at"])
        self.assertEqual(resumed["revision"], original["revision"] + 2)
        self.assertIsNone(await self.claim(original, expected_revision=original["revision"]))
        await storage.close_all_connections()
        await storage.init_db(self.db_path)
        self.assertEqual((await storage.list_pos_automations(1, db_path=self.db_path))[0]["revision"], resumed["revision"])
        self.assertIsNotNone(await self.claim(resumed, expected_revision=resumed["revision"]))

    async def test_concurrent_updates_increase_revision_without_lost_updates(self):
        original = await self.rule()
        updates = await asyncio.gather(*(self.rule(definition={"update": i}) for i in range(20)))
        self.assertEqual({rule["revision"] for rule in updates}, set(range(2, 22)))
        latest = (await storage.list_pos_automations(1, db_path=self.db_path))[0]
        self.assertEqual(latest["revision"], original["revision"] + 20)

    async def test_invalid_expected_revision_does_not_claim_event(self):
        rule = await self.rule()
        for revision in (False, 0, -1, 2**63, "1"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                await self.claim(rule, expected_revision=revision)
        self.assertEqual(await storage.list_pos_automation_runs(1, db_path=self.db_path), [])

    async def test_event_keys_are_per_rule_and_claims_for_missing_rules_fail_closed(self):
        first = await self.rule()
        second = await self.rule(name="Ещё")
        self.assertIsNotNone(await self.claim(first))
        self.assertIsNotNone(await self.claim(second))
        self.assertIsNone(await storage.claim_pos_automation_run(1, 999, "a", db_path=self.db_path))

    async def test_terminal_result_cannot_be_overwritten_or_replayed(self):
        rule = await self.rule()
        for index, status in enumerate(("completed", "failed", "partial", "skipped", "unknown")):
            with self.subTest(status=status):
                event_key = f"event:{index}"
                run_id = await self.claim(rule, event_key)
                await storage.finish_pos_automation_run(run_id, status=status, result={"sent": 123}, db_path=self.db_path)
                await storage.finish_pos_automation_run(run_id, status="completed", result={"sent": 999}, db_path=self.db_path)
                latest = (await storage.list_pos_automation_runs(1, db_path=self.db_path))[0]
                self.assertEqual(latest["status"], status)
                self.assertEqual(latest["result"], {"sent": 123})
                self.assertIsNotNone(latest["finished_at"])
                self.assertIsNone(await self.claim(rule, event_key))

    async def test_deleting_rule_retains_history_but_prevents_future_execution(self):
        rule = await self.rule()
        run_id = await self.claim(rule)
        self.assertTrue(await storage.delete_pos_automation(1, rule["id"], self.db_path))
        self.assertFalse(await storage.delete_pos_automation(1, rule["id"], self.db_path))
        self.assertIsNone(await self.claim(rule, "new"))
        self.assertEqual((await storage.list_pos_automation_runs(1, rule["id"], db_path=self.db_path))[0]["id"], run_id)
        recreated = await self.rule()
        self.assertNotEqual(recreated["id"], rule["id"])

    async def test_atomic_cooldown_blocks_concurrent_distinct_events(self):
        rule = await self.rule()
        with patch.object(storage.time, "time", return_value=1000):
            claims = await asyncio.gather(*(self.claim(rule, f"event:{i}", cooldown_seconds=30) for i in range(20)))
        self.assertEqual(sum(run_id is not None for run_id in claims), 1)
        with patch.object(storage.time, "time", return_value=1029):
            self.assertIsNone(await self.claim(rule, "later", cooldown_seconds=30))
        with patch.object(storage.time, "time", return_value=1030):
            self.assertIsNotNone(await self.claim(rule, "later", cooldown_seconds=30))

    async def test_skipped_run_releases_cooldown_but_not_its_event_key(self):
        rule = await self.rule()
        with patch.object(storage.time, "time", return_value=1000):
            run_id = await self.claim(rule, "skip", cooldown_seconds=30)
            await storage.finish_pos_automation_run(run_id, status="skipped", result={}, db_path=self.db_path)
            self.assertIsNone(await self.claim(rule, "skip", cooldown_seconds=30))
            self.assertIsNotNone(await self.claim(rule, "next", cooldown_seconds=30))

    async def test_maximum_rule_count_is_atomic_and_updates_work_at_capacity(self):
        results = await asyncio.gather(*(self.rule(name=f"Rule {i}") for i in range(52)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 50)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 2)
        updated = await self.rule(name="Rule 0", definition={"changed": True})
        self.assertTrue(updated["definition"]["changed"])
        self.assertIsNotNone(await self.rule(guild_id=4))

    async def test_invalid_payloads_and_ids_are_rejected_without_partial_rules(self):
        bad_definitions = [[], {"large": "я" * 17000}, {"bad": float("nan")}, {1: "not a string key"}, {"bad": object()}]
        for definition in bad_definitions:
            with self.subTest(definition_type=type(definition).__name__), self.assertRaises(ValueError):
                await self.rule(definition=definition)
        for args in ({"guild_id": 0}, {"owner_id": True}, {"channel_id": 2**63}, {"name": " "}, {"name": "x" * 101},
                     {"trigger_events": []}, {"trigger_events": ["invalid event"]}, {"enabled": 1}):
            with self.subTest(**args), self.assertRaises(ValueError):
                await self.rule(**args)
        self.assertEqual(await storage.list_pos_automations(1, db_path=self.db_path), [])

    async def test_invalid_run_result_leaves_reserved_claim_and_respects_json_bound(self):
        rule = await self.rule()
        run_id = await self.claim(rule)
        for kwargs in ({"status": "nonsense", "result": {}}, {"status": [], "result": {}},
                       {"status": "completed", "result": {"large": "x" * 32768}}):
            with self.subTest(status=kwargs["status"]), self.assertRaises(ValueError):
                await storage.finish_pos_automation_run(run_id, **kwargs, db_path=self.db_path)
        self.assertEqual((await storage.list_pos_automation_runs(1, db_path=self.db_path))[0]["status"], "reserved")
        self.assertIsNone(await self.claim(rule))

    async def test_runs_are_ordered_bounded_and_guild_scoped(self):
        first = await self.rule()
        second = await self.rule(name="Second")
        foreign = await self.rule(guild_id=4)
        for i in range(55):
            await self.claim(first, f"event:{i}")
        second_id = await self.claim(second)
        foreign_id = await self.claim(foreign)
        runs = await storage.list_pos_automation_runs(1, limit=1000, db_path=self.db_path)
        self.assertEqual(len(runs), 50)
        self.assertEqual(runs[0]["id"], second_id)
        self.assertNotIn(foreign_id, [run["id"] for run in runs])
        self.assertEqual(await storage.list_pos_automation_runs(4, first["id"], db_path=self.db_path), [])
        limited = await storage.list_pos_automation_runs(1, first["id"], limit=2, db_path=self.db_path)
        self.assertEqual([run["event_key"] for run in limited], ["event:54", "event:53"])

    async def test_consistent_backup_contains_rules_and_deduplication_ledger(self):
        rule = await self.rule()
        run_id = await self.claim(rule)
        snapshot = await storage._create_consistent_snapshot(self.db_path)
        self.assertIsNotNone(snapshot)
        with sqlite3.connect(snapshot) as conn:
            self.assertEqual(conn.execute("SELECT name FROM pos_automations").fetchone()[0], rule["name"])
            self.assertEqual(conn.execute("SELECT id, status FROM pos_automation_runs").fetchone(), (run_id, "reserved"))


if __name__ == "__main__":
    unittest.main()
