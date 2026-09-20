import asyncio
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import storage


class Attachment:
    def __init__(self, filename, raw):
        self.filename = filename
        self.raw = raw
        self.size = len(raw)

    async def read(self):
        return self.raw


class Message:
    def __init__(self, message_id, author, content, attachment):
        self.id = message_id
        self.author = author
        self.content = content
        self.attachments = [attachment]
        self.deleted = False

    async def delete(self):
        self.deleted = True


class BackupChannel:
    def __init__(self, author, upload_limit=32 * 1024):
        self.author = author
        self.guild = SimpleNamespace(filesize_limit=upload_limit)
        self.messages = []
        self.fetches = []
        self.fail_send_at = None
        self.send_error = RuntimeError("upload interrupted")
        self.send_count = 0

    async def send(self, *, content, file):
        self.send_count += 1
        if self.send_count == self.fail_send_at:
            raise self.send_error
        raw = file.fp.read()
        if len(raw) > self.guild.filesize_limit:
            raise AssertionError("attachment exceeds Discord upload limit")
        message = Message(
            len(self.messages) + 1, self.author, content,
            Attachment(file.filename, raw),
        )
        self.messages.append(message)
        return message

    def history(self, limit):
        async def generate():
            for message in list(reversed(self.messages))[:limit]:
                if not message.deleted:
                    yield message

        return generate()

    async def fetch_message(self, message_id):
        self.fetches.append(message_id)
        for message in self.messages:
            if message.id == message_id and not message.deleted:
                return message
        raise LookupError("backup part missing")


class MultipartBackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source_path = os.path.join(self.temp.name, "source.db")
        self.target_path = os.path.join(self.temp.name, "target.db")
        self.payload = os.urandom(80 * 1024)
        self._write_sentinel(self.source_path, self.payload)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=42))
        self.channel = BackupChannel(self.bot.user)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(storage, "BACKUP_CHANNEL_ID", 123))
        self.patches.enter_context(patch.object(storage, "_backup_uploads_allowed", True))
        self.patches.enter_context(patch.object(
            storage, "_resolve_backup_channel", AsyncMock(return_value=self.channel),
        ))

    async def asyncTearDown(self):
        await storage.close_all_connections()
        self.patches.close()
        self.temp.cleanup()

    @staticmethod
    def _write_sentinel(path, payload):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sentinel (value BLOB)")
            connection.execute("INSERT INTO sentinel VALUES (?)", (payload,))

    def _read_sentinel(self):
        with sqlite3.connect(self.target_path) as connection:
            return connection.execute("SELECT value FROM sentinel").fetchone()[0]

    async def _upload(self):
        self.assertTrue(await storage.backup_db_to_discord(self.bot, self.source_path))
        return self.channel.messages[-1]

    def _replace_manifest(self, message, manifest):
        raw = json.dumps(manifest).encode()
        message.attachments = [Attachment(storage._BACKUP_MANIFEST_FILENAME, raw)]
        message.content = storage._BACKUP_MARKER + " sha256=" + hashlib.sha256(raw).hexdigest()

    async def test_multipart_round_trip_uses_final_manifest_and_bounded_attachments(self):
        commit = await self._upload()
        self.assertEqual(commit.attachments[0].filename, storage._BACKUP_MANIFEST_FILENAME)
        manifest = storage._parse_backup_manifest(commit.attachments[0].raw)
        self.assertGreater(len(manifest["parts"]), 1)
        self.assertEqual(manifest["parts"][-1]["message_id"], commit.id - 1)
        self.assertTrue(all(
            message.attachments[0].size <= self.channel.guild.filesize_limit
            for message in self.channel.messages
        ))
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), self.payload)
        self.assertTrue(storage._backup_uploads_allowed)
        self.assertFalse(os.path.exists(self.source_path + ".backup"))
        self.assertFalse(os.path.exists(self.source_path + ".backup.gz"))

    async def test_small_upload_keeps_legacy_single_gzip_format(self):
        self.channel.guild.filesize_limit = 1024 * 1024
        commit = await self._upload()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(commit.attachments[0].filename, "bot_data.db.gz")
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), self.payload)

    async def test_missing_newest_part_falls_back_to_old_gzip_backup(self):
        old_path = os.path.join(self.temp.name, "old.db")
        self._write_sentinel(old_path, b"previous complete backup")
        with open(old_path, "rb") as source:
            old_raw = gzip.compress(source.read())
        self.channel.messages.append(Message(
            1, self.bot.user,
            storage._BACKUP_MARKER + " sha256=" + hashlib.sha256(old_raw).hexdigest(),
            Attachment("bot_data.db.gz", old_raw),
        ))
        commit = await self._upload()
        manifest = json.loads(commit.attachments[0].raw)
        part = await self.channel.fetch_message(manifest["parts"][0]["message_id"])
        part.deleted = True
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), b"previous complete backup")

    async def test_corrupt_part_preserves_existing_database_and_blocks_upload(self):
        await self._upload()
        self._write_sentinel(self.target_path, b"local intact")
        first = self.channel.messages[0].attachments[0]
        first.raw = b"x" + first.raw[1:]
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), b"local intact")
        self.assertFalse(storage._backup_uploads_allowed)
        count = self.channel.send_count
        self.assertFalse(await storage.backup_db_to_discord(self.bot, self.target_path))
        self.assertEqual(self.channel.send_count, count)

    async def test_part_from_another_author_is_rejected_before_attachment_download(self):
        await self._upload()
        first = self.channel.messages[0]
        first.author = SimpleNamespace(id=99)
        first.attachments[0].read = AsyncMock()
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        first.attachments[0].read.assert_not_awaited()
        self.assertFalse(os.path.exists(self.target_path))

    async def test_only_uncommitted_parts_are_unsafe_instead_of_an_empty_channel(self):
        commit = await self._upload()
        commit.deleted = True
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertFalse(storage._backup_uploads_allowed)

    async def test_failed_part_upload_removes_its_parts_and_keeps_previous_commit(self):
        previous = await self._upload()
        existing = list(self.channel.messages)
        self.channel.fail_send_at = self.channel.send_count + 2
        self.assertFalse(await storage.backup_db_to_discord(self.bot, self.source_path))
        self.assertFalse(previous.deleted)
        self.assertTrue(all(not message.deleted for message in existing))
        self.assertTrue(all(message.deleted for message in self.channel.messages[len(existing):]))
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))

    async def test_cancelled_upload_cleans_created_parts_and_propagates_cancellation(self):
        self.channel.fail_send_at = 2
        self.channel.send_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await storage.backup_db_to_discord(self.bot, self.source_path)
        self.assertTrue(self.channel.messages[0].deleted)
        self.assertFalse(os.path.exists(self.source_path + ".backup"))

    async def test_manifest_upload_failure_does_not_publish_complete_backup(self):
        self.channel.fail_send_at = 4  # 80 KiB random data produces three 32 KiB parts.
        self.assertFalse(await storage.backup_db_to_discord(self.bot, self.source_path))
        self.assertEqual(len(self.channel.messages), 3)
        self.assertTrue(all(message.deleted for message in self.channel.messages))

    async def test_manifest_size_caps_are_checked_before_fetching_parts(self):
        commit = await self._upload()
        manifest = json.loads(commit.attachments[0].raw)
        manifest["size"] = storage._MAX_BACKUP_BYTES + 1
        self._replace_manifest(commit, manifest)
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self.channel.fetches, [])

    async def test_duplicate_part_ids_are_rejected_before_fetch(self):
        commit = await self._upload()
        manifest = json.loads(commit.attachments[0].raw)
        manifest["parts"][1]["message_id"] = manifest["parts"][0]["message_id"]
        self._replace_manifest(commit, manifest)
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self.channel.fetches, [])

    async def test_incorrect_original_size_never_replaces_local_database(self):
        commit = await self._upload()
        self._write_sentinel(self.target_path, b"local intact")
        manifest = json.loads(commit.attachments[0].raw)
        manifest["original_size"] -= 1
        self._replace_manifest(commit, manifest)
        self.assertIsNone(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), b"local intact")

    async def test_pruning_removes_only_parts_of_retired_commit(self):
        await self._upload()
        old = list(self.channel.messages)
        with patch.object(storage, "_BACKUP_KEEP_LAST", 1):
            latest = await self._upload()
        self.assertTrue(all(message.deleted for message in old))
        self.assertFalse(latest.deleted)
        self.assertTrue(all(not message.deleted for message in self.channel.messages[len(old):]))
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))
        self.assertEqual(self._read_sentinel(), self.payload)

    async def test_missing_retired_part_does_not_prevent_other_parts_being_pruned(self):
        await self._upload()
        old = list(self.channel.messages)
        old[0].deleted = True
        with patch.object(storage, "_BACKUP_KEEP_LAST", 1):
            latest = await self._upload()
        self.assertTrue(all(message.deleted for message in old))
        self.assertFalse(latest.deleted)
        self.assertTrue(await storage.restore_db_from_discord(self.bot, self.target_path))
