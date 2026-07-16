import asyncio
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cogs.logging_events as logging_events
import storage
from message_gate import begin_moderation, finish_moderation, wait_for_moderation
from moderation import extract_urls
from pos_ai import _extract_textual_tool_calls, request_pos_reply
from storage import close_all_connections, restore_db_from_discord


class MessageGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiter_can_arrive_before_moderation_listener(self):
        message_id = 987654321
        waiter = asyncio.create_task(wait_for_moderation(message_id, timeout=1.0))
        await asyncio.sleep(0)
        begin_moderation(message_id)
        finish_moderation(message_id, True)
        self.assertTrue(await waiter)


class UrlExtractionTests(unittest.TestCase):
    def test_extracts_bare_domains_but_not_common_filename(self):
        self.assertEqual(
            extract_urls("open suspicious-site.xyz/path and report.pdf"),
            ["suspicious-site.xyz/path"],
        )


class ToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_textual_tool_parser_rejects_code_and_unapproved_tools(self):
        self.assertEqual(
            _extract_textual_tool_calls(
                "tool_call: kick_user(user_id=__import__('os').getuid())",
                frozenset({"kick_user"}),
            ),
            [],
        )
        self.assertEqual(
            _extract_textual_tool_calls(
                "tool_call: kick_user(user_id='123')",
                frozenset({"ban_user"}),
            ),
            [],
        )

    async def test_only_intended_schema_is_exposed_and_duplicate_call_is_skipped(self):
        tool_call = {
            "id": "call-1",
            "function": {
                "name": "ban_user",
                "arguments": json.dumps({"user_id": "123", "reason": "spam"}),
            },
        }
        response = {"role": "assistant", "tool_calls": [tool_call, dict(tool_call)]}
        message = SimpleNamespace(
            content="P.OS, забань пользователя test за спам",
            author=SimpleNamespace(id=968698192411652176),
        )
        state = {"tools_executed": False}

        chat = AsyncMock(return_value=response)
        execute = AsyncMock(return_value="Пользователь забанен.")
        with patch("pos_ai.pos_chat_completion", new=chat), \
             patch("pos_ai.execute_pos_tool", new=execute):
            result = await request_pos_reply(SimpleNamespace(), message, [], state=state)

        schemas = chat.await_args.kwargs["tools"]
        self.assertEqual([schema["function"]["name"] for schema in schemas], ["ban_user"])
        self.assertEqual(chat.await_count, 1)
        self.assertEqual(execute.await_count, 1)
        self.assertTrue(state["tools_executed"])
        self.assertIn("Повторный идентичный вызов пропущен", result)

    async def test_non_owner_gets_only_mutating_schema_for_owner_approval(self):
        message = SimpleNamespace(
            content="P.OS, забань пользователя test",
            author=SimpleNamespace(id=123),
        )
        chat = AsyncMock(return_value={"role": "assistant", "content": "Нет доступа."})

        with patch("pos_ai.pos_chat_completion", new=chat):
            result = await request_pos_reply(SimpleNamespace(), message, [])

        self.assertEqual(result, "Нет доступа.")
        schemas = chat.await_args.kwargs["tools"]
        self.assertEqual([schema["function"]["name"] for schema in schemas], ["ban_user"])

    async def test_textual_tool_call_from_provider_is_executed(self):
        response = {
            "role": "assistant",
            "content": (
                "Принято. Выполняю устранение JuniperBot (`1351879409832951893`) с сервера.\n\n"
                "tool_call: kick_user(user_id='1351879409832951893', "
                "reason='По приказу владельца')"
            ),
        }
        message = SimpleNamespace(
            content="кикни бота джунипера с сервера",
            author=SimpleNamespace(id=968698192411652176),
        )
        state = {"tools_executed": False}
        chat = AsyncMock(return_value=response)
        execute = AsyncMock(return_value="JuniperBot кикнут с сервера.")

        with patch("pos_ai.pos_chat_completion", new=chat), \
             patch("pos_ai.execute_pos_tool", new=execute):
            result = await request_pos_reply(SimpleNamespace(), message, [], state=state)

        self.assertEqual(result, "JuniperBot кикнут с сервера.")
        self.assertTrue(state["tools_executed"])
        call = execute.await_args.args[2]
        self.assertEqual(call["function"]["name"], "kick_user")
        self.assertEqual(
            json.loads(call["function"]["arguments"])["user_id"],
            "1351879409832951893",
        )


class RestoreFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_all_connections()

    async def test_skips_corrupt_newest_backup_and_restores_older_valid_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.db")
            target_path = os.path.join(tmp, "target.db")
            connection = sqlite3.connect(source_path)
            connection.execute("CREATE TABLE sentinel (value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('restored')")
            connection.commit()
            connection.close()
            with open(source_path, "rb") as source:
                valid_raw = source.read()

            class Attachment:
                def __init__(self, raw):
                    self.filename = "bot_data.db"
                    self.size = len(raw)
                    self._raw = raw

                async def read(self):
                    return self._raw

            author = SimpleNamespace(id=42)
            corrupt_raw = b"SQLite format 3\x00" + (b"broken" * 20)
            messages = [
                SimpleNamespace(
                    id=2,
                    author=author,
                    content="[DATABASE_BACKUP] sha256=" + hashlib.sha256(corrupt_raw).hexdigest(),
                    attachments=[Attachment(corrupt_raw)],
                ),
                SimpleNamespace(
                    id=1,
                    author=author,
                    content="[DATABASE_BACKUP] sha256=" + hashlib.sha256(valid_raw).hexdigest(),
                    attachments=[Attachment(valid_raw)],
                ),
            ]

            class Channel:
                def history(self, limit=50):
                    async def generate():
                        for message in messages:
                            yield message
                    return generate()

            bot = SimpleNamespace(user=author)
            with patch("storage.BACKUP_CHANNEL_ID", 123), \
                 patch("storage._resolve_backup_channel", new=AsyncMock(return_value=Channel())):
                restored = await restore_db_from_discord(bot, target_path)

            self.assertTrue(restored)
            restored_db = sqlite3.connect(target_path)
            try:
                value = restored_db.execute("SELECT value FROM sentinel").fetchone()[0]
            finally:
                restored_db.close()
            self.assertEqual(value, "restored")

    async def test_restores_new_gzip_backup_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.db")
            target_path = os.path.join(tmp, "target.db")
            connection = sqlite3.connect(source_path)
            connection.execute("CREATE TABLE sentinel (value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('gzip-restored')")
            connection.commit()
            connection.close()
            with open(source_path, "rb") as source:
                valid_raw = source.read()
            compressed = gzip.compress(valid_raw, mtime=0)

            class Attachment:
                filename = "bot_data.db.gz"
                size = len(compressed)

                async def read(self):
                    return compressed

            author = SimpleNamespace(id=42)
            message = SimpleNamespace(
                id=3,
                author=author,
                content=(
                    "[DATABASE_BACKUP] encoding=gzip sha256="
                    + hashlib.sha256(compressed).hexdigest()
                ),
                attachments=[Attachment()],
            )

            class Channel:
                def history(self, limit=50):
                    async def generate():
                        yield message

                    return generate()

            bot = SimpleNamespace(user=author)
            with patch("storage.BACKUP_CHANNEL_ID", 123), patch(
                "storage._resolve_backup_channel",
                new=AsyncMock(return_value=Channel()),
            ):
                restored = await restore_db_from_discord(bot, target_path)

            self.assertTrue(restored)
            restored_db = sqlite3.connect(target_path)
            try:
                value = restored_db.execute("SELECT value FROM sentinel").fetchone()[0]
            finally:
                restored_db.close()
            self.assertEqual(value, "gzip-restored")

    def test_gzip_restore_rejects_decompression_bomb(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_path = os.path.join(tmp, "expanded.db")
            compressed = gzip.compress(b"x" * 1024, mtime=0)
            with patch.object(storage, "_MAX_BACKUP_BYTES", 128):
                with self.assertRaisesRegex(ValueError, "decompressed database"):
                    storage._write_restore_payload(
                        target_path,
                        compressed,
                        compressed=True,
                    )


class BackupConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_backup_channel_warns_only_once(self):
        bot = SimpleNamespace()
        with patch.object(storage, "BACKUP_CHANNEL_ID", 0), patch.object(
            storage,
            "_backup_disabled_warning_emitted",
            False,
        ), patch.object(storage.logger, "warning") as warning:
            self.assertFalse(await storage.backup_db_to_discord(bot))
            self.assertFalse(await storage.backup_db_to_discord(bot))
            self.assertFalse(await storage.restore_db_from_discord(bot))

        warning.assert_called_once()
        self.assertIn("DB_BACKUP_CHANNEL_ID", warning.call_args.args[0])


class LoggingRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_update_reads_discord_py_slowmode_attribute(self):
        guild = SimpleNamespace(id=7)

        class FakeTextChannel:
            def __init__(self, slowmode_delay):
                self.id = 70
                self.guild = guild
                self.name = "general"
                self.mention = "<#70>"
                self.category_id = None
                self.category = None
                self.topic = None
                self.nsfw = False
                self.slowmode_delay = slowmode_delay
                self.overwrites = {}
                self.position = 1

        before = FakeTextChannel(0)
        after = FakeTextChannel(5)
        append_audit = AsyncMock()
        send_log = AsyncMock()

        with patch.object(logging_events.discord, "TextChannel", FakeTextChannel), patch.object(
            logging_events,
            "_append_audit_fields",
            append_audit,
        ), patch.object(logging_events, "send_log_embed", send_log):
            cog = logging_events.LoggingCog(SimpleNamespace())
            await cog.on_guild_channel_update(before, after)

        append_audit.assert_awaited_once()
        send_log.assert_awaited_once()
        self.assertIn(
            ("Slowmode", "0s → 5s", True),
            send_log.await_args.kwargs["fields"],
        )


if __name__ == "__main__":
    unittest.main()
