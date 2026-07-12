import asyncio
import os
import re
import time
import uuid
from typing import cast
import discord
from discord import Embed, Color
from discord.ext import commands
import logging

from config import (
    FORM_CHANNEL_ID,
    TAC_CHANNEL_ID,
    TAC_REVIEWER_ROLE_IDS,
    TAC_ROLE_REWARDS,
    PSC_CHANNEL_ID,
    PING_ROLE_ID,
    COMPLAINT_INPUT_CHANNEL,
    COMPLAINT_NOTIFY_ROLE,
    form_channel_id,
    punishment_roles,
    squad_roles,
    TARGET_CHANNELS,
    TARGET_OUTPUT_CHANNEL,
    allowed_role_ids,
)
from forms import ConfirmView, ComplaintView
from utils import (
    extract_clean_keyword,
    strip_leading_enumeration,
    assess_applicant_risk,
    assess_roblox_account,
    classify_applicant_danger,
    safe_send_dm,
)
from logging_utils import send_log_embed

logger = logging.getLogger(__name__)

FORM_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
FORM_MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
FORM_MAX_ATTACHMENTS = 5
COMPLAINT_COOLDOWN_SECONDS = 10 * 60
_COMPLAINT_TOPIC_PREFIX = "p.os-complaint-owner:"


def _is_authorized_staff(member: discord.Member) -> bool:
    permissions = member.guild_permissions
    if permissions.administrator or permissions.manage_guild or permissions.manage_roles or permissions.manage_messages:
        return True
    trusted_role_ids = set(allowed_role_ids) | set(TAC_REVIEWER_ROLE_IDS)
    return any(role.id in trusted_role_ids for role in member.roles)


def _validate_form_attachments(attachments: list[discord.Attachment]) -> str | None:
    if len(attachments) > FORM_MAX_ATTACHMENTS:
        return f"Можно приложить не больше {FORM_MAX_ATTACHMENTS} файлов."
    total = 0
    for attachment in attachments:
        size = int(attachment.size or 0)
        if size > FORM_MAX_ATTACHMENT_BYTES:
            return f"Файл `{attachment.filename}` больше {FORM_MAX_ATTACHMENT_BYTES // (1024 * 1024)} МБ."
        total += size
    if total > FORM_MAX_TOTAL_ATTACHMENT_BYTES:
        return f"Суммарный размер файлов больше {FORM_MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} МБ."
    return None

class FormsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._complaint_last_submit: dict[tuple[int, int], float] = {}
        self._complaint_locks: dict[int, asyncio.Lock] = {}

    def _complaint_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._complaint_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._complaint_locks[guild_id] = lock
        return lock

    @staticmethod
    def _find_open_complaint(guild: discord.Guild, user_id: int) -> discord.TextChannel | None:
        topic = f"{_COMPLAINT_TOPIC_PREFIX}{user_id}"
        return next((channel for channel in guild.text_channels if (channel.topic or "") == topic), None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if not message.guild or author.bot or not isinstance(author, discord.Member):
            return
        author_member = cast(discord.Member, author)

        # Авто-отписки
        try:
            if message.channel.id in TARGET_CHANNELS and "ОТПИСКИ" in (message.content or "").upper():
                if not _is_authorized_staff(author_member):
                    return
                today = discord.utils.utcnow().strftime("%d.%m.%Y")
                group = TARGET_CHANNELS[message.channel.id]
                title = f"Общее мероприятие [P.S.C] ({today}) - Отписки." if group == "P.S.C" else f"Мероприятие [{group}] ({today}) - Отписки."
                embed = Embed(title=title, color=Color.from_rgb(255, 255, 255))
                target_channel = self.bot.get_channel(TARGET_OUTPUT_CHANNEL)
                if isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                    try:
                        sent = await target_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                        try:
                            await sent.create_thread(name=f"Отписки • {group}")
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"Ошибка при отправке эмбеда/создании ветки: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Ошибка авто-отписок: {e}", exc_info=True)

        # PSC EMBED СООБЩЕНИЯ
        if message.channel.id == PSC_CHANNEL_ID:
            content_strip = (message.content or "").strip()
            if content_strip.upper().startswith("С ВЕБХУКОМ"):
                if not _is_authorized_staff(author_member):
                    await message.reply("❌ У вас нет прав для служебной публикации.", mention_author=False)
                    return

                if len(message.attachments) > 1:
                    await message.reply("❌ Для служебной публикации можно приложить один файл.", mention_author=False)
                    return
                attachment_error = _validate_form_attachments(list(message.attachments[:1]))
                if attachment_error:
                    await message.reply(f"❌ {attachment_error}", mention_author=False)
                    return

                role_ping = message.guild.get_role(PING_ROLE_ID)
                if role_ping:
                    try:
                        await message.channel.send(
                            role_ping.mention,
                            allowed_mentions=discord.AllowedMentions(roles=[role_ping]),
                        )
                    except Exception:
                        pass

                content_without_flag = content_strip[len("С ВЕБХУКОМ"):].strip()
                file_to_send = None
                embed = Embed(description=content_without_flag or "(без текста)", color=Color.from_rgb(255, 255, 255))
                embed.set_footer(text=f"©Provision Security Complex | {discord.utils.utcnow().strftime('%d.%m.%Y')}")
                if message.attachments:
                    att = message.attachments[0]
                    try:
                        safe_filename = os.path.basename(att.filename or f"attachment-{uuid.uuid4().hex}")
                        file_to_send = await att.to_file(filename=safe_filename)
                        if (att.content_type or "").lower().startswith("image/"):
                            embed.set_image(url=f"attachment://{safe_filename}")
                    except Exception as e:
                        logger.error(f"Failed to save attachment for PSC embed: {e}", exc_info=True)
                        file_to_send = None

                try:
                    if file_to_send:
                        await message.channel.send(embed=embed, file=file_to_send)
                    else:
                        await message.channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Failed to send PSC embed: {e}", exc_info=True)
                    return
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        # Жалобы
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
                return

            attachment_error = _validate_form_attachments(list(message.attachments))
            if attachment_error:
                await message.reply(f"❌ {attachment_error}", mention_author=False)
                return

            guild = message.guild
            category = message.channel.category if isinstance(message.channel, discord.TextChannel) else None
            cooldown_key = (guild.id, message.author.id)
            now = time.monotonic()
            last_submit = self._complaint_last_submit.get(cooldown_key, 0.0)
            remaining = COMPLAINT_COOLDOWN_SECONDS - (now - last_submit)
            if remaining > 0:
                await message.reply(
                    f"⏳ Новую жалобу можно отправить через {max(1, int(remaining // 60) + 1)} мин.",
                    mention_author=False,
                )
                return

            open_complaint = self._find_open_complaint(guild, message.author.id)
            if open_complaint:
                await message.reply(
                    f"У вас уже есть открытая жалоба: {open_complaint.mention}.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            notify_role = guild.get_role(COMPLAINT_NOTIFY_ROLE)

            overwrites: dict[
                discord.Role | discord.Member | discord.Object,
                discord.PermissionOverwrite,
            ] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                author_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            }
            if guild.me:
                overwrites[guild.me] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                )
            if notify_role:
                overwrites[notify_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
            for role in guild.roles:
                try:
                    if role.permissions.administrator or role.permissions.manage_guild:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                except Exception:
                    pass

            complaint_chan: discord.TextChannel | None = None
            async with self._complaint_lock(guild.id):
                open_complaint = self._find_open_complaint(guild, message.author.id)
                if open_complaint:
                    await message.reply(
                        f"У вас уже есть открытая жалоба: {open_complaint.mention}.",
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return

                existing_numbers = []
                for channel in (category.channels if category else guild.channels):
                    match = re.match(r"жалоба-(\d+)$", channel.name)
                    if match:
                        existing_numbers.append(int(match.group(1)))
                index = (max(existing_numbers) + 1) if existing_numbers else 1
                channel_name = f"жалоба-{index}"
                try:
                    complaint_chan = await guild.create_text_channel(
                        channel_name,
                        overwrites=overwrites,
                        category=category,
                        topic=f"{_COMPLAINT_TOPIC_PREFIX}{message.author.id}",
                        reason=f"Новая жалоба от {message.author}",
                    )
                except Exception as exc:
                    logger.error("Failed to create complaint channel: %s", exc, exc_info=True)

            if not complaint_chan:
                await message.reply(
                    "⚠️ Не удалось создать приватный канал жалобы. Исходное сообщение сохранено; сообщите администрации.",
                    mention_author=False,
                )
                return

            embed = Embed(title="📢 Новая жалоба", color=Color.blue())
            embed.add_field(name="Отправитель", value=f"{message.author.mention} (ID: {message.author.id})", inline=False)
            full_text = "\n".join(lines)
            embed.add_field(name="Жалоба", value=f"```{full_text[:1900]}```", inline=False)
            embed.set_footer(text=f"ID жалобы: {index} | {discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}")

            try:
                if notify_role:
                    await complaint_chan.send(
                        notify_role.mention,
                        allowed_mentions=discord.AllowedMentions(roles=[notify_role]),
                    )
                complaint_view = ComplaintView(submitter=message.author, complaint_channel_id=complaint_chan.id)
                await complaint_chan.send(
                    embed=embed,
                    view=complaint_view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

                if message.attachments:
                    files = [await attachment.to_file() for attachment in message.attachments]
                    await complaint_chan.send(
                        content="📎 Приложенные доказательства от автора жалобы:",
                        files=files,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except Exception as exc:
                logger.error("Failed to hand off complaint: %s", exc, exc_info=True)
                try:
                    await complaint_chan.delete(reason="Откат неполной жалобы P.OS")
                except Exception:
                    pass
                await message.reply(
                    "⚠️ Не удалось надежно перенести жалобу и доказательства. Исходное сообщение сохранено.",
                    mention_author=False,
                )
                return

            self._complaint_last_submit[cooldown_key] = time.monotonic()
            try:
                await message.delete()
            except Exception:
                pass

            try:
                await send_log_embed(
                    guild,
                    "forms",
                    "📢 Новая жалоба",
                    f"Создан канал жалобы для {message.author.mention}.",
                    color=Color.blue(),
                    fields=[
                        ("Канал", complaint_chan.mention, False),
                        ("Категория", category.name if category else "—", False),
                    ],
                )
            except Exception:
                pass
            return

        # Форма Arbaiter
        if message.channel.id == FORM_CHANNEL_ID:
            template = (
                "1. Ваш Роблокс никнейм  (не дисплей)\n"
                "2. Ваш Дискорд никнейм  (не дисплей)\n"
                "3. Почему решили вступить в отряд ?\n"
                "4. Отряд , в который в желаете вступить : Arbaiter"
            )
            lines = [line.strip() for line in (message.content or "").strip().split("\n") if line.strip()]
            if len(lines) != 4:
                try:
                    embed = Embed(
                        title="❌ Неверный шаблон",
                        description="Форма должна содержать четыре строки.",
                        color=Color.red()
                    )
                    embed.add_field(name="Пример", value=f"```{template}```", inline=False)
                    await message.reply(embed=embed)
                except Exception:
                    pass
                return

            user_line, discord_nick_line, why_line, choice_line = lines
            # Срезаем ведущий номер пункта ("1.", "2)") — в проверку Roblox/DS
            # должен идти только сам ник, а не номер строки из шаблона.
            user_line = strip_leading_enumeration(user_line)
            discord_nick_line = strip_leading_enumeration(discord_nick_line)
            keyword = extract_clean_keyword(choice_line)

            # Принимаем разные написания отряда: arbaiter / arbeiter (нем.) и
            # кириллические арбайтер / арбейтер. Раньше принималось только
            # "arbaiter", из-за чего правильное "Arbeiter"/"Арбайтер" отклонялось.
            arbaiter_aliases = ("arbaiter", "arbeiter", "арбайтер", "арбейтер")
            if not any(alias in keyword for alias in arbaiter_aliases):
                try:
                    embed = Embed(
                        title="❌ Неверный шаблон",
                        description="Форма должна содержать четыре строки.",
                        color=Color.red()
                    )
                    embed.add_field(name="Пример", value=f"```{template}```", inline=False)
                    await message.reply(embed=embed)
                except Exception:
                    pass
                return

            # Канал-приёмник заявок: get_channel, при промахе кэша — fetch_channel.
            target_channel = message.guild.get_channel(TAC_CHANNEL_ID)
            if not target_channel:
                try:
                    target_channel = await self.bot.fetch_channel(TAC_CHANNEL_ID)
                except Exception:
                    target_channel = None
            if not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
                logger.error(f"Arbaiter: канал-приёмник {TAC_CHANNEL_ID} не найден или не текстовый.")
                try:
                    await message.reply(
                        "⚠️ Заявка принята, но канал рассмотрения временно недоступен — "
                        "сообщи администрации."
                    )
                except Exception:
                    pass
                return

            # --- Вердикт: проверяем Discord-аккаунт и Roblox-аккаунт по логину ---
            # Любая ошибка проверки НЕ должна ронять заявку — она всё равно уйдёт
            # проверяющим, просто без авто-вердикта.
            try:
                discord_flags = assess_applicant_risk(user_line, discord_nick_line, author_member)
            except Exception as e:
                logger.error(f"Arbaiter: ошибка assess_applicant_risk: {e}", exc_info=True)
                discord_flags = []
            try:
                roblox = await assess_roblox_account(user_line)
            except Exception as e:
                logger.error(f"Arbaiter: ошибка assess_roblox_account: {e}", exc_info=True)
                roblox = {"found": None, "flags": []}
            try:
                danger_level, too_dangerous = classify_applicant_danger(discord_flags, roblox)
            except Exception as e:
                logger.error(f"Arbaiter: ошибка classify_applicant_danger: {e}", exc_info=True)
                danger_level, too_dangerous = "low", False

            discord_text = "\n".join(f"⚠️ {flag}" for flag in discord_flags) or "✅ Явных рисков не обнаружено"

            # Блок Roblox-аккаунта
            if roblox.get("found") is True:
                age = roblox.get("age_days")
                age_text = f"{age} дн." if age is not None else "неизвестно"
                roblox_lines = [
                    f"Логин: `{roblox.get('name') or user_line}`"
                    + (f" (дисплей: {roblox['display_name']})" if roblox.get("display_name") else ""),
                    f"Возраст аккаунта: {age_text}",
                    f"Бан: {'🚫 ДА' if roblox.get('banned') else 'нет'}",
                ]
                if roblox.get("profile_url"):
                    roblox_lines.append(f"[Профиль Roblox]({roblox['profile_url']})")
            elif roblox.get("found") is False:
                roblox_lines = [f"❌ Roblox-аккаунт `{user_line}` не найден"]
            else:
                roblox_lines = [f"❓ Не удалось проверить Roblox `{user_line}` (API недоступен)"]
            for flag in roblox.get("flags", []):
                roblox_lines.append(f"⚠️ {flag}")
            roblox_text = "\n".join(roblox_lines)

            color = {"high": Color.red(), "medium": Color.gold(), "low": Color.green()}.get(danger_level, Color.blue())
            title = "📋 Заявка в Arbaiter"
            if too_dangerous:
                title = "🚨 ОПАСНЫЙ КАНДИДАТ — заявка в Arbaiter"

            embed = Embed(
                title=title,
                description=(
                    f"{message.author.mention} хочет вступить в отряд **Arbaiter**\n"
                    f"Roblox ник: `{user_line}`\n"
                    f"Discord ник: `{discord_nick_line}`\n"
                    f"Почему решили вступить: {why_line}"
                ),
                color=color,
            )
            if too_dangerous:
                embed.add_field(
                    name="🚨 ВНИМАНИЕ",
                    value="Кандидат помечен как **потенциально опасный**. Проверьте вручную перед принятием.",
                    inline=False,
                )
            embed.add_field(name="🔎 Проверка Discord", value=discord_text[:1024], inline=False)
            embed.add_field(name="🎮 Проверка Roblox", value=roblox_text[:1024], inline=False)
            embed.set_footer(
                text=f"ID пользователя: {message.author.id} | уровень риска: {danger_level.upper()} | "
                f"{discord.utils.utcnow().strftime('%d.%m.%Y %H:%M:%S')}"
            )

            confirm_view = ConfirmView(
                TAC_REVIEWER_ROLE_IDS,
                target_message=message,
                squad_name="Arbaiter",
                role_ids=TAC_ROLE_REWARDS,
                target_user_id=message.author.id
            )

            mentions = []
            for rid in TAC_REVIEWER_ROLE_IDS:
                reviewer_role = message.guild.get_role(rid)
                if reviewer_role:
                    mentions.append(reviewer_role.mention)

            try:
                if mentions:
                    allowed_roles = [
                        allowed_role
                        for role_id in TAC_REVIEWER_ROLE_IDS
                        if (allowed_role := message.guild.get_role(role_id)) is not None
                    ]
                    await target_channel.send(
                        content=" ".join(mentions),
                        embed=embed,
                        view=confirm_view,
                        allowed_mentions=discord.AllowedMentions(roles=allowed_roles),
                    )
                else:
                    await target_channel.send(
                        embed=embed,
                        view=confirm_view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except Exception as e:
                logger.error(f"Failed to send Arbaiter form embed: {e}", exc_info=True)
                await message.reply(
                    "⚠️ Не удалось передать заявку проверяющим. Исходное сообщение сохранено; сообщите администрации.",
                    mention_author=False,
                )
                return

            try:
                await message.reply(
                    "✅ **Ваша заявка на стадии рассмотрения**\n\n"
                    "Заявку проверит человек; это может занять некоторое время.",
                    mention_author=False,
                )
            except Exception:
                pass
            return

        # Форма наказаний
        if message.channel.id == form_channel_id:
            if not _is_authorized_staff(author_member):
                await message.reply("❌ У вас нет прав для применения наказаний.", mention_author=False)
                return

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
                return

            valid_punishments = {"1 выговор", "2 выговора", "1 страйк", "2 страйка"}
            if punishment not in valid_punishments:
                await message.reply(
                    "❌ Неизвестное наказание. Допустимо: 1 выговор, 2 выговора, 1 страйк, 2 страйка.",
                    mention_author=False,
                )
                return
            if not reason:
                await message.reply("❌ Причина наказания не может быть пустой.", mention_author=False)
                return

            member = message.guild.get_member(user_id)
            if not member:
                try:
                    member = await message.guild.fetch_member(user_id)
                except Exception:
                    pass
            if not member:
                try:
                    await message.reply("❌ Пользователь с таким ID не найден на сервере.")
                except Exception:
                    pass
                return

            bot_member = message.guild.me
            if bot_member and member != message.guild.owner and member.top_role >= bot_member.top_role:
                await message.reply(
                    "❌ Роль цели находится не ниже роли P.OS; Discord не разрешит изменить наказания.",
                    mention_author=False,
                )
                return

            roles = member.roles
            role_errors: list[str] = []

            async def log_action(text, color=Color.orange()):
                details = (
                    f"{text}\nПричина: {reason[:500]}\n"
                    f"Оформил: {message.author.mention} (`{message.author.id}`)\n"
                    f"Указанный ник: `{nickname[:100]}`"
                )
                if role_errors:
                    details += "\n❌ Ошибки ролей: " + "; ".join(role_errors)
                    color = Color.red()
                await send_log_embed(
                    message.guild,
                    "moderation",
                    "📋 Лог наказаний" if not role_errors else "❌ Наказание применено не полностью",
                    details,
                    color=color
                )
                if role_errors:
                    await message.reply(
                        "⚠️ Наказание применено не полностью: " + "; ".join(role_errors),
                        mention_author=False,
                    )

            async def apply_roles(to_add, to_remove):
                roles_to_add = [role for role in to_add if role and role not in roles]
                roles_to_remove = [role for role in to_remove if role and role in roles]
                if roles_to_add:
                    try:
                        await member.add_roles(*roles_to_add, reason=f"P.OS punishment by {message.author}")
                    except Exception as exc:
                        names = ", ".join(role.name for role in roles_to_add)
                        role_errors.append(f"не удалось выдать {names}: {exc}")
                        logger.error("Failed to add punishment roles: %s", exc)
                        return False
                if roles_to_remove:
                    try:
                        await member.remove_roles(*roles_to_remove, reason=f"P.OS punishment by {message.author}")
                    except Exception as exc:
                        names = ", ".join(role.name for role in roles_to_remove)
                        role_errors.append(f"не удалось снять {names}: {exc}")
                        logger.error("Failed to remove punishment roles: %s", exc)
                        return False
                return True

            punish_1 = message.guild.get_role(punishment_roles["1 выговор"])
            punish_2 = message.guild.get_role(punishment_roles["2 выговора"])
            strike_1 = message.guild.get_role(punishment_roles["1 страйк"])
            strike_2 = message.guild.get_role(punishment_roles["2 страйка"])

            configured_roles = {
                "1 выговор": punish_1,
                "2 выговора": punish_2,
                "1 страйк": strike_1,
                "2 страйка": strike_2,
            }
            missing_roles = [name for name, role in configured_roles.items() if role is None]
            if missing_roles:
                await message.reply(
                    "❌ На сервере отсутствуют настроенные роли наказаний: " + ", ".join(missing_roles),
                    mention_author=False,
                )
                return
            punish_1 = cast(discord.Role, punish_1)
            punish_2 = cast(discord.Role, punish_2)
            strike_1 = cast(discord.Role, strike_1)
            strike_2 = cast(discord.Role, strike_2)
            configured_role_values = (punish_1, punish_2, strike_1, strike_2)
            if bot_member:
                unmanageable = [
                    role.name
                    for role in configured_role_values
                    if role.managed or role >= bot_member.top_role
                ]
                if unmanageable:
                    await message.reply(
                        "❌ P.OS не может управлять ролями из-за иерархии: " + ", ".join(unmanageable),
                        mention_author=False,
                    )
                    return

            if all(r in roles for r in [punish_1, punish_2, strike_1, strike_2]):
                notify = None
                if squad_roles["got_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["got_notify"])
                elif squad_roles["cesu_base"] in [r.id for r in roles]:
                    notify = message.guild.get_role(squad_roles["cesu_notify"])
                if notify:
                    await log_action(f"{notify.mention}\nСотрудник {member.mention} получил **максимальное количество наказаний** и подлежит **увольнению**.")
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
                    notify = None
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
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
                    notify = None
                    if squad_roles["got_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["got_notify"])
                    elif squad_roles["cesu_base"] in [r.id for r in roles]:
                        notify = message.guild.get_role(squad_roles["cesu_notify"])
                    if notify:
                        await log_action(f"{notify.mention}\nСотрудник {member.mention} получил 3-й страйк. Подлежит увольнению.")
                else:
                    await apply_roles([strike_1, strike_2], [])
                    await log_action(f"{member.mention} получил 2 страйка.")
            return

async def setup(bot: commands.Bot):
    await bot.add_cog(FormsCog(bot))
    # Persistent views: без регистрации кнопки заявок/жалоб умирали после каждого
    # рестарта бота («Взаимодействие не удалось»). Контекст восстанавливается
    # из embed сообщения внутри самих view.
    bot.add_view(ConfirmView(TAC_REVIEWER_ROLE_IDS))
    bot.add_view(ComplaintView())
