from __future__ import annotations

import os
import re
import uuid
from datetime import timedelta

import discord
from discord import Embed, Color
from discord.ext import commands
from discord.utils import escape_markdown, escape_mentions

from config import (
    allowed_guild_ids,
    allowed_role_ids,
    FORM_CHANNEL_ID,
    TAC_CHANNEL_ID,
    TAC_REVIEWER_ROLE_IDS,
    TAC_ROLE_REWARDS,
    VOICE_TIMEOUT_HOURS,
    PSC_CHANNEL_ID,
    PING_ROLE_ID,
    COMPLAINT_INPUT_CHANNEL,
    COMPLAINT_NOTIFY_ROLE,
    form_channel_id,
    punishment_roles,
    squad_roles,
    WELCOME_CHANNEL_ID,
    GOODBYE_CHANNEL_ID,
    TARGET_CHANNELS,
    TARGET_OUTPUT_CHANNEL,
    NEW_MEMBER_ROLE_IDS,
    UPDATE_LOG_CHANNEL_ID,
    UPDATE_LOG_MARKER,
)
from logging_utils import ensure_log_category_and_channels, send_log_embed, is_log_channel
from moderation import (
    check_and_handle_urls,
    handle_spam_if_needed,
    detect_advertising_or_scam_text,
    detect_attachment_violations,
    apply_max_timeout,
    log_violation_with_evidence,
)
from forms import ConfirmView, ComplaintView
from utils import extract_clean_keyword, assess_applicant_risk, safe_send_dm, collect_runtime_health
from pos_ai import handle_pos_ai, remember_server_message


def _clean_text(text: str, limit: int = 1800) -> str:
    safe = escape_mentions(escape_markdown(text or ""))
    return (safe[:limit] + "…") if len(safe) > limit else (safe or "(без текста)")


def _format_attachments(message: discord.Message) -> str:
    if not message.attachments:
        return "—"
    return "\n".join(a.url for a in message.attachments[:5])


def _should_log_message(message: discord.Message) -> bool:
    if not message.guild:
        return False
    if message.author.bot:
        return False
    if is_log_channel(message.channel):
        return False
    return True


