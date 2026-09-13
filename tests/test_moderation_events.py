import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.mod import ModerationCog
from config import DEFAULT_MODERATION_SETTINGS


def _message(content="", *, attachments=(), staff=False):
    guild = SimpleNamespace(id=123)
    author = MagicMock(spec=discord.Member)
    author.id = 456
    author.bot = False
    author.mention = "<@456>"
    author.guild = guild
    author.guild_permissions = discord.Permissions(administrator=staff)
    author.send = AsyncMock()
    return SimpleNamespace(
        id=424242, content=content, guild=guild, author=author,
        channel=SimpleNamespace(id=777), attachments=list(attachments),
        mentions=[], role_mentions=[], mention_everyone=False, delete=AsyncMock(),
    )


def _attachment(name, data):
    return SimpleNamespace(
        id=1, filename=name, content_type="application/octet-stream",
        size=len(data), read=AsyncMock(return_value=data),
    )


class ModerationEventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {**DEFAULT_MODERATION_SETTINGS, "ai_moderation": False}
        self.cog = ModerationCog(SimpleNamespace())
        # Extension unload/reload may replace sys.modules['cogs.mod'] after
        # collection. Patch the globals actually used by this collected class.
        self.cog_globals = ModerationCog._moderate_message.__globals__
        self.core_globals = self.cog_globals["detect_attachment_violations"].__globals__
        self.content_timeout = AsyncMock(return_value=True)
        self.url_timeout = AsyncMock(return_value=True)
        self.enterContext(patch.dict(self.cog_globals, {
            "get_guild_settings": AsyncMock(return_value=self.settings),
            "is_log_channel": MagicMock(return_value=False),
            "send_log_embed": AsyncMock(),
            "log_violation_with_evidence": AsyncMock(),
            "apply_max_timeout": self.content_timeout,
        }))
        self.enterContext(patch.dict(self.core_globals, {
            "log_violation_with_evidence": AsyncMock(),
            "ai_has_configured_provider": MagicMock(return_value=False),
            "VIRUSTOTAL_KEY": None,
            "GOOGLE_SAFEBROWSING_KEY": None,
            "apply_max_timeout": self.url_timeout,
        }))

    async def test_blacklisted_link_added_by_edit_is_checked_with_and_without_cache(self):
        for cached in (True, False):
            with self.subTest(cached=cached):
                message = _message("https://discord-gift.com/")
                payload = discord.RawMessageUpdateEvent(data={"content": message.content}, message=message)
                if cached:
                    payload.cached_message = _message("Привет")
                await self.cog.on_raw_message_edit(payload)
                message.delete.assert_awaited_once()

    async def test_attachment_added_by_uncached_edit_is_checked(self):
        attachment = _attachment("photo.png", b"MZ" + b"\0" * 16)
        message = _message(attachments=[attachment])
        payload = discord.RawMessageUpdateEvent(data={"attachments": [{}]}, message=message)
        await self.cog.on_raw_message_edit(payload)
        message.delete.assert_awaited_once()
        self.content_timeout.assert_awaited_once()

    async def test_benign_edit_does_not_count_as_another_spam_event(self):
        message = _message("Обычное исправление")
        payload = discord.RawMessageUpdateEvent(data={"content": message.content}, message=message)
        spam = AsyncMock()
        crosschannel = MagicMock()
        with patch.dict(self.cog_globals, {"handle_spam_if_needed": spam, "detect_crosschannel_spam": crosschannel}):
            await self.cog.on_raw_message_edit(payload)
            spam.assert_not_awaited()
            crosschannel.assert_not_called()
        message.delete.assert_not_awaited()

    async def test_embed_only_unchanged_and_bot_updates_are_ignored(self):
        message = _message("Обычный текст")
        embed_only = discord.RawMessageUpdateEvent(data={"embeds": []}, message=message)
        unchanged = discord.RawMessageUpdateEvent(data={"content": message.content}, message=message)
        unchanged.cached_message = _message(message.content)
        bot_message = _message("https://discord-gift.com/")
        bot_message.author.bot = True
        bot_update = discord.RawMessageUpdateEvent(data={"content": bot_message.content}, message=bot_message)
        with patch.object(self.cog, "_moderate_message", AsyncMock()) as moderate:
            for payload in (embed_only, unchanged, bot_update):
                await self.cog.on_raw_message_edit(payload)
            moderate.assert_not_awaited()

    async def test_malware_is_blocked_when_ads_and_nsfw_are_disabled(self):
        self.settings.update(filter_ads=False, filter_nsfw=False, filter_scam=True)
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("payload.exe", b"data")
        samples = (
            ("payload.exe", b"content"),
            ("picture.png", b"MZ" + b"\0" * 16),
            ("archive.zip", archive_bytes.getvalue()),
        )
        ai_media = AsyncMock()
        with patch.dict(self.core_globals, {"_classify_media_with_ai": ai_media}):
            for name, data in samples:
                with self.subTest(filename=name):
                    message = _message(attachments=[_attachment(name, data)])
                    self.assertTrue(await self.cog._moderate_message(message, check_behavior=False))
                    message.delete.assert_awaited_once()
            ai_media.assert_not_awaited()

    async def test_staff_malware_is_removed_without_timeout(self):
        self.settings.update(filter_ads=False, filter_nsfw=False, filter_scam=True)
        message = _message(attachments=[_attachment("payload.exe", b"content")], staff=True)
        self.assertTrue(await self.cog._moderate_message(message, check_behavior=False))
        message.delete.assert_awaited_once()
        self.content_timeout.assert_not_awaited()

    async def test_safe_document_and_disabled_malware_filter_do_not_punish(self):
        self.settings.update(filter_ads=False, filter_nsfw=False, filter_scam=True)
        document_bytes = io.BytesIO()
        with zipfile.ZipFile(document_bytes, "w") as archive:
            archive.writestr("word/document.xml", "<document/>")
        message = _message(attachments=[_attachment("report.docx", document_bytes.getvalue())])
        self.assertFalse(await self.cog._moderate_message(message, check_behavior=False))
        message.delete.assert_not_awaited()
        for settings in ({"filter_scam": False}, {"enabled": False, "filter_scam": True}):
            self.settings.update(settings)
            attachment = _attachment("payload.exe", b"content")
            message = _message(attachments=[attachment])
            self.assertFalse(await self.cog._moderate_message(message, check_behavior=False))
            attachment.read.assert_not_awaited()
            message.delete.assert_not_awaited()
