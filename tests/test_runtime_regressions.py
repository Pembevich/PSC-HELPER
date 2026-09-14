import asyncio
import datetime
import io
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord

import action_undo
import commands
import forms
import storage
import telegram_bridge
from cogs.forms import COMPLAINT_INPUT_CHANNEL, FormsCog


class UndoRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_undo_unban_does_not_delete_messages(self):
        http = types.SimpleNamespace(ban=AsyncMock())
        guild = types.SimpleNamespace(id=1, _state=types.SimpleNamespace(http=http))
        guild.ban = types.MethodType(discord.Guild.ban, guild)
        bot = types.SimpleNamespace(get_guild=lambda _gid: guild)
        success, _ = await action_undo.undo_exact_action(bot, {
            "inverse_operation": "ban_user",
            "inverse_args": {"target_guild_id": 1, "user_id": 2},
        })
        self.assertTrue(success)
        self.assertEqual(http.ban.await_args.args[2], 0)

    async def test_mixed_group_is_claimed_once_and_does_not_skip_to_older_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "undo.db")
            await storage.init_db(db)
            try:
                common = dict(source_guild_id=1, source_channel_id=2, actor_id=3,
                              target_guild_id=1, args={}, result="OK", success=True, db_path=db)
                await storage.record_pos_tool_action(**common, source_message_id=4,
                                                     operation="create_role", inverse_operation="delete_role")
                await storage.record_pos_tool_action(**common, source_message_id=5, operation="delete_messages")
                await storage.record_pos_tool_action(**common, source_message_id=5,
                                                     operation="kick_user", inverse_operation="restore_kick_user")
                claim = dict(actor_id=3, source_guild_id=1, source_channel_id=2, db_path=db)
                results = await asyncio.gather(*(storage.claim_recent_pos_action_group(**claim) for _ in range(2)))
                self.assertEqual(sorted(map(len, results)), [0, 2])
                claimed = next(rows for rows in results if rows)
                self.assertEqual({row["source_message_id"] for row in claimed}, {5})
                self.assertEqual({row["undo_status"] for row in claimed}, {"in_progress"})
                self.assertFalse(await storage.claim_recent_pos_action_group(**claim))
                for row in claimed:
                    await storage.finish_pos_action_undo(row["id"], status="undone" if row["inverse_operation"] else "acknowledged",
                                                         result="handled", db_path=db)
                self.assertEqual((await storage.claim_recent_pos_action_group(**claim))[0]["source_message_id"], 4)
            finally:
                await storage.close_all_connections()


class TelegramRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_dm_never_publishes_private_reply(self):
        user = types.SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("DM unavailable")))
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        channel.fetch_message = AsyncMock()
        bot = types.SimpleNamespace(get_channel=lambda _id: channel, get_user=lambda _id: user)
        bridge = telegram_bridge.TelegramOwnerBridge(bot)
        target, error = await bridge._deliver_discord_response(
            {"channel_id": 1, "discord_message_id": 2, "discord_user_id": 3}, "private text", public=False,
        )
        self.assertEqual(target, "личные сообщения Discord")
        self.assertIsNotNone(error)
        channel.send.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()

    async def test_explicit_public_reply_still_uses_source(self):
        source = types.SimpleNamespace(reply=AsyncMock(), author=types.SimpleNamespace(id=3))
        channel = MagicMock(spec=discord.TextChannel)
        channel.fetch_message = AsyncMock(return_value=source)
        bridge = telegram_bridge.TelegramOwnerBridge(types.SimpleNamespace(get_channel=lambda _id: channel))
        _, error = await bridge._deliver_discord_response(
            {"channel_id": 1, "discord_message_id": 2, "discord_user_id": 3}, "public text", public=True,
        )
        self.assertIsNone(error)
        source.reply.assert_awaited_once()

    async def _transport(self, chunks, *, limit=None):
        protocol = types.SimpleNamespace(_reading_paused=False, connected=True, resume_reading=lambda **_kwargs: None)
        stream = aiohttp.StreamReader(protocol, 2**16, loop=asyncio.get_running_loop())

        class Response:
            content = stream
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        bridge = telegram_bridge.TelegramOwnerBridge(types.SimpleNamespace())
        bridge._session = types.SimpleNamespace(closed=False, post=lambda *_args, **_kwargs: Response())

        async def feed():
            for chunk in chunks:
                stream.feed_data(chunk)
                await asyncio.sleep(0)
            stream.feed_eof()

        feeder = asyncio.create_task(feed())
        try:
            with patch.object(telegram_bridge, "_MAX_TELEGRAM_RESPONSE_BYTES", limit or 2 * 1024 * 1024):
                return await bridge._api("getUpdates", {})
        finally:
            await feeder

    async def test_partial_network_chunks_are_read_until_eof(self):
        result = await self._transport([b'{"ok":true,', b'"result":', b'[1,2]}'])
        self.assertEqual(result, [1, 2])

    async def test_transport_limit_applies_to_sum_of_chunks(self):
        with self.assertRaisesRegex(telegram_bridge.TelegramBridgeError, "response_too_large"):
            await self._transport([b"12345678", b"abcdefgh"], limit=10)

    async def test_orphan_reservations_expire_without_retrying_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "telegram.db")
            await storage.init_db(db)
            try:
                common = dict(guild_id=1, channel_id=2, discord_user_id=3, discord_username="member",
                              urgency="normal", urgency_reason="", min_interval_seconds=1, daily_limit=5,
                              global_hourly_limit=20, max_pending_per_user=2, db_path=db)
                for message_id, timestamp in [(4, 10000), (5, 10001)]:
                    request, _ = await storage.reserve_telegram_contact_request(
                        **common, discord_message_id=message_id, message_text=str(message_id), now=timestamp,
                    )
                    self.assertIsNotNone(request)
                request, reason = await storage.reserve_telegram_contact_request(
                    **common, discord_message_id=6, message_text="another", now=10002,
                )
                self.assertIsNone(request)
                self.assertEqual(reason, "pending_limit")
                request, reason = await storage.reserve_telegram_contact_request(
                    **common, discord_message_id=7, message_text="new", now=20000,
                )
                self.assertIsNotNone(request)
                self.assertIsNone(reason)
                duplicate, reason = await storage.reserve_telegram_contact_request(
                    **common, discord_message_id=4, message_text="4", now=20001,
                )
                self.assertIsNone(duplicate)
                self.assertEqual(reason, "already_processed")
            finally:
                await storage.close_all_connections()


class GifCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_drains_encoder_before_releasing_slot_and_removes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            started = threading.Event()
            release = threading.Event()
            calls = []
            paths = []
            real_mkdtemp = tempfile.mkdtemp

            def mkdtemp(**kwargs):
                path = real_mkdtemp(dir=tmp, **kwargs)
                paths.append(Path(path))
                return path

            async def save(path):
                path.write_bytes(b"video")

            def encoder(_source, output, **_kwargs):
                calls.append(output)
                if len(calls) == 1:
                    started.set()
                    release.wait(timeout=5)
                Path(output).write_bytes(b"GIF")

            attachment = types.SimpleNamespace(filename="video.mp4", content_type="video/mp4", size=5, save=save)
            with patch.object(commands.tempfile, "mkdtemp", side_effect=mkdtemp), patch.object(
                commands, "_build_gif_from_video", side_effect=encoder
            ), patch.object(commands, "_gif_job_semaphore", asyncio.Semaphore(1)):
                first = asyncio.create_task(commands.generate_gif_from_attachments([attachment]))
                second = None
                try:
                    for _ in range(100):
                        if started.is_set():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(started.is_set())
                    first.cancel()
                    second = asyncio.create_task(commands.generate_gif_from_attachments([attachment]))
                    await asyncio.sleep(0.02)
                    self.assertEqual(len(calls), 1)
                    self.assertFalse(first.done())
                    self.assertTrue(paths[0].exists())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    self.assertFalse(paths[0].exists())
                    result, _ = await asyncio.wait_for(second, 2)
                    self.assertTrue(Path(result).exists())
                    self.assertEqual(len(calls), 2)
                finally:
                    release.set()
                    await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