async def _send_update_log_if_needed(bot: commands.Bot):
    if not UPDATE_LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(UPDATE_LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        async for existing in channel.history(limit=20):
            if existing.author.id == bot.user.id and UPDATE_LOG_MARKER in (existing.content or ""):
                return
    except Exception:
        return

    release_message = (
        f"[{UPDATE_LOG_MARKER}]\n"
        "Лог обновления P.S.C Helper:\n"
        "- обновлён стек зависимостей и приведён в рабочее состояние под Railway;\n"
        "- P.OS переведён на GitHub Models через OpenAI-compatible endpoint;\n"
        "- команда !ai снова отвечает, а диалоговый P.OS использует тот же AI-клиент и тот же характер;\n"
        "- улучшена конфигурация через env-переменные для модели, промпта и таймингов;\n"
        "- сохранены и актуализированы фильтрация, GIF-генерация, логи и форма с \"ОТПИСКИ\";\n"
        "- добавлены базовые тесты и dev-конфиг для дальнейшей поддержки."
    )

    try:
        await channel.send(release_message, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def _format_identity(entity) -> str:
    if not entity:
        return "—"
    entity_id = getattr(entity, "id", None)
    mention = getattr(entity, "mention", None)
    label = f"{entity} (`{entity_id}`)" if entity_id else str(entity)
    if mention:
        return f"{mention} — {label}"
    return label


async def _find_audit_entry(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    *,
    target_id: int | None = None,
    limit: int = 6,
) -> discord.AuditLogEntry | None:
    me = guild.me
    if not me or not me.guild_permissions.view_audit_log:
        return None

    after = discord.utils.utcnow() - timedelta(seconds=20)
    try:
        async for entry in guild.audit_logs(limit=limit, action=action, after=after):
            entry_target_id = getattr(entry.target, "id", None)
            if target_id is not None and entry_target_id != target_id:
                continue
            return entry
    except Exception:
        return None
    return None


async def _append_audit_fields(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int | None,
    fields: list[tuple[str, str, bool]],
) -> discord.AuditLogEntry | None:
    entry = await _find_audit_entry(guild, action, target_id=target_id)
    if not entry:
        return None

    fields.append(("Кто сделал", _format_identity(entry.user), False))
    if entry.reason:
        fields.append(("Причина", _clean_text(entry.reason, 1000), False))
    return entry


async def _log_message_create(message: discord.Message):
    if not _should_log_message(message):
        return
    desc = _clean_text(message.content)
    fields = [
        ("Автор", f"{message.author} (`{message.author.id}`)", False),
        ("Канал", message.channel.mention, False),
        ("Ссылка", message.jump_url, False),
    ]
    if message.attachments:
        fields.append(("Вложения", _format_attachments(message), False))
    await send_log_embed(
        message.guild,
        "messages",
        "💬 Сообщение",
        desc,
        color=Color.blurple(),
        fields=fields
    )


async def _log_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot:
        return
    if is_log_channel(before.channel):
        return
    if before.content == after.content and before.attachments == after.attachments:
        return

    before_text = _clean_text(before.content, 900)
    after_text = _clean_text(after.content, 900)
    desc = f"**Было:**\n{before_text}\n\n**Стало:**\n{after_text}"

    fields = [
        ("Автор", f"{before.author} (`{before.author.id}`)", False),
        ("Канал", before.channel.mention, False),
        ("Ссылка", after.jump_url, False),
    ]
    if after.attachments:
        fields.append(("Вложения", _format_attachments(after), False))

    await send_log_embed(
        before.guild,
        "message_edits",
        "✏️ Сообщение изменено",
        desc,
        color=Color.orange(),
        fields=fields
    )


async def _log_message_delete(message: discord.Message):
    if not message.guild:
        return
    if message.author and message.author.bot:
        return
    if is_log_channel(message.channel):
        return

    author = message.author if message.author else "(неизвестно)"
    author_id = message.author.id if message.author else "—"
    desc = _clean_text(message.content)
    fields = [
        ("Автор", f"{author} (`{author_id}`)", False),
        ("Канал", message.channel.mention, False),
    ]
    if message.attachments:
        fields.append(("Вложения", _format_attachments(message), False))

    await send_log_embed(
        message.guild,
        "message_deletes",
        "🗑️ Сообщение удалено",
        desc,
        color=Color.red(),
        fields=fields
    )


async def _log_member_roles_change(before: discord.Member, after: discord.Member):
    before_roles = set(before.roles)
    after_roles = set(after.roles)
    added = [r for r in after_roles - before_roles if r.name != "@everyone"]
    removed = [r for r in before_roles - after_roles if r.name != "@everyone"]
    if not added and not removed:
        return

    fields = [("Кому", _format_identity(after), False)]
    if added:
        fields.append(("Добавлены", ", ".join(r.mention for r in sorted(added, key=lambda role: role.position, reverse=True)), False))
    if removed:
        fields.append(("Удалены", ", ".join(r.mention for r in sorted(removed, key=lambda role: role.position, reverse=True)), False))

    await _append_audit_fields(after.guild, discord.AuditLogAction.member_role_update, after.id, fields)

    await send_log_embed(
        after.guild,
        "members",
        "🎭 Роли участника изменены",
        f"У участника обновился набор ролей.",
        color=Color.gold(),
        fields=fields
    )


async def _log_member_nick_change(before: discord.Member, after: discord.Member):
    if before.display_name == after.display_name:
        return
    fields = [
        ("Кому", _format_identity(after), False),
        ("Было", before.display_name, False),
        ("Стало", after.display_name, False),
    ]
    await _append_audit_fields(after.guild, discord.AuditLogAction.member_update, after.id, fields)
    await send_log_embed(
        after.guild,
        "members",
        "📝 Изменение ника",
        "Участнику обновили отображаемое имя.",
        color=Color.blue(),
        fields=fields
    )


async def _log_member_boost_change(before: discord.Member, after: discord.Member):
    if before.premium_since == after.premium_since:
        return
    if after.premium_since:
        title = "💎 Буст включён"
        detail = after.premium_since.strftime("%d.%m.%Y %H:%M:%S")
        color = Color.purple()
    else:
        title = "💤 Буст отключён"
        detail = "Буст снят"
        color = Color.orange()
    await send_log_embed(
        after.guild,
        "members",
        title,
        f"{after.mention} (`{after.id}`)",
        color=color,
        fields=[("Детали", detail, False)]
    )


async def _log_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel == after.channel and before.self_mute == after.self_mute and before.self_deaf == after.self_deaf:
        return

    if before.channel is None and after.channel is not None:
        action = "➕ Вошёл в канал"
        detail = after.channel.mention
    elif before.channel is not None and after.channel is None:
        action = "➖ Вышел из канала"
        detail = before.channel.mention
    elif before.channel != after.channel:
        action = "🔁 Перешёл между каналами"
        detail = f"{before.channel.mention} → {after.channel.mention}"
    else:
        action = "🔊 Изменение голосового статуса"
        detail = after.channel.mention if after.channel else "—"

    fields = [("Канал", detail, False)]
    if before.self_mute != after.self_mute:
        fields.append(("Самозаглушение", "вкл" if after.self_mute else "выкл", True))
    if before.self_deaf != after.self_deaf:
        fields.append(("Самооглушение", "вкл" if after.self_deaf else "выкл", True))

    await send_log_embed(
        member.guild,
        "voice",
        action,
        f"{member.mention} (`{member.id}`)",
        color=Color.purple(),
        fields=fields
    )


async def _log_member_timeout_change(before: discord.Member, after: discord.Member):
    if before.timed_out_until == after.timed_out_until:
        return
    now = discord.utils.utcnow()
    if after.timed_out_until and after.timed_out_until > now:
        title = "⏳ Тайм-аут выдан"
        detail = f"До: {after.timed_out_until.strftime('%d.%m.%Y %H:%M:%S')}"
        color = Color.orange()
    else:
        title = "✅ Тайм-аут снят"
        detail = "Ограничение снято"
        color = Color.green()
    fields = [("Кому", _format_identity(after), False), ("Детали", detail, False)]
    await _append_audit_fields(after.guild, discord.AuditLogAction.member_update, after.id, fields)
    await send_log_embed(
        after.guild,
        "moderation",
        title,
        "Изменение тайм-аута участника.",
        color=color,
        fields=fields
    )


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        await _log_message_edit(before, after)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        await _log_message_delete(message)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        if not messages:
            return
        message = next(iter(messages))
        if not message.guild or is_log_channel(message.channel):
            return
        await send_log_embed(
            message.guild,
            "message_deletes",
            "🧹 Массовое удаление сообщений",
            f"Удалено сообщений: {len(messages)}",
            color=Color.red(),
            fields=[("Канал", message.channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
            if channel:
                embed = discord.Embed(
                    title="## Запись в дата-базу P - O.S…",
                    description=(
                        f"————————————\n"
                        f"```\n"
                        f"Приветствую, {member.name}!\n"
                        f"Добро пожаловать на связанной центр фракции P.S.C!\n"
                        f"Вы записаны в базу хранения.\n"
                        f"```\n\n"
                        f"> **Ознакомьтесь с <#1340596281986383912>, там вы найдёте нужную вам информацию!**"
                    ),
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка приветствия on_member_join: {e}")

        try:
            for rid in NEW_MEMBER_ROLE_IDS:
                role = member.guild.get_role(rid)
                if role:
                    await member.add_roles(role, reason="Выдача ролей новым игрокам")
        except Exception as e:
            print(f"Ошибка выдачи ролей on_member_join: {e}")

        try:
            await send_log_embed(
                member.guild,
                "members",
                "➕ Участник вошёл",
                f"{member.mention} вошёл на сервер.",
                color=Color.green(),
                fields=[("ID", str(member.id), False)]
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        try:
            channel = member.guild.get_channel(GOODBYE_CHANNEL_ID)
            if channel:
                embed = Embed(
                    title="## Выписываю из базы данных…",
                    description=(
                        f"```\n"
                        f"Желаем удачи, {member.name}!\n"
                        f"Вы выписаны из базы данных.\n"
                        f"Ждём вас снова у нас!\n"
                        f"```"
                    ),
                    color=Color.red()
                )
                await channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка on_member_remove: {e}")

        try:
            await send_log_embed(
                member.guild,
                "members",
                "➖ Участник вышел",
                f"{member.mention} покинул сервер.",
                color=Color.orange(),
                fields=[("ID", str(member.id), False)]
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        await _log_member_roles_change(before, after)
        await _log_member_nick_change(before, after)
        await _log_member_timeout_change(before, after)
        await _log_member_boost_change(before, after)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        await _log_voice_state_update(member, before, after)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        fields = [("Тип", str(channel.type), False)]
        await _append_audit_fields(channel.guild, discord.AuditLogAction.channel_create, channel.id, fields)
        await send_log_embed(
            channel.guild,
            "channels",
            "➕ Канал создан",
            f"{getattr(channel, 'mention', channel.name)} (`{channel.id}`)",
            color=Color.green(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        fields = [("Тип", str(channel.type), False)]
        await _append_audit_fields(channel.guild, discord.AuditLogAction.channel_delete, channel.id, fields)
        await send_log_embed(
            channel.guild,
            "channels",
            "➖ Канал удалён",
            f"{channel.name} (`{channel.id}`)",
            color=Color.red(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        fields = []

        if before.name != after.name:
            fields.append(("Имя", f"{before.name} → {after.name}", False))

        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            before_cat = before.category.name if before.category else "—"
            after_cat = after.category.name if after.category else "—"
            fields.append(("Категория", f"{before_cat} → {after_cat}", False))

        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            if before.topic != after.topic:
                fields.append(("Тема", f"{before.topic or '—'} → {after.topic or '—'}", False))
            if before.nsfw != after.nsfw:
                fields.append(("NSFW", f"{'вкл' if before.nsfw else 'выкл'} → {'вкл' if after.nsfw else 'выкл'}", True))
            if before.rate_limit_per_user != after.rate_limit_per_user:
                fields.append(("Slowmode", f"{before.rate_limit_per_user}s → {after.rate_limit_per_user}s", True))

        if isinstance(before, discord.VoiceChannel) and isinstance(after, discord.VoiceChannel):
            if before.bitrate != after.bitrate:
                fields.append(("Bitrate", f"{before.bitrate} → {after.bitrate}", True))
            if before.user_limit != after.user_limit:
                fields.append(("User limit", f"{before.user_limit} → {after.user_limit}", True))

        if getattr(before, "overwrites", None) != getattr(after, "overwrites", None):
            fields.append(("Права", "изменены", False))

        if getattr(before, "position", None) != getattr(after, "position", None):
            fields.append(("Позиция", f"{before.position} → {after.position}", True))

        if not fields:
            return

        await _append_audit_fields(after.guild, discord.AuditLogAction.channel_update, after.id, fields)

        await send_log_embed(
            after.guild,
            "channels",
            "✏️ Канал изменён",
            f"{after.mention if isinstance(after, discord.TextChannel) else after.name} (`{after.id}`)",
            color=Color.orange(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        fields = [("Роль", role.mention, False)]
        await _append_audit_fields(role.guild, discord.AuditLogAction.role_create, role.id, fields)
        await send_log_embed(
            role.guild,
            "roles",
            "➕ Роль создана",
            f"{role.name} (`{role.id}`)",
            color=Color.green(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        fields = [("Роль", role.name, False)]
        await _append_audit_fields(role.guild, discord.AuditLogAction.role_delete, role.id, fields)
        await send_log_embed(
            role.guild,
            "roles",
            "➖ Роль удалена",
            f"{role.name} (`{role.id}`)",
            color=Color.red(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        fields = [("Роль", after.mention, False)]
        if before.name != after.name:
            fields.append(("Имя", f"{before.name} → {after.name}", False))
        if before.permissions != after.permissions:
            fields.append(("Права", "изменены", False))
        if len(fields) == 1:
            return
        await _append_audit_fields(after.guild, discord.AuditLogAction.role_update, after.id, fields)
        await send_log_embed(
            after.guild,
            "roles",
            "✏️ Роль изменена",
            f"{after.name} (`{after.id}`)",
            color=Color.orange(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        before_map = {e.id: e for e in before}
        after_map = {e.id: e for e in after}
        added = [e for e in after if e.id not in before_map]
        removed = [e for e in before if e.id not in after_map]
        changed = []
        for eid, b in before_map.items():
            a = after_map.get(eid)
            if a and a.name != b.name:
                changed.append(f"{b.name} → {a.name}")
        if not added and not removed and not changed:
            return
        fields = []
        if added:
            fields.append(("Добавлены", ", ".join(e.name for e in added)[:1024], False))
        if removed:
            fields.append(("Удалены", ", ".join(e.name for e in removed)[:1024], False))
        if changed:
            fields.append(("Изменены", ", ".join(changed)[:1024], False))
        await send_log_embed(
            guild,
            "server",
            "😀 Эмодзи обновлены",
            f"Всего: {len(after)}",
            color=Color.orange(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild: discord.Guild, before, after):
        before_map = {s.id: s for s in before}
        after_map = {s.id: s for s in after}
        added = [s for s in after if s.id not in before_map]
        removed = [s for s in before if s.id not in after_map]
        changed = []
        for sid, b in before_map.items():
            a = after_map.get(sid)
            if a and a.name != b.name:
                changed.append(f"{b.name} → {a.name}")
        if not added and not removed and not changed:
            return
        fields = []
        if added:
            fields.append(("Добавлены", ", ".join(s.name for s in added)[:1024], False))
        if removed:
            fields.append(("Удалены", ", ".join(s.name for s in removed)[:1024], False))
        if changed:
            fields.append(("Изменены", ", ".join(changed)[:1024], False))
        await send_log_embed(
            guild,
            "server",
            "🖼️ Стикеры обновлены",
            f"Всего: {len(after)}",
            color=Color.orange(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        await send_log_embed(
            channel.guild,
            "server",
            "🔗 Вебхуки обновлены",
            "Обновление вебхуков",
            color=Color.orange(),
            fields=[("Канал", channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        fields = [("Родитель", thread.parent.mention if thread.parent else "—", False)]
        await _append_audit_fields(thread.guild, discord.AuditLogAction.channel_create, thread.id, fields)
        await send_log_embed(
            thread.guild,
            "channels",
            "🧵 Тред создан",
            f"{thread.mention} (`{thread.id}`)",
            color=Color.green(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        fields = []
        if before.name != after.name:
            fields.append(("Имя", f"{before.name} → {after.name}", False))
        if before.archived != after.archived:
            fields.append(("Архив", f"{'вкл' if before.archived else 'выкл'} → {'вкл' if after.archived else 'выкл'}", True))
        if before.locked != after.locked:
            fields.append(("Закрыт", f"{'вкл' if before.locked else 'выкл'} → {'вкл' if after.locked else 'выкл'}", True))
        if before.auto_archive_duration != after.auto_archive_duration:
            fields.append(("Автоархив", f"{before.auto_archive_duration} → {after.auto_archive_duration} мин", True))
        if before.slowmode_delay != after.slowmode_delay:
            fields.append(("Slowmode", f"{before.slowmode_delay}s → {after.slowmode_delay}s", True))

        if not fields:
            return

        await _append_audit_fields(after.guild, discord.AuditLogAction.channel_update, after.id, fields)

        await send_log_embed(
            after.guild,
            "channels",
            "✏️ Тред изменён",
            f"{after.mention} (`{after.id}`)",
            color=Color.orange(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        fields = [("Родитель", thread.parent.mention if thread.parent else "—", False)]
        await _append_audit_fields(thread.guild, discord.AuditLogAction.channel_delete, thread.id, fields)
        await send_log_embed(
            thread.guild,
            "channels",
            "🗑️ Тред удалён",
            f"{thread.name} (`{thread.id}`)",
            color=Color.red(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_guild_channel_pins_update(self, channel: discord.abc.GuildChannel, last_pin):
        await send_log_embed(
            channel.guild,
            "server",
            "📌 Пины обновлены",
            f"{channel.mention}",
            color=Color.orange(),
            fields=[("Последний пин", last_pin.strftime('%d.%m.%Y %H:%M:%S') if last_pin else "—", False)]
        )

    @commands.Cog.listener()
    async def on_stage_instance_create(self, stage_instance: discord.StageInstance):
        await send_log_embed(
            stage_instance.guild,
            "voice",
            "🎙️ Stage создан",
            stage_instance.topic or "Без темы",
            color=Color.green(),
            fields=[("Канал", stage_instance.channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_stage_instance_update(self, before: discord.StageInstance, after: discord.StageInstance):
        fields = []
        if before.topic != after.topic:
            fields.append(("Было", before.topic or "—", False))
            fields.append(("Стало", after.topic or "—", False))
        await send_log_embed(
            after.guild,
            "voice",
            "✏️ Stage изменён",
            after.topic or "Без темы",
            color=Color.orange(),
            fields=fields or [("Канал", after.channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_stage_instance_delete(self, stage_instance: discord.StageInstance):
        await send_log_embed(
            stage_instance.guild,
            "voice",
            "🗑️ Stage удалён",
            stage_instance.topic or "Без темы",
            color=Color.red(),
            fields=[("Канал", stage_instance.channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        await send_log_embed(
            event.guild,
            "server",
            "📅 Событие создано",
            event.name,
            color=Color.green(),
            fields=[("Старт", event.start_time.strftime('%d.%m.%Y %H:%M:%S') if event.start_time else "—", False)]
        )

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent):
        fields = []
        if before.name != after.name:
            fields.append(("Было", before.name, False))
            fields.append(("Стало", after.name, False))
        if before.start_time != after.start_time:
            fields.append(("Старт", after.start_time.strftime('%d.%m.%Y %H:%M:%S') if after.start_time else "—", False))
        await send_log_embed(
            after.guild,
            "server",
            "✏️ Событие изменено",
            after.name,
            color=Color.orange(),
            fields=fields or [("ID", str(after.id), False)]
        )

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        await send_log_embed(
            event.guild,
            "server",
            "🗑️ Событие удалено",
            event.name,
            color=Color.red(),
            fields=[("ID", str(event.id), False)]
        )

    @commands.Cog.listener()
    async def on_guild_integrations_update(self, guild: discord.Guild):
        await send_log_embed(
            guild,
            "server",
            "🔌 Интеграции обновлены",
            "Изменения в интеграциях сервера",
            color=Color.orange()
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        fields = [("Кого", _format_identity(user), False)]
        await _append_audit_fields(guild, discord.AuditLogAction.ban, user.id, fields)
        await send_log_embed(
            guild,
            "moderation",
            "⛔ Бан",
            "Пользователь забанен.",
            color=Color.red(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        fields = [("Кого", _format_identity(user), False)]
        await _append_audit_fields(guild, discord.AuditLogAction.unban, user.id, fields)
        await send_log_embed(
            guild,
            "moderation",
            "✅ Разбан",
            "Пользователь разбанен.",
            color=Color.green(),
            fields=fields
        )

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        await send_log_embed(
            invite.guild,
            "server",
            "🔗 Инвайт создан",
            f"{invite.code}",
            color=Color.blue(),
            fields=[
                ("Канал", invite.channel.mention if invite.channel else "—", False),
                ("Создатель", invite.inviter.mention if invite.inviter else "—", False)
            ]
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        await send_log_embed(
            invite.guild,
            "server",
            "🗑️ Инвайт удалён",
            f"{invite.code}",
            color=Color.red(),
            fields=[("Канал", invite.channel.mention if invite.channel else "—", False)]
        )

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if before.name != after.name:
            fields = [("Было", before.name, False), ("Стало", after.name, False)]
            await _append_audit_fields(after, discord.AuditLogAction.guild_update, after.id, fields)
            await send_log_embed(
                after,
                "server",
                "🏷️ Сервер переименован",
                "У сервера обновилось название.",
                color=Color.orange(),
                fields=fields
            )

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        if not reaction.message.guild or user.bot:
            return
        if is_log_channel(reaction.message.channel):
            return
        await send_log_embed(
            reaction.message.guild,
            "server",
            "⭐ Реакция добавлена",
            f"{user} поставил {reaction.emoji}",
            color=Color.gold(),
            fields=[("Канал", reaction.message.channel.mention, False), ("Ссылка", reaction.message.jump_url, False)]
        )

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        if not reaction.message.guild or user.bot:
            return
        if is_log_channel(reaction.message.channel):
            return
        await send_log_embed(
            reaction.message.guild,
            "server",
            "❌ Реакция удалена",
            f"{user} убрал {reaction.emoji}",
            color=Color.orange(),
            fields=[("Канал", reaction.message.channel.mention, False), ("Ссылка", reaction.message.jump_url, False)]
        )

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        await send_log_embed(
            ctx.guild,
            "commands",
            "⌨️ Команда",
            f"{ctx.author} использовал `{ctx.message.content}`",
            color=Color.blurple(),
            fields=[("Канал", ctx.channel.mention, False)]
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await send_log_embed(
            ctx.guild,
            "errors",
            "❗ Ошибка команды",
            f"{ctx.author}: `{ctx.message.content}`",
            color=Color.red(),
            fields=[("Ошибка", str(error), False)]
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        if interaction.type == discord.InteractionType.application_command:
            data = interaction.data or {}
            name = data.get("name", "(неизвестно)")
            await send_log_embed(
                interaction.guild,
                "commands",
                "⚡ Slash-команда",
                f"/{name} — {interaction.user}",
                color=Color.blurple(),
                fields=[("Канал", interaction.channel.mention if interaction.channel else "—", False)]
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot))
