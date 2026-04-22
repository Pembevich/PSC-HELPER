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
from pos_ai import handle_pos_ai


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


def register_events(bot: commands.Bot):
    @bot.event
    async def on_ready():
        print(f"✅ Бот запущен как {bot.user} (id: {bot.user.id})")
        runtime = collect_runtime_health()
        missing_optional = [k for k, available in runtime.items() if not available and k != "DISCORD_TOKEN"]
        if missing_optional:
            print(f"⚠️ Не настроены опциональные ключи: {', '.join(missing_optional)}")

        for guild in bot.guilds:
            if allowed_guild_ids and guild.id not in allowed_guild_ids:
                continue
            try:
                await ensure_log_category_and_channels(guild)
            except Exception as e:
                print(f"Не удалось создать каналы логов: {e}")

        for guild_id in allowed_guild_ids:
            try:
                guild = discord.Object(id=guild_id)
                await bot.tree.sync(guild=guild)
                print(f"✅ Команды синхронизированы с сервером {guild_id}")
            except Exception as e:
                print(f"❌ Ошибка при синхронизации: {e}")

        try:
            for guild in bot.guilds:
                await send_log_embed(
                    guild,
                    "server",
                    "✅ Бот запущен",
                    f"Бот запущен как {bot.user}.",
                    color=Color.green()
                )
        except Exception:
            pass

        try:
            await _send_update_log_if_needed(bot)
        except Exception:
            pass

    @bot.event
    async def on_message(message: discord.Message):
        if not message.guild:
            return
        if message.author.bot:
            return

        await _log_message_create(message)

        try:
            if await check_and_handle_urls(message):
                return
        except Exception as e:
            print(f"Ошибка проверки ссылок: {e}")
            return

        try:
            if await handle_spam_if_needed(message):
                return
        except Exception:
            pass

        text_reasons = detect_advertising_or_scam_text(message.content or "")
        attachment_reasons = await detect_attachment_violations(message.attachments, message.content or "")
        moderation_reasons = text_reasons + attachment_reasons
        if moderation_reasons:
            try:
                await message.delete()
            except Exception:
                pass
            await apply_max_timeout(message.author, "Автомодерация: реклама/NSFW/вложения")
            await log_violation_with_evidence(message, "🚨 Автомодерация: нарушение контента", moderation_reasons)
            try:
                dm = Embed(
                    title="🚫 Сообщение удалено",
                    description="Обнаружены признаки рекламы/скама или нерелевантного медиа. Выдано ограничение голоса на 24 часа.",
                    color=Color.red()
                )
                dm.add_field(name="Причины", value="\n".join(f"• {r}" for r in moderation_reasons)[:1024], inline=False)
                await message.author.send(embed=dm)
            except Exception:
                pass
            return

        # --- Авто-отписки: эмбед + ветка (thread) ---
        try:
            if message.channel.id in TARGET_CHANNELS and "ОТПИСКИ" in (message.content or "").upper():
                today = discord.utils.utcnow().strftime("%d.%m.%Y")
                group = TARGET_CHANNELS[message.channel.id]

                if group == "P.S.C":
                    title = f"Общее мероприятие [P.S.C] ({today}) - Отписки."
                else:
                    title = f"Мероприятие [{group}] ({today}) - Отписки."

                embed = Embed(title=title, color=Color.from_rgb(255, 255, 255))

                target_channel = bot.get_channel(TARGET_OUTPUT_CHANNEL)
                if target_channel:
                    try:
                        sent = await target_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                        try:
                            await sent.create_thread(name=f"Отписки • {group}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"Ошибка при отправке эмбеда/создании ветки: {e}")
        except Exception as e:
            print(f"Ошибка авто-отписок: {e}")

        # --- PSC EMBED СООБЩЕНИЯ ---
        if message.channel.id == PSC_CHANNEL_ID:
            content_strip = (message.content or "").strip()
            if content_strip.upper().startswith("С ВЕБХУКОМ"):
                try:
                    await message.delete()
                except Exception:
                    pass

                guild = message.guild
                role_ping = guild.get_role(PING_ROLE_ID) if guild else None
                if role_ping:
                    try:
                        await message.channel.send(role_ping.mention)
                    except Exception:
                        pass

                content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()

                file_to_send = None
                embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255, 255, 255))
                embed.set_footer(text=f"©Provision Security Complex | {discord.utils.utcnow().strftime('%d.%m.%Y')}")
                if message.attachments:
                    att = message.attachments[0]
                    try:
                        os.makedirs("temp", exist_ok=True)
                        fname = f"temp/{uuid.uuid4().hex}-{att.filename}"
                        await att.save(fname)
                        file_to_send = discord.File(fname, filename=att.filename)
                        embed.set_image(url=f"attachment://{att.filename}")
                    except Exception:
                        file_to_send = None

                try:
                    if file_to_send:
                        await message.channel.send(embed=embed, file=file_to_send)
                        try:
                            os.remove(file_to_send.fp.name)
                        except Exception:
                            pass
                    else:
                        await message.channel.send(embed=embed)
                except Exception:
                    pass

                await bot.process_commands(message)
                return

        # --- Жалобы: приём формы ---
        if message.channel.id == COMPLAINT_INPUT_CHANNEL:
            lines = [ln.strip() for ln in (message.content or "").split("\n") if ln.strip()]
            if len(lines) < 2:
                try:
                    await message.delete()
                except Exception:
                    pass
                template = "1. Никнейм нарушителя\n2. Суть нарушения\n3. Доказательства (по желанию)"
                em = Embed(title="❌ Неверная форма жалобы", description="В форме должно быть минимум 2 строки: никнейм и суть нарушения.", color=Color.red())
                em.add_field(name="Пример", value=f"```{template}```", inline=False)
                await safe_send_dm(message.author, em)
                await bot.process_commands(message)
                return

            try:
                await message.delete()
            except Exception:
                pass

            guild = message.guild
            category = message.channel.category
            existing = [ch for ch in (category.channels if category else guild.channels) if ch.name.startswith("жалоба-")]
            index = len(existing) + 1
            channel_name = f"жалоба-{index}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                message.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }
            for role in guild.roles:
                try:
                    if role.permissions.administrator or role.permissions.manage_guild:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                except Exception:
                    pass

            try:
                complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, category=category, reason=f"Новая жалоба от {message.author}")
            except Exception:
                try:
                    complaint_chan = await guild.create_text_channel(channel_name, overwrites=overwrites, reason=f"Новая жалоба от {message.author}")
                except Exception:
                    complaint_chan = None

            try:
                await send_log_embed(
                    guild,
                    "forms",
                    "📢 Новая жалоба",
                    f"Создан канал жалобы для {message.author.mention}.",
                    color=Color.blue(),
                    fields=[
                        ("Канал", complaint_chan.mention if complaint_chan else "не создан", False),
                        ("Категория", category.name if category else "—", False)
                    ]
                )
            except Exception:
                pass

            embed = Embed(title="📢 Новая жалоба", color=Color.blue())
            embed.add_field(name="Отправитель", value=f"{message.author.mention} (ID: {message.author.id})", inline=False)
            full_text = "\n".join(lines)
            embed.add_field(name="Жалоба", value=f"```{full_text[:1900]}```", inline=False)
            embed.set_footer(text=f"ID жалобы: {index} | {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}")
            notify_role = guild.get_role(COMPLAINT_NOTIFY_ROLE)
            try:
                if complaint_chan:
                    if notify_role:
                        await complaint_chan.send(notify_role.mention)
                    view = ComplaintView(submitter=message.author, complaint_channel_id=complaint_chan.id)
                    await complaint_chan.send(embed=embed, view=view)

                    if message.attachments:
                        files = []
                        for att in message.attachments[:5]:
                            try:
                                files.append(await att.to_file())
                            except Exception:
                                pass
                        if files:
                            await complaint_chan.send(content="📎 Приложенные доказательства от автора жалобы:", files=files)
            except Exception:
                pass

            await bot.process_commands(message)
            return

        # --- Обработка формы вступления (единый отряд TAC) ---
        if message.channel.id == FORM_CHANNEL_ID:
            template = "1. Ваш roblox никнейм (НЕ ДИСПЛЕЙ)\n2. Ваш Discord ник\n3. tac"
            lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
            if len(lines) != 3:
                try:
                    await message.reply(embed=Embed(title="❌ Неверный шаблон", description="Форма должна содержать 3 строки.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            user_line, discord_nick_line, choice_line = lines
            keyword = extract_clean_keyword(choice_line)

            if keyword != "tac":
                try:
                    await message.reply(embed=Embed(title="❌ Неизвестный отряд", description="Третий пункт должен содержать только **tac**.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            target_channel = message.guild.get_channel(TAC_CHANNEL_ID)
            if not target_channel:
                try:
                    await message.reply("❌ Ошибка конфигурации: канал TAC не найден.")
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            risk_flags = assess_applicant_risk(user_line, discord_nick_line, message.author)
            risk_text = "\n".join(f"⚠️ {flag}" for flag in risk_flags) if risk_flags else "✅ Явных рисков не обнаружено"

            embed = Embed(
                title="📋 Подтверждение вступления в TAC",
                description=(
                    f"{message.author.mention} хочет вступить в отряд **TAC**\n"
                    f"Roblox ник: `{user_line}`\n"
                    f"Discord ник: `{discord_nick_line}`"
                ),
                color=Color.blue()
            )
            embed.add_field(name="Автопроверка кандидата", value=risk_text[:1024], inline=False)
            embed.set_footer(text=f"ID пользователя: {message.author.id} | {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}")

            view = ConfirmView(
                TAC_REVIEWER_ROLE_IDS,
                target_message=message,
                squad_name="TAC",
                role_ids=TAC_ROLE_REWARDS,
                target_user_id=message.author.id
            )

            mentions = []
            for rid in TAC_REVIEWER_ROLE_IDS:
                role = message.guild.get_role(rid)
                if role:
                    mentions.append(role.mention)

            try:
                if mentions:
                    await target_channel.send(content=" ".join(mentions), embed=embed, view=view)
                else:
                    await target_channel.send(embed=embed, view=view)
            except Exception:
                pass

            await bot.process_commands(message)
            return

        # --- Обработка наказаний (форма по id, выговоры/страйки) ---
        if message.channel.id == form_channel_id:
            template = (
                "Никнейм: Robloxer228\n"
                "Дискорд айди: 1234567890\n"
                "Наказание: 1 выговор / 2 выговора / 1 страйк / 2 страйка\n"
                "Причина: причина наказания\n"
                "Док-ва: (по желанию)"
            )

            lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
            if len(lines) < 4 or len(lines) > 5:
                try:
                    await message.reply(embed=Embed(title="❌ Ошибка", description="Форма должна содержать 4 или 5 строк.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            try:
                nickname = lines[0].split(":", 1)[1].strip()
                user_id = int(lines[1].split(":", 1)[1].strip())
                punishment = lines[2].split(":", 1)[1].strip().lower()
                reason = lines[3].split(":", 1)[1].strip()
            except Exception:
                try:
                    await message.reply(embed=Embed(title="❌ Ошибка в шаблоне", description="Проверь правильность полей (особенно Discord ID)", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            member = message.guild.get_member(user_id)
            if not member:
                try:
                    await message.reply("❌ Пользователь с таким ID не найден на сервере.")
                except Exception:
                    pass
                await bot.process_commands(message)
                return

            roles = member.roles

            async def log_action(text, color=Color.orange()):
                await send_log_embed(
                    message.guild,
                    "moderation",
                    "📋 Лог наказаний",
                    text,
                    color=color
                )

            async def apply_roles(to_add, to_remove):
                for r in to_remove:
                    if r in roles:
                        try:
                            await member.remove_roles(r)
                        except Exception:
                            pass
                for r in to_add:
                    if r not in roles:
                        try:
                            await member.add_roles(r)
                        except Exception:
                            pass

            punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
            punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
            strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
            strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

            if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                else:
                    notify = None
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} получил **максимальное количество наказаний** и подлежит **увольнению**.")
                await bot.process_commands(message)
                return

            if punishment == "1 выговор":
                if punish_1 in roles and punish_2 in roles:
                    await apply_roles([strike_1], [punish_1, punish_2])
                    await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
                elif punish_1 in roles:
                    await apply_roles([punish_2], [])
                    await log_action(f"{member.mention} получил второй выговор.")
                else:
                    await apply_roles([punish_1], [])
                    await log_action(f"{member.mention} получил первый выговор.")
            elif punishment == "2 выговора":
                if punish_1 in roles and punish_2 in roles:
                    await apply_roles([strike_1], [punish_1, punish_2])
                    await log_action(f"{member.mention} получил 1 страйк (2 выговора заменены).")
                elif punish_1 in roles:
                    await apply_roles([strike_1], [punish_1])
                    await log_action(f"{member.mention} получил 1 страйк (1 выговор заменён).")
                else:
                    await apply_roles([punish_1, punish_2], [])
                    await log_action(f"{member.mention} получил 2 выговора.")
            elif punishment == "1 страйк":
                if strike_1 in roles and strike_2 in roles:
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
                    else:
                        notify = None
                    if notify:
                        await log_action(f"{notify.mention}\nСотрудник {member.mention} получил 3-й страйк. Подлежит увольнению.")
                elif strike_1 in roles:
                    await apply_roles([strike_2], [])
                    await log_action(f"{member.mention} получил второй страйк.")
                else:
                    await apply_roles([strike_1], [])
                    await log_action(f"{member.mention} получил первый страйк.")
            elif punishment == "2 страйка":
                if strike_1 in roles or strike_2 in roles:
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
                    else:
                        notify = None
                    if notify:
                        await log_action(f"{notify.mention}\nСотрудник {member.mention} уже имеет страйки и получил ещё. Подлежит увольнению.")
                else:
                    await apply_roles([strike_1, strike_2], [])
                    await log_action(f"{member.mention} получил 2 страйка.")
            else:
                try:
                    await message.reply(embed=Embed(title="❌ Неизвестное наказание", description="Допустимые значения: `1 выговор`, `2 выговора`, `1 страйк`, `2 страйка`.", color=Color.red()).add_field(name="Пример", value=f"```{template}```"))
                except Exception:
                    pass

            await bot.process_commands(message)
            return

        if await handle_pos_ai(message, bot):
            return

        await bot.process_commands(message)

    @bot.event
    async def on_message_edit(before: discord.Message, after: discord.Message):
        await _log_message_edit(before, after)

    @bot.event
    async def on_message_delete(message: discord.Message):
        await _log_message_delete(message)

    @bot.event
    async def on_bulk_message_delete(messages):
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

    @bot.event
    async def on_member_join(member: discord.Member):
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

    @bot.event
    async def on_member_remove(member: discord.Member):
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

    @bot.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        await _log_member_roles_change(before, after)
        await _log_member_nick_change(before, after)
        await _log_member_timeout_change(before, after)
        await _log_member_boost_change(before, after)

    @bot.event
    async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        await _log_voice_state_update(member, before, after)

    @bot.event
    async def on_guild_channel_create(channel: discord.abc.GuildChannel):
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

    @bot.event
    async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
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

    @bot.event
    async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
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

    @bot.event
    async def on_guild_role_create(role: discord.Role):
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

    @bot.event
    async def on_guild_role_delete(role: discord.Role):
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

    @bot.event
    async def on_guild_role_update(before: discord.Role, after: discord.Role):
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

    @bot.event
    async def on_guild_emojis_update(guild: discord.Guild, before, after):
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

    @bot.event
    async def on_guild_stickers_update(guild: discord.Guild, before, after):
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

    @bot.event
    async def on_webhooks_update(channel: discord.abc.GuildChannel):
        await send_log_embed(
            channel.guild,
            "server",
            "🔗 Вебхуки обновлены",
            "Обновление вебхуков",
            color=Color.orange(),
            fields=[("Канал", channel.mention, False)]
        )

    @bot.event
    async def on_thread_create(thread: discord.Thread):
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

    @bot.event
    async def on_thread_update(before: discord.Thread, after: discord.Thread):
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

    @bot.event
    async def on_thread_delete(thread: discord.Thread):
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

    @bot.event
    async def on_guild_channel_pins_update(channel: discord.abc.GuildChannel, last_pin):
        await send_log_embed(
            channel.guild,
            "server",
            "📌 Пины обновлены",
            f"{channel.mention}",
            color=Color.orange(),
            fields=[("Последний пин", last_pin.strftime('%d.%m.%Y %H:%M:%S') if last_pin else "—", False)]
        )

    @bot.event
    async def on_stage_instance_create(stage_instance: discord.StageInstance):
        await send_log_embed(
            stage_instance.guild,
            "voice",
            "🎙️ Stage создан",
            stage_instance.topic or "Без темы",
            color=Color.green(),
            fields=[("Канал", stage_instance.channel.mention, False)]
        )

    @bot.event
    async def on_stage_instance_update(before: discord.StageInstance, after: discord.StageInstance):
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

    @bot.event
    async def on_stage_instance_delete(stage_instance: discord.StageInstance):
        await send_log_embed(
            stage_instance.guild,
            "voice",
            "🗑️ Stage удалён",
            stage_instance.topic or "Без темы",
            color=Color.red(),
            fields=[("Канал", stage_instance.channel.mention, False)]
        )

    @bot.event
    async def on_scheduled_event_create(event: discord.ScheduledEvent):
        await send_log_embed(
            event.guild,
            "server",
            "📅 Событие создано",
            event.name,
            color=Color.green(),
            fields=[("Старт", event.start_time.strftime('%d.%m.%Y %H:%M:%S') if event.start_time else "—", False)]
        )

    @bot.event
    async def on_scheduled_event_update(before: discord.ScheduledEvent, after: discord.ScheduledEvent):
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

    @bot.event
    async def on_scheduled_event_delete(event: discord.ScheduledEvent):
        await send_log_embed(
            event.guild,
            "server",
            "🗑️ Событие удалено",
            event.name,
            color=Color.red(),
            fields=[("ID", str(event.id), False)]
        )

    @bot.event
    async def on_guild_integrations_update(guild: discord.Guild):
        await send_log_embed(
            guild,
            "server",
            "🔌 Интеграции обновлены",
            "Изменения в интеграциях сервера",
            color=Color.orange()
        )

    @bot.event
    async def on_member_ban(guild: discord.Guild, user: discord.User):
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

    @bot.event
    async def on_member_unban(guild: discord.Guild, user: discord.User):
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

    @bot.event
    async def on_invite_create(invite: discord.Invite):
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

    @bot.event
    async def on_invite_delete(invite: discord.Invite):
        await send_log_embed(
            invite.guild,
            "server",
            "🗑️ Инвайт удалён",
            f"{invite.code}",
            color=Color.red(),
            fields=[("Канал", invite.channel.mention if invite.channel else "—", False)]
        )

    @bot.event
    async def on_guild_update(before: discord.Guild, after: discord.Guild):
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

    @bot.event
    async def on_reaction_add(reaction: discord.Reaction, user: discord.User | discord.Member):
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

    @bot.event
    async def on_reaction_remove(reaction: discord.Reaction, user: discord.User | discord.Member):
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

    @bot.event
    async def on_command(ctx: commands.Context):
        await send_log_embed(
            ctx.guild,
            "commands",
            "⌨️ Команда",
            f"{ctx.author} использовал `{ctx.message.content}`",
            color=Color.blurple(),
            fields=[("Канал", ctx.channel.mention, False)]
        )

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        await send_log_embed(
            ctx.guild,
            "errors",
            "❗ Ошибка команды",
            f"{ctx.author}: `{ctx.message.content}`",
            color=Color.red(),
            fields=[("Ошибка", str(error), False)]
        )

    @bot.event
    async def on_interaction(interaction: discord.Interaction):
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