class ComplaintArchiveTests(unittest.IsolatedAsyncioTestCase):
    def _complaint_submission(self):
        target = MagicMock(spec=discord.TextChannel)
        target.id = 123
        target.send = AsyncMock()
        guild = types.SimpleNamespace(
            id=1, default_role=MagicMock(spec=discord.Role), me=None, roles=[], channels=[], text_channels=[],
            get_role=lambda _id: None, create_text_channel=AsyncMock(return_value=target),
        )
        author = MagicMock(spec=discord.Member)
        author.id, author.bot, author.mention = 9, False, "<@9>"
        source = MagicMock(spec=discord.TextChannel)
        source.id, source.category = COMPLAINT_INPUT_CHANNEL, None
        content = "Никнейм нарушителя\n" + "длинное описание " * 100
        message = types.SimpleNamespace(id=99, content=content, guild=guild, author=author, channel=source,
                                        attachments=[], delete=AsyncMock(), reply=AsyncMock())
        cog = FormsCog(types.SimpleNamespace())
        return cog, message, target

    async def test_long_complaint_is_published_without_invalid_fields(self):
        cog, message, target = self._complaint_submission()
        # Extension-loading tests may replace cogs.forms in sys.modules after
        # collection; patch the globals of the class actually under test.
        with patch.dict(FormsCog.on_message.__globals__, {
            "wait_for_moderation": AsyncMock(return_value=False),
            "send_log_embed": AsyncMock(),
            # A fresh Linux VM can have less uptime than the complaint cooldown.
            "time": types.SimpleNamespace(monotonic=lambda: 100.0),
        }):
            await cog.on_message(message)
        target.send.assert_awaited_once()
        embed = target.send.await_args.kwargs["embed"]
        self.assertEqual(embed.description, message.content.strip())
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        message.delete.assert_awaited_once()

    async def test_complaint_submitted_at_zero_observes_cooldown_until_expiry(self):
        cog, message, target = self._complaint_submission()
        clock = types.SimpleNamespace(now=0.0)
        with patch.dict(FormsCog.on_message.__globals__, {
            "wait_for_moderation": AsyncMock(return_value=False),
            "send_log_embed": AsyncMock(),
            "time": types.SimpleNamespace(monotonic=lambda: clock.now),
        }):
            await cog.on_message(message)
            target.send.assert_awaited_once()
            message.reply.assert_not_awaited()

            for now, remaining_minutes in ((0.0, 10), (540.0, 1), (599.0, 1)):
                with self.subTest(now=now):
                    clock.now = now
                    message.reply.reset_mock()
                    await cog.on_message(message)
                    target.send.assert_awaited_once()
                    message.guild.create_text_channel.assert_awaited_once()
                    message.delete.assert_awaited_once()
                    message.reply.assert_awaited_once()
                    self.assertIn(f"через {remaining_minutes} мин.", message.reply.await_args.args[0])

            clock.now = 600.0
            await cog.on_message(message)
            self.assertEqual(target.send.await_count, 2)
            self.assertEqual(message.guild.create_text_channel.await_count, 2)
            self.assertEqual(message.delete.await_count, 2)

    def _channel(self, messages):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name = 123, "жалоба-1"

        async def history(**kwargs):
            self.assertIsNone(kwargs["limit"])
            for message in messages:
                yield message

        channel.history = history
        channel.delete = AsyncMock()
        return channel

    def _message(self, index, *, attachments=()):
        embed = discord.Embed(description=f"Form body {index}")
        return types.SimpleNamespace(
            id=index, created_at=datetime.datetime.now(datetime.timezone.utc),
            author=types.SimpleNamespace(id=9), content=f"Message {index}", embeds=[embed], attachments=attachments,
        )

    def _archive(self):
        archive = MagicMock(spec=discord.TextChannel)
        archive.id = 456
        archive.permissions_for.return_value.view_channel = False
        archive.send = AsyncMock(return_value=types.SimpleNamespace(jump_url="https://discord.com/channels/1/456/789"))
        return archive

    async def test_full_history_and_evidence_are_archived_before_closure(self):
        attachment = types.SimpleNamespace(
            filename="evidence.txt", size=4, url="https://cdn.example/evidence", to_file=AsyncMock(
                side_effect=lambda **_kwargs: discord.File(io.BytesIO(b"proof"), filename="evidence.txt")
            ),
        )
        messages = [self._message(i) for i in range(205)] + [self._message(205, attachments=[attachment])]
        channel = self._channel(messages)
        archive = self._archive()
        uploaded = []

        async def send(**kwargs):
            uploaded.append((kwargs["file"].filename, kwargs["file"].fp.read()))
            return types.SimpleNamespace(jump_url="https://discord.com/channels/1/456/789")

        archive.send.side_effect = send
        guild = types.SimpleNamespace(default_role=object(), roles=[], filesize_limit=8 * 1024 * 1024)
        with patch.object(forms, "get_log_channel", return_value=archive):
            success, preview = await forms._archive_complaint(guild, channel)
        self.assertTrue(success)
        self.assertLessEqual(len(preview), 1900)
        self.assertEqual(uploaded[0], ("evidence.txt", b"proof"))
        transcript = uploaded[-1][1].decode("utf-8")
        self.assertIn("Message 205", transcript)
        self.assertIn("Form body 205", transcript)
        self.assertIn("https://discord.com/channels/1/456/789", transcript)

    async def test_archive_failure_preserves_channel_and_allows_retry(self):
        channel = self._channel([self._message(1)])
        archive = self._archive()
        archive.send.side_effect = RuntimeError("archive unavailable")
        guild = types.SimpleNamespace(default_role=object(), roles=[], filesize_limit=8 * 1024 * 1024,
                                      get_channel=lambda _id: channel)
        interaction = types.SimpleNamespace(
            guild=guild, response=types.SimpleNamespace(defer=AsyncMock()), followup=types.SimpleNamespace(send=AsyncMock()),
            message=types.SimpleNamespace(id=99),
        )
        view = forms.ComplaintView(submitter=types.SimpleNamespace(id=1), complaint_channel_id=channel.id)
        claim = AsyncMock(return_value=True)
        with patch.object(forms, "get_log_channel", return_value=archive), patch.object(forms, "_claim_message", claim):
            await view.approve.callback(interaction)
        channel.delete.assert_not_awaited()
        claim.assert_not_awaited()

    async def test_public_archive_is_refused_without_copying_private_evidence(self):
        channel = self._channel([self._message(1)])
        archive = self._archive()
        archive.permissions_for.return_value.view_channel = True
        guild = types.SimpleNamespace(default_role=object(), roles=[], filesize_limit=8 * 1024 * 1024)
        with patch.object(forms, "get_log_channel", return_value=archive):
            success, _ = await forms._archive_complaint(guild, channel)
        self.assertFalse(success)
        archive.send.assert_not_awaited()

    async def test_archive_with_broader_role_access_is_refused(self):
        channel = self._channel([self._message(1)])
        channel.permissions_for.return_value.view_channel = False
        archive = self._archive()
        public_role = MagicMock(spec=discord.Role)
        default_role = MagicMock(spec=discord.Role)
        archive.permissions_for.side_effect = lambda role: types.SimpleNamespace(view_channel=role is public_role)
        guild = types.SimpleNamespace(default_role=default_role, roles=[default_role, public_role],
                                      filesize_limit=8 * 1024 * 1024)
        with patch.object(forms, "get_log_channel", return_value=archive):
            success, _ = await forms._archive_complaint(guild, channel)
        self.assertFalse(success)
        archive.send.assert_not_awaited()

    async def test_rejection_dm_accepts_long_history_and_reason(self):
        channel = self._channel([])
        guild = types.SimpleNamespace(get_channel=lambda _id: channel)
        moderator = MagicMock(spec=discord.Member)
        moderator.guild_permissions.administrator = True
        moderator.mention = "<@2>"
        user = types.SimpleNamespace(id=1, mention="<@1>")
        interaction = types.SimpleNamespace(
            guild=guild, user=moderator, response=types.SimpleNamespace(defer=AsyncMock()),
            followup=types.SimpleNamespace(send=AsyncMock()),
        )
        modal = forms.RejectModal(user, channel.id, source_message_id=99)
        modal.reason._value = "р" * 1000
        send_dm = AsyncMock(return_value=True)
        with patch.object(forms, "_archive_complaint", AsyncMock(return_value=(True, "x" * 1900))), patch.object(
            forms, "_claim_message_id", AsyncMock(return_value=True)
        ), patch.object(forms, "safe_send_dm", send_dm), patch.object(forms, "send_log_embed", AsyncMock()):
            await modal.on_submit(interaction)
        embed = send_dm.await_args.args[1]
        self.assertEqual(len(embed.description), 1900)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        channel.delete.assert_awaited_once()

    async def test_long_history_dm_has_valid_embed_limits_even_when_dm_fails(self):
        channel = self._channel([])
        guild = types.SimpleNamespace(get_channel=lambda _id: channel)
        user = types.SimpleNamespace(id=1, mention="<@1>")
        interaction = types.SimpleNamespace(
            guild=guild, user=user, response=types.SimpleNamespace(defer=AsyncMock()),
            followup=types.SimpleNamespace(send=AsyncMock()), message=types.SimpleNamespace(id=99, edit=AsyncMock()),
        )
        view = forms.ComplaintView(submitter=user, complaint_channel_id=channel.id)
        send_dm = AsyncMock(return_value=False)
        with patch.object(forms, "_archive_complaint", AsyncMock(return_value=(True, "x" * 1900))), patch.object(
            forms, "_claim_message", AsyncMock(return_value=True)
        ), patch.object(forms, "safe_send_dm", send_dm), patch.object(forms, "send_log_embed", AsyncMock()):
            await view.approve.callback(interaction)
        embed = send_dm.await_args.args[1]
        self.assertLessEqual(len(embed.description), 4096)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        channel.delete.assert_awaited_once()
        self.assertIn("отправить не удалось", interaction.followup.send.await_args.args[0])
