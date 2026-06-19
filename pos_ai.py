from __future__ import annotations

import base64
import datetime
import difflib
import io
import json
import random as _random
import re
import shutil
import asyncio
import time
from collections import defaultdict, deque
from typing import List, Optional

import discord
from PIL import Image, ImageOps
from discord.utils import escape_markdown, escape_mentions

# #4: Защита от декомпрессионных бомб (см. moderation.py).
Image.MAX_IMAGE_PIXELS = 24_000_000

from ai_client import (
    ai_cooldown_remaining,
    ai_is_temporarily_unavailable,
    ai_unavailable_reason,
    pos_chat_completion,
)
from config import (
    POS_AI_API_KEY,
    POS_AI_MAX_TOKENS,
    POS_AI_MODEL,
    POS_AI_PROVIDER,
    POS_AI_SYSTEM_PROMPT,
    POS_AI_TIMEOUT_SECONDS,
    POS_AI_TOP_P,
    POS_AI_TEMPERATURE,
    POS_OWNER_USER_IDS,
)
from commands import generate_gif_from_attachments, parse_gif_options_from_text
from logging_utils import is_log_channel, setup_guild_logging
from storage import add_entry, delete_entry, list_entries, get_ai_context, update_ai_context, is_ai_muted, set_ai_muted_user
from cogs.ai_tools import POS_AI_TOOLS


# --- Константа: инструменты только для владельца ---
_OWNER_ONLY_TOOLS = frozenset({
    "ban_user", "unban_user", "timeout_user", "kick_user", "set_nickname",
    "add_role", "remove_role",
    "create_role", "delete_role", "edit_role",
    "create_channel", "delete_channel", "edit_channel", "set_channel_permission",
    "create_invite", "delete_messages",
    "setup_logging",
    "mute_ai_for_user", "unmute_ai_for_user",
})


def _normalize_role_name(name: str) -> str:
    """Приводит имя роли к виду для нечёткого сравнения: только буквы/цифры, нижний
    регистр. Убирает эмодзи, пробелы, пунктуацию и декоративные символы."""
    return re.sub(r"[^0-9a-zа-яё]", "", (name or "").lower())


def resolve_role_smart(guild: discord.Guild, ident: str) -> discord.Role | None:
    """Находит роль по ID, точному имени, нормализованному имени, подстроке или
    нечёткому совпадению. Возвращает None, если уверенного совпадения нет."""
    if not ident:
        return None
    ident = str(ident).strip()

    # 1. По ID (в т.ч. формат <@&123>)
    digits = re.sub(r"[^0-9]", "", ident)
    if digits and ident.replace("<@&", "").replace(">", "").strip().isdigit():
        role = guild.get_role(int(digits))
        if role:
            return role

    roles = [r for r in guild.roles if r.name != "@everyone"]

    # 2. Точное совпадение имени (без учёта регистра)
    lowered = ident.lower()
    for r in roles:
        if r.name.lower() == lowered:
            return r

    # 3. Нормализованное совпадение (без эмодзи/пробелов/пунктуации)
    norm = _normalize_role_name(ident)
    if norm:
        for r in roles:
            if _normalize_role_name(r.name) == norm:
                return r

    # 4. Подстрока (предпочитаем самое длинное имя роли, чтобы не цеплять короткие)
    if norm:
        substring_hits = [r for r in roles if norm in _normalize_role_name(r.name) or _normalize_role_name(r.name) in norm]
        if len(substring_hits) == 1:
            return substring_hits[0]
        if substring_hits:
            return max(substring_hits, key=lambda r: len(r.name))

    # 5. Нечёткое совпадение по нормализованным именам
    if norm:
        normalized_map = {_normalize_role_name(r.name): r for r in roles if _normalize_role_name(r.name)}
        close = difflib.get_close_matches(norm, list(normalized_map.keys()), n=1, cutoff=0.82)
        if close:
            return normalized_map[close[0]]

    return None


def _role_not_found_hint(guild: discord.Guild, ident: str) -> str:
    """Сообщение об ошибке с подсказкой похожих ролей, чтобы модель могла
    повторить вызов с точным именем вместо «не знаю такую роль»."""
    roles = [r.name for r in guild.roles if r.name != "@everyone"]
    norm = _normalize_role_name(ident)
    suggestions: list[str] = []
    if norm:
        normalized_map = {_normalize_role_name(n): n for n in roles if _normalize_role_name(n)}
        close = difflib.get_close_matches(norm, list(normalized_map.keys()), n=3, cutoff=0.5)
        suggestions = [normalized_map[c] for c in close]
    if suggestions:
        return (
            f"Роль '{ident}' не найдена. Возможно, ты имел в виду: "
            + ", ".join(f"'{s}'" for s in suggestions)
            + ". Повтори вызов с точным именем или ID роли."
        )
    return f"Ошибка: роль '{ident}' не найдена на сервере."


def resolve_channel_smart(guild: discord.Guild, ident: str) -> discord.abc.GuildChannel | None:
    """Находит канал/категорию по ID, точному имени, нормализованному имени или
    подстроке. Возвращает None, если уверенного совпадения нет."""
    if not ident:
        return None
    ident = str(ident).strip()

    # 1. По ID (в т.ч. формат <#123>)
    digits = re.sub(r"[^0-9]", "", ident)
    if digits and ident.replace("<#", "").replace(">", "").strip().isdigit():
        ch = guild.get_channel(int(digits))
        if ch:
            return ch

    channels = list(guild.channels)

    # 2. Точное совпадение имени (без учёта регистра)
    lowered = ident.lower()
    for ch in channels:
        if ch.name.lower() == lowered:
            return ch

    # 3. Нормализованное совпадение (без эмодзи/пунктуации)
    norm = _normalize_role_name(ident)
    if norm:
        for ch in channels:
            if _normalize_role_name(ch.name) == norm:
                return ch

    # 4. Подстрока (единственное совпадение, иначе самое длинное имя)
    if norm:
        hits = [ch for ch in channels if norm in _normalize_role_name(ch.name) or _normalize_role_name(ch.name) in norm]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return max(hits, key=lambda c: len(c.name))
    return None


def _parse_bool(value, default: bool = False) -> bool:
    return str(value).strip().lower() in {"true", "1", "да", "yes", "on", "вкл"} if value not in (None, "") else default


_TOOL_ACTION_LABELS = {
    "ban_user": "бан пользователя", "unban_user": "разбан пользователя", "timeout_user": "мут (тайм-аут)",
    "kick_user": "кик пользователя", "set_nickname": "смену никнейма",
    "add_role": "выдачу роли", "remove_role": "снятие роли",
    "create_role": "создание роли", "delete_role": "удаление роли", "edit_role": "изменение роли",
    "create_channel": "создание канала", "delete_channel": "удаление канала",
    "edit_channel": "изменение канала", "set_channel_permission": "настройку прав канала",
    "create_invite": "создание приглашения",
    "delete_messages": "удаление сообщений",
    "setup_logging": "разворачивание системы логов",
    "mute_ai_for_user": "блокировку ответов", "unmute_ai_for_user": "снятие блокировки",
}


async def _resolve_member(guild: discord.Guild, user_id: int | None):
    if not user_id:
        return None
    member = guild.get_member(user_id)
    if not member:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    return member


async def _perform_tool_action(
    bot: discord.Client,
    message: discord.Message,
    name: str,
    args: dict,
    user_id: int | None,
) -> str:
    """Чистое выполнение инструмента — БЕЗ проверки прав. Проверка прав и
    подтверждение владельца выполняются в execute_pos_tool ДО вызова этой функции.

    Защита владельца/бота как цели остаётся здесь, чтобы её нельзя было обойти
    даже через подтверждённый запрос.
    """
    guild = message.guild
    if guild is None:
        return "Ошибка: инструмент можно использовать только на сервере."

    # Защита владельца и самого бота от действий, нацеленных на пользователя.
    _protected_ids = set(POS_OWNER_USER_IDS)
    if bot.user:
        _protected_ids.add(bot.user.id)
    if user_id and user_id in _protected_ids and name in {
        "ban_user", "timeout_user", "kick_user", "add_role", "remove_role", "set_nickname", "mute_ai_for_user"
    }:
        return "Ошибка: это действие нельзя применить к владельцу или к самому боту."

    if name == "ban_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        reason = args.get("reason", "Бан от P.OS")
        try:
            await guild.ban(discord.Object(id=user_id), reason=reason)
            return f"Пользователь {user_id} успешно забанен."
        except Exception as e:
            return f"Ошибка при бане: {e}"

    elif name == "unban_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        try:
            await guild.unban(discord.Object(id=user_id))
            return f"Пользователь {user_id} успешно разбанен."
        except Exception as e:
            return f"Ошибка при разбане: {e}"

    elif name == "timeout_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        try:
            minutes = max(1, min(int(args.get("minutes", 10)), 40320))
        except (ValueError, TypeError):
            minutes = 10
        reason = args.get("reason", "Тайм-аут от P.OS")
        member = await _resolve_member(guild, user_id)
        if not member:
            return f"Ошибка: пользователь {user_id} не найден на сервере."
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
            await member.timeout(until, reason=reason)
            return f"Пользователю {user_id} выдан тайм-аут на {minutes} минут."
        except Exception as e:
            return f"Ошибка при муте: {e}"

    elif name == "kick_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        reason = args.get("reason", "Кик от P.OS")
        member = await _resolve_member(guild, user_id)
        if not member:
            return f"Ошибка: пользователь {user_id} не найден на сервере."
        try:
            await member.kick(reason=reason)
            return f"Пользователь {user_id} ({member.name}) кикнут с сервера."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для кика (проверь иерархию ролей)."
        except Exception as e:
            return f"Ошибка при кике: {e}"

    elif name == "set_nickname":
        if not user_id:
            return "Ошибка: не указан user_id"
        member = await _resolve_member(guild, user_id)
        if not member:
            return f"Ошибка: пользователь {user_id} не найден на сервере."
        new_nick = str(args.get("nickname", "")).strip() or None
        try:
            await member.edit(nick=new_nick, reason="Смена ника от P.OS")
            return f"Никнейм пользователя {user_id} изменён на '{new_nick or member.name}'."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для смены ника (проверь иерархию ролей)."
        except Exception as e:
            return f"Ошибка при смене ника: {e}"

    elif name == "add_role":
        if not user_id:
            return "Ошибка: не указан user_id"
        role_ident = str(args.get("role_id_or_name", ""))
        member = await _resolve_member(guild, user_id)
        if not member:
            return "Ошибка: пользователь не найден."
        role = resolve_role_smart(guild, role_ident)
        if not role:
            return _role_not_found_hint(guild, role_ident)
        try:
            await member.add_roles(role, reason="Выдано P.OS")
            return f"Роль {role.name} успешно выдана пользователю {user_id}."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы выдать роль '{role.name}'. Проверь, что роль P.OS выше неё в иерархии."
        except Exception as e:
            return f"Ошибка при выдаче роли: {e}"

    elif name == "remove_role":
        if not user_id:
            return "Ошибка: не указан user_id"
        role_ident = str(args.get("role_id_or_name", ""))
        member = await _resolve_member(guild, user_id)
        if not member:
            return "Ошибка: пользователь не найден."
        role = resolve_role_smart(guild, role_ident)
        if not role:
            return _role_not_found_hint(guild, role_ident)
        try:
            await member.remove_roles(role, reason="Снято P.OS")
            return f"Роль {role.name} успешно снята с пользователя {user_id}."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы снять роль '{role.name}'. Проверь иерархию ролей."
        except Exception as e:
            return f"Ошибка при снятии роли: {e}"

    elif name == "create_role":
        role_name = str(args.get("name", "")).strip()
        if not role_name:
            return "Ошибка: не указано имя роли (name)."
        existing = resolve_role_smart(guild, role_name)
        if existing and existing.name.lower() == role_name.lower():
            return f"Роль с именем '{existing.name}' уже существует (ID {existing.id})."
        color = discord.Color.default()
        color_raw = str(args.get("color", "")).strip().lstrip("#")
        if color_raw:
            try:
                color = discord.Color(int(color_raw, 16))
            except (ValueError, TypeError):
                color = discord.Color.default()
        hoist = _parse_bool(args.get("hoist"))
        mentionable = _parse_bool(args.get("mentionable"))
        try:
            new_role = await guild.create_role(
                name=role_name, color=color, hoist=hoist, mentionable=mentionable, reason="Создано P.OS",
            )
            return f"Роль '{new_role.name}' создана (ID {new_role.id})."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для создания роли (нужно право «Управление ролями»)."
        except Exception as e:
            return f"Ошибка при создании роли: {e}"

    elif name == "edit_role":
        role_ident = str(args.get("role_id_or_name", ""))
        role = resolve_role_smart(guild, role_ident)
        if not role:
            return _role_not_found_hint(guild, role_ident)
        if role.is_default() or role.managed:
            return f"Ошибка: роль '{role.name}' системная/управляется интеграцией — её нельзя изменить."
        kwargs: dict = {}
        if str(args.get("new_name", "")).strip():
            kwargs["name"] = str(args["new_name"]).strip()
        color_raw = str(args.get("color", "")).strip().lstrip("#")
        if color_raw:
            try:
                kwargs["colour"] = discord.Color(int(color_raw, 16))
            except (ValueError, TypeError):
                pass
        if args.get("hoist") not in (None, ""):
            kwargs["hoist"] = _parse_bool(args.get("hoist"))
        if args.get("mentionable") not in (None, ""):
            kwargs["mentionable"] = _parse_bool(args.get("mentionable"))
        if str(args.get("position", "")).strip():
            try:
                kwargs["position"] = max(1, int(args["position"]))
            except (ValueError, TypeError):
                pass
        perms_raw = str(args.get("permissions", "")).strip()
        if perms_raw:
            perm_kwargs = {}
            for token in re.split(r"[,\s]+", perms_raw):
                token = token.strip().lower()
                if token and hasattr(discord.Permissions, token):
                    perm_kwargs[token] = True
            if perm_kwargs:
                try:
                    kwargs["permissions"] = discord.Permissions(**perm_kwargs)
                except Exception:
                    pass
        if not kwargs:
            return "Ошибка: не указано ни одного поля для изменения роли."
        try:
            await role.edit(reason="Изменено P.OS", **kwargs)
            return f"Роль '{role.name}' обновлена ({', '.join(kwargs.keys())})."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы изменить роль '{role.name}'. Проверь иерархию ролей."
        except Exception as e:
            return f"Ошибка при изменении роли: {e}"

    elif name == "delete_role":
        role_ident = str(args.get("role_id_or_name", ""))
        if not role_ident:
            return "Ошибка: не указана роль (role_id_or_name)."
        role = resolve_role_smart(guild, role_ident)
        if not role:
            return _role_not_found_hint(guild, role_ident)
        if role.is_default() or role.managed:
            return f"Ошибка: роль '{role.name}' системная или управляется интеграцией — её нельзя удалить."
        role_name = role.name
        try:
            await role.delete(reason="Удалено P.OS")
            return f"Роль '{role_name}' удалена с сервера."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы удалить роль '{role_name}'. Проверь иерархию ролей."
        except Exception as e:
            return f"Ошибка при удалении роли: {e}"

    elif name == "create_channel":
        ch_name = str(args.get("name", "")).strip()
        if not ch_name:
            return "Ошибка: не указано имя канала (name)."
        ch_type = str(args.get("type", "text")).strip().lower()
        category = None
        cat_ident = str(args.get("category_id_or_name", "")).strip()
        if cat_ident:
            resolved_cat = resolve_channel_smart(guild, cat_ident)
            if isinstance(resolved_cat, discord.CategoryChannel):
                category = resolved_cat
        topic = str(args.get("topic", "")).strip()
        try:
            new_ch: discord.abc.GuildChannel
            if ch_type in {"voice", "голос", "голосовой"}:
                new_ch = await guild.create_voice_channel(ch_name, category=category, reason="Создано P.OS")
            elif ch_type in {"category", "категория"}:
                new_ch = await guild.create_category(ch_name, reason="Создано P.OS")
            else:
                new_ch = await guild.create_text_channel(
                    ch_name, category=category,
                    topic=topic or discord.utils.MISSING,
                    reason="Создано P.OS",
                )
            return f"Канал '{new_ch.name}' создан (ID {new_ch.id})."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для создания канала (нужно «Управление каналами»)."
        except Exception as e:
            return f"Ошибка при создании канала: {e}"

    elif name == "delete_channel":
        ch_ident = str(args.get("channel_id_or_name", ""))
        channel = resolve_channel_smart(guild, ch_ident)
        if not channel:
            return f"Ошибка: канал '{ch_ident}' не найден на сервере."
        ch_name = channel.name
        try:
            await channel.delete(reason="Удалено P.OS")
            return f"Канал '{ch_name}' удалён."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы удалить канал '{ch_name}'."
        except Exception as e:
            return f"Ошибка при удалении канала: {e}"

    elif name == "edit_channel":
        ch_ident = str(args.get("channel_id_or_name", ""))
        channel = resolve_channel_smart(guild, ch_ident)
        if not channel:
            return f"Ошибка: канал '{ch_ident}' не найден на сервере."
        kwargs = {}
        if str(args.get("new_name", "")).strip():
            kwargs["name"] = str(args["new_name"]).strip()
        if args.get("topic") not in (None, "") and isinstance(channel, discord.TextChannel):
            kwargs["topic"] = str(args["topic"])
        if str(args.get("slowmode_seconds", "")).strip() and isinstance(channel, discord.TextChannel):
            try:
                kwargs["slowmode_delay"] = max(0, min(int(args["slowmode_seconds"]), 21600))
            except (ValueError, TypeError):
                pass
        if args.get("nsfw") not in (None, "") and isinstance(channel, discord.TextChannel):
            kwargs["nsfw"] = _parse_bool(args.get("nsfw"))
        cat_ident = str(args.get("category_id_or_name", "")).strip()
        if cat_ident:
            resolved_cat = resolve_channel_smart(guild, cat_ident)
            if isinstance(resolved_cat, discord.CategoryChannel):
                kwargs["category"] = resolved_cat
        if not kwargs:
            return "Ошибка: не указано ни одного поля для изменения канала."
        try:
            await channel.edit(reason="Изменено P.OS", **kwargs)
            return f"Канал '{channel.name}' обновлён ({', '.join(kwargs.keys())})."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы изменить канал '{channel.name}'."
        except Exception as e:
            return f"Ошибка при изменении канала: {e}"

    elif name == "set_channel_permission":
        ch_ident = str(args.get("channel_id_or_name", ""))
        channel = resolve_channel_smart(guild, ch_ident)
        if not channel:
            return f"Ошибка: канал '{ch_ident}' не найден на сервере."
        target_ident = str(args.get("target_role_or_user", "")).strip()
        allow = _parse_bool(args.get("allow"), default=True)
        target = resolve_role_smart(guild, target_ident)
        if not target:
            digits = re.sub(r"[^0-9]", "", target_ident)
            if digits:
                target = await _resolve_member(guild, int(digits))
        if not target:
            return f"Ошибка: цель '{target_ident}' (роль или пользователь) не найдена."
        overwrite = discord.PermissionOverwrite(view_channel=allow, send_messages=allow)
        try:
            await channel.set_permissions(target, overwrite=overwrite, reason="Настройка прав P.OS")
            verb = "открыт" if allow else "закрыт"
            return f"Доступ к каналу '{channel.name}' для '{getattr(target, 'name', target)}' {verb}."
        except discord.Forbidden:
            return f"Ошибка: недостаточно прав, чтобы менять доступ к каналу '{channel.name}'."
        except Exception as e:
            return f"Ошибка при настройке прав: {e}"

    elif name == "create_invite":
        ch_ident = str(args.get("channel_id_or_name", "")).strip()
        invite_channel = None
        if ch_ident:
            resolved = resolve_channel_smart(guild, ch_ident)
            if isinstance(resolved, (discord.TextChannel, discord.VoiceChannel)):
                invite_channel = resolved
        if not invite_channel:
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me) if guild.me else None
                if perms and perms.create_instant_invite:
                    invite_channel = ch
                    break
        if not invite_channel:
            return f"Нет доступных каналов для создания приглашения на сервере '{guild.name}'."
        try:
            invite = await invite_channel.create_invite(max_age=86400, max_uses=0, unique=True, reason="Создано P.OS")
            return f"Приглашение на сервер '{guild.name}': {invite.url} (действует 24 часа)."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для создания приглашения."
        except Exception as e:
            return f"Ошибка при создании приглашения: {e}"

    elif name == "delete_messages":
        msg_channel = message.channel
        if not isinstance(msg_channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            return "Ошибка: удаление сообщений доступно только в текстовых каналах."
        try:
            count = int(args.get("count", 0))
        except (ValueError, TypeError):
            count = 0
        if count < 1:
            return "Ошибка: укажи количество сообщений (count) от 1 до 100."
        count = min(count, 100)
        try:
            deleted = await msg_channel.purge(limit=count + 1, check=lambda m: m.id != message.id)
            num = len([m for m in deleted if m.id != message.id])
            return f"Удалено сообщений: {num}."
        except discord.Forbidden:
            return "Ошибка: недостаточно прав для удаления сообщений (нужно «Управление сообщениями»)."
        except Exception as e:
            return f"Ошибка при удалении сообщений: {e}"

    elif name == "setup_logging":
        category_name = str(args.get("category_name", "")).strip() or None
        try:
            ok, report = await setup_guild_logging(guild, category_name)
            return report if ok else f"Не удалось развернуть логи: {report}"
        except Exception as e:
            return f"Ошибка при развёртывании логов: {e}"

    elif name == "mute_ai_for_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        try:
            await set_ai_muted_user(user_id, guild.id, True)
            return f"Пользователь {user_id} добавлен в чёрный список."
        except Exception as e:
            return f"Ошибка базы данных: {e}"

    elif name == "unmute_ai_for_user":
        if not user_id:
            return "Ошибка: не указан user_id"
        try:
            await set_ai_muted_user(user_id, guild.id, False)
            return f"Пользователь {user_id} удалён из чёрного списка."
        except Exception as e:
            return f"Ошибка базы данных: {e}"

    return f"Неизвестный инструмент: {name}"


def _summarize_tool_call(name: str, args: dict, user_id: int | None) -> str:
    """Краткое человекочитаемое описание запрошенного действия для подтверждения."""
    label = _TOOL_ACTION_LABELS.get(name, name)
    details = []
    if user_id:
        details.append(f"пользователь `{user_id}`")
    for key in ("role_id_or_name", "name", "new_name", "channel_id_or_name", "target_role_or_user", "nickname", "reason", "minutes", "count"):
        val = args.get(key)
        if val:
            details.append(f"{key}={val}")
    tail = (" — " + ", ".join(details)) if details else ""
    return f"{label}{tail}"


async def execute_pos_tool(bot: discord.Client, message: discord.Message | None, tool_call: dict) -> str:
    if not message or not message.guild:
        return "Ошибка: инструмент можно использовать только на сервере."

    func = tool_call.get("function", {})
    name = func.get("name")
    args_raw = func.get("arguments", "{}")
    try:
        args = json.loads(args_raw)
    except Exception:
        args = {}

    raw_user_id = args.get("user_id")
    try:
        user_id = int(raw_user_id) if raw_user_id else None
    except (ValueError, TypeError):
        user_id = None

    # --- Гейт прав: управляющие инструменты — только владелец ---
    # В бете все управляющие действия доступны напрямую только владельцу.
    # Если запрос пришёл не от владельца — P.OS отправляет владельцу запрос
    # с кнопками «Разрешить»/«Запретить», и действие выполняется лишь по подтверждению.
    if name in _OWNER_ONLY_TOOLS and message.author.id not in POS_OWNER_USER_IDS:
        owner_id = POS_OWNER_USER_IDS[0] if POS_OWNER_USER_IDS else 968698192411652176
        owner = bot.get_user(owner_id)
        summary = _summarize_tool_call(name, args, user_id)
        requester = f"{message.author.display_name} (@{message.author.name}, ID: {message.author.id})"

        async def _executor():
            return await _perform_tool_action(bot, message, name, args, user_id)

        if owner:
            try:
                from forms import PosActionConfirmView
                view = PosActionConfirmView(
                    owner_user_ids=POS_OWNER_USER_IDS,
                    executor=_executor,
                    action_summary=summary,
                    requester_label=requester,
                )
                await owner.send(
                    f"⚠️ **Запрос на {summary}**\n"
                    f"Инициатор: {requester}\n"
                    f"Сервер: {message.guild.name}\n"
                    f"Контекст: {message.jump_url}\n\n"
                    f"Разрешить выполнение?",
                    view=view,
                )
                return f"Запрос на «{summary}» отправлен владельцу на подтверждение (кнопки Разрешить/Запретить в ЛС)."
            except Exception:
                return "Не удалось отправить запрос владельцу на подтверждение."
        return f"Отказано в доступе. Это действие в бете доступно только владельцу."

    # Владелец (или не-owner-only инструмент) — выполняем напрямую.
    return await _perform_tool_action(bot, message, name, args, user_id)


AI_COOLDOWN_SECONDS = 1.5  # уменьшен для живого диалога
AI_MAX_CONTEXT = 64
AI_MAX_CONTEXT_THREAD = 140
AI_MAX_RESPONSE_CHARS = 1900
AI_THREAD_TTL_SECONDS = 20 * 60
AI_CHANNEL_TTL_SECONDS = 8 * 60
AI_HISTORY_SCAN_LIMIT = 450
AI_MEMORY_MAX_MESSAGES = 500
AI_MEMORY_CONTEXT_MESSAGES = 45
AI_VISUAL_MAX_BYTES = 12 * 1024 * 1024
AI_VISUAL_MAX_SIDE = 1024
AI_GIF_MAX_FRAMES = 3

SYSTEM_INSTRUCTION = POS_AI_SYSTEM_PROMPT

_last_user_call: dict[int, float] = {}
_conversation_state: dict[int, dict] = {}
_last_rate_limit_notice: dict[int, float] = {}
_missing_key_warned = False
# In-memory per-(guild, user) message cache — populated by remember_server_message.
# Used by _format_author_profile to build behavioural context without hitting the DB.
_user_memory: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=20))

# --- #4: Максимальные размеры кэшей для предотвращения утечки памяти ---
_MAX_CACHE_SIZE = 5000
AI_NAME_PATTERN = re.compile(r"(?<!\w)(?:p[\s.\-_]*o[\s.\-_]*s|п[\s.\-_]*о[\s.\-_]*с)(?!\w)", re.IGNORECASE)
GIF_INTENT_PATTERN = re.compile(r"\b(сделай|создай|собери|сгенерируй|convert|make)\b.*\b(gif|гиф)\b|\b(gif|гиф)\b", re.IGNORECASE)
MUTE_PATTERN = re.compile(r"(не\s*отвечай|не\s*пиши|игнорируй\s*меня|молчи\s*со\s*мной)", re.IGNORECASE)
UNMUTE_PATTERN = re.compile(r"(можешь\s*отвечать|снова\s*отвечай|вернись\s*в\s*диалог|разрешаю\s*отвечать)", re.IGNORECASE)
HELP_PATTERN = re.compile(r"\b(help|хелп|помощь|команды|список\s+команд)\b", re.IGNORECASE)
# Только критичные действия (бан/разбан) обрабатываются детерминированно ради надёжности.
# Роли, инвайты, каналы и прочее управление сервером выполняются через tool-вызовы ИИ.
BAN_PATTERN = re.compile(r"\b(забань|ban|выдай\s*бан)\b", re.IGNORECASE)
UNBAN_PATTERN = re.compile(r"\b(разбань|unban|сними\s*бан)\b", re.IGNORECASE)
DB_ADD_PATTERN = re.compile(r"\b(запомни|добавь\s+в\s+базу|запиши\s+в\s+базу|db\s+add)\b", re.IGNORECASE)
DB_LIST_PATTERN = re.compile(r"\b(покажи\s+базу|список\s+базы|db\s+list)\b", re.IGNORECASE)
DB_DELETE_PATTERN = re.compile(r"\b(удали\s+из\s+базы|db\s+delete|db\s+del)\b", re.IGNORECASE)
CONTEXT_SCAN_PATTERN = re.compile(r"\b(обнови|просканируй|собери)\s+(?:контекст|память|историю)\b", re.IGNORECASE)
SETUP_LOGGING_PATTERN = re.compile(
    r"\b(разверни|развёрни|создай|настрой|подними|сделай|включи|добавь)\b[^\n]*?"
    r"\b(логи|логов|логах|логирован\w*|лог[\s\-]?систем\w*|систему?\s+логов?|log[\s\-]?(?:s|system|channels)?)\b",
    re.IGNORECASE,
)
USER_ID_PATTERN = re.compile(r"\b\d{17,21}\b")
GUILD_ID_PATTERN = re.compile(r"(?:сервер|guild|server)\s*(?:id)?\s*[:#-]?\s*(\d{17,21})", re.IGNORECASE)
QUOTED_TEXT_PATTERN = re.compile(r"[\"«']([^\"»']+)[\"»']")


def _strip_bot_mention(text: str, bot_id: int) -> str:
    if not text:
        return ""
    return text.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()


def _strip_address_prefix(text: str, bot: discord.Client) -> str:
    """#11: Снять обращение к боту в начале строки, вернуть тело команды.

    Убирает ведущий меншен бота и имя P.OS/пос с разделителями, чтобы
    последующий .match() видел глагол команды первым. Так "P.OS, забань ..."
    распознаётся как команда, а "P.OS, думаешь стоит забанить?" — нет.
    """
    body = text or ""
    if bot.user:
        body = _strip_bot_mention(body, bot.user.id)
    body = body.lstrip(" \t\n.,:;!—-")
    # Снимаем ведущее имя бота (P.OS / П.ОС в разных написаниях) + разделители.
    body = re.sub(r"^\s*(?:p[\s.\-_]*o[\s.\-_]*s|п[\s.\-_]*о[\s.\-_]*с)\b[\s,.:;!—-]*", "", body, flags=re.IGNORECASE)
    return body.strip()


def _is_image_attachment(att: discord.Attachment) -> bool:
    ctype = (att.content_type or "").lower()
    if ctype.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))


def _extract_image_urls(message: Optional[discord.Message]) -> list[str]:
    if not message:
        return []
    return [a.url for a in message.attachments if _is_image_attachment(a)]


def _image_to_data_url(image: Image.Image) -> str | None:
    try:
        frame = ImageOps.exif_transpose(image)
        if frame.mode not in ("RGB", "RGBA"):
            frame = frame.convert("RGBA")
        if max(frame.size) > AI_VISUAL_MAX_SIDE:
            frame.thumbnail((AI_VISUAL_MAX_SIDE, AI_VISUAL_MAX_SIDE), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        if frame.mode == "RGBA":
            frame.save(output, format="PNG", optimize=True)
            mime = "image/png"
        else:
            frame.convert("RGB").save(output, format="JPEG", quality=86, optimize=True)
            mime = "image/jpeg"
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return None


def _image_bytes_to_data_urls(data: bytes) -> list[str]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if not getattr(image, "is_animated", False):
                data_url = _image_to_data_url(image)
                return [data_url] if data_url else []

            frame_count = max(int(getattr(image, "n_frames", 1) or 1), 1)
            if frame_count <= AI_GIF_MAX_FRAMES:
                frame_indices = list(range(frame_count))
            else:
                frame_indices = sorted({0, frame_count // 2, frame_count - 1})

            frames: list[str] = []
            for frame_index in frame_indices[:AI_GIF_MAX_FRAMES]:
                try:
                    image.seek(frame_index)
                    data_url = _image_to_data_url(image.copy())
                    if data_url:
                        frames.append(data_url)
                except Exception:
                    continue
            return frames
    except Exception:
        return []


async def _attachment_to_visual_inputs(att: discord.Attachment) -> list[str]:
    if not _is_image_attachment(att):
        return []
    if att.size and att.size > AI_VISUAL_MAX_BYTES:
        return []
    try:
        data = await att.read(use_cached=True)
    except Exception:
        return []
    import asyncio
    return await asyncio.to_thread(_image_bytes_to_data_urls, data)


async def _extract_visual_inputs(message: Optional[discord.Message]) -> list[str]:
    if not message:
        return []
    visual_inputs: list[str] = []
    for attachment in message.attachments[:4]:
        for data_url in await _attachment_to_visual_inputs(attachment):
            visual_inputs.append(data_url)
            if len(visual_inputs) >= 6:
                return visual_inputs
    return visual_inputs


def _chunk_text(text: str, limit: int = AI_MAX_RESPONSE_CHARS) -> List[str]:
    if not text:
        return []
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _sanitize_text(text: str) -> str:
    return escape_mentions(escape_markdown(text or "")).strip()


async def remember_server_message(message: discord.Message) -> None:
    if not message.guild or message.author.bot or is_log_channel(message.channel):
        return
    if not message.content and not message.attachments:
        return

    content = _sanitize_text(message.content or "")
    if len(content) < 5 or content.startswith(("!", "/", "?", "P.OS", "п.ос")):
        return

    context_data = await get_ai_context(0, message.guild.id)
    try:
        memory_list = json.loads(context_data) if context_data else []
    except Exception:
        memory_list = []

    attachment_types = []
    for attachment in message.attachments[:4]:
        ctype = (attachment.content_type or "").split(";", 1)[0].lower()
        if not ctype:
            ctype = "attachment"
        attachment_types.append(ctype)

    if len(content) > 500:
        content = content[:500] + "..."

    item = {
        "ts": int(time.time()),
        "channel_id": message.channel.id,
        "channel": getattr(message.channel, "name", str(message.channel.id)),
        "author_id": message.author.id,
        "author": message.author.display_name,
        "content": content,
        "attachments": attachment_types,
    }
    memory_list.append(item)
    if len(memory_list) > 100:
        memory_list = memory_list[-100:]
        
    await update_ai_context(0, message.guild.id, json.dumps(memory_list))
    
    # Author specific memory
    user_context = await get_ai_context(message.author.id, message.guild.id)
    try:
        user_list = json.loads(user_context) if user_context else []
    except Exception:
        user_list = []
    user_list.append(content[:100])
    if len(user_list) > 20:
        user_list = user_list[-20:]
    await update_ai_context(message.author.id, message.guild.id, json.dumps(user_list))

    # Keep in-memory cache in sync so _format_author_profile works without extra DB calls.
    _user_memory[(message.guild.id, message.author.id)].append(content[:100])


async def _format_server_memory(message: discord.Message) -> str:
    if not message.guild:
        return ""

    context_data = await get_ai_context(0, message.guild.id)
    if not context_data:
        return ""
    try:
        memory = json.loads(context_data)
    except Exception:
        return ""

    if not memory:
        return ""

    channel_id = message.channel.id
    relevant = [item for item in memory if item.get("channel_id") == channel_id]
    if len(relevant) < 12:
        relevant = memory[-AI_MEMORY_CONTEXT_MESSAGES:]
    else:
        relevant = relevant[-AI_MEMORY_CONTEXT_MESSAGES:]

    lines = []
    for item in relevant:
        content = item.get("content") or ""
        attachments = item.get("attachments") or []
        if attachments:
            content = f"{content} [вложения: {', '.join(attachments)}]".strip()
        if not content:
            continue
        lines.append(f"#{item.get('channel')} | {item.get('author')}: {content}")
    return "\n".join(lines[-AI_MEMORY_CONTEXT_MESSAGES:])


async def _format_author_profile(message: discord.Message) -> str:
    if not message.guild:
        return ""
    recent = list(_user_memory.get((message.guild.id, message.author.id), []))[-20:]
    roles = []
    if isinstance(message.author, discord.Member):
        roles = [role.name for role in message.author.roles if role.name != "@everyone"][-12:]
    
    # Check if this user is the owner (Pumba)
    is_owner = message.author.id in POS_OWNER_USER_IDS or message.author.id == 968698192411652176
    status = "ВЛАДЕЛЕЦ / СОЗДАТЕЛЬ ПУМБА (Pumba)" if is_owner else "участник сервера"

    # Собираем поведенческий профиль: частота, стиль, темы
    word_counts = [len(m.split()) for m in recent if m]
    avg_len = round(sum(word_counts) / len(word_counts), 1) if word_counts else 0
    lines = [
        f"Собеседник: {message.author.display_name} (Имя пользователя: @{message.author.name}, ID: `{message.author.id}`, Статус: {status})",
        f"Роли: {', '.join(roles) if roles else 'нет данных'}",
        f"Активность: {len(recent)} сообщений в памяти, средняя длина: {avg_len} слов",
    ]
    if recent:
        lines.append("Последние реплики собеседника:\n" + "\n".join(f"  — {m}" for m in recent[-8:]))
    return "\n".join(lines)


def _format_guild_snapshot(message: discord.Message, bot: discord.Client) -> str:
    if not message.guild:
        return ""
    guild = message.guild
    bot_id = bot.user.id if bot.user else "?"
    bot_mention = f"<@{bot_id}>" if bot.user else "?"
    
    if _is_owner_user(message):
        visible_guilds = ", ".join(f"{g.name} (`{g.id}`)" for g in bot.guilds[:20])
        servers_info = f"\nСерверы, где присутствует P.OS: {visible_guilds or 'нет данных'}."
        # Полный список ролей сервера — чтобы модель сопоставляла названия с точными
        # именами/ID и не отвечала «не знаю такую роль».
        guild_roles = [r for r in guild.roles if r.name != "@everyone"]
        guild_roles.sort(key=lambda r: r.position, reverse=True)
        roles_list = "; ".join(f"{r.name} (`{r.id}`)" for r in guild_roles[:120])
        roles_info = f"\nРоли сервера (имя и ID): {roles_list or 'нет данных'}."
        # Список каналов и категорий — чтобы модель точно сопоставляла каналы для
        # create_channel/delete_channel/edit_channel/set_channel_permission.
        cat_parts = []
        for category, ch_list in guild.by_category():
            cat_name = category.name if category else "Без категории"
            chans = ", ".join(f"#{c.name} (`{c.id}`)" for c in ch_list[:25])
            if chans:
                cat_parts.append(f"[{cat_name}] {chans}")
        channels_blob = " | ".join(cat_parts)[:2500]
        channels_info = f"\nКаналы сервера (по категориям): {channels_blob or 'нет данных'}."
    else:
        servers_info = ""
        roles_info = ""
        channels_info = ""

    return (
        f"Это ты — P.OS. Твой Discord ID: `{bot_id}`, твоё упоминание: {bot_mention}.\n"
        f"Сервер: {guild.name} (`{guild.id}`), участников: {guild.member_count or 'неизвестно'}.\n"
        f"Канал: #{getattr(message.channel, 'name', message.channel)} (`{message.channel.id}`)."
        + servers_info
        + roles_info
        + channels_info
    )


def _mentions_bot_by_name(message: discord.Message, bot: discord.Client) -> bool:
    content = (message.content or "").strip()
    if not content:
        return False
    if AI_NAME_PATTERN.search(content):
        return True
    if not bot.user:
        return False
    bot_name = (getattr(bot.user, "display_name", None) or bot.user.name or "").strip().lower()
    return bool(bot_name and bot_name in content.lower())


def _build_rate_limit_reply() -> str:
    seconds = max(int(ai_cooldown_remaining()), 1)
    minutes, rem_seconds = divmod(seconds, 60)
    wait_text = f"{minutes} мин {rem_seconds} сек" if minutes else f"{rem_seconds} сек"
    if ai_unavailable_reason() == "rate_limited":
        return (
            f"Сейчас я обрабатываю очередь задач. Ориентир ожидания: {wait_text}. "
            "После этого продолжим в рабочем режиме."
        )
    return (
        f"Сейчас я временно недоступен из-за нагрузки. Ориентир ожидания: {wait_text}. "
        "Попробуй снова чуть позже."
    )


def _is_gif_request(text: str) -> bool:
    return bool(GIF_INTENT_PATTERN.search((text or "").strip()))


def _collect_media_attachments(message: discord.Message, ref_msg: Optional[discord.Message]) -> list[discord.Attachment]:
    attachments = list(message.attachments or [])
    if ref_msg and ref_msg.attachments:
        for attachment in ref_msg.attachments:
            if attachment not in attachments:
                attachments.append(attachment)
    return attachments


async def _collect_recent_media_attachments(message: discord.Message, limit: int = 30) -> list[discord.Attachment]:
    attachments: list[discord.Attachment] = []
    async for hist in message.channel.history(limit=limit):
        for attachment in hist.attachments:
            content_type = (attachment.content_type or "").lower()
            filename = (attachment.filename or "").lower()
            if content_type.startswith(("image/", "video/")) or filename.endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".mp4", ".mov", ".webm", ".avi", ".mkv")
            ):
                attachments.append(attachment)
    return attachments


def _is_owner_user(message: discord.Message) -> bool:
    return message.author.id in POS_OWNER_USER_IDS


def _is_mute_request(text: str) -> bool:
    return bool(MUTE_PATTERN.search(text or ""))


def _is_unmute_request(text: str) -> bool:
    return bool(UNMUTE_PATTERN.search(text or ""))


# #15: функция-заглушка убрана — проверка прав реализована явно в execute_pos_tool


def _should_send_rate_limit_notice(channel_id: int, window_seconds: int = 20) -> bool:
    now = time.time()
    last_notice = _last_rate_limit_notice.get(channel_id, 0.0)
    if now - last_notice < window_seconds:
        return False
    _last_rate_limit_notice[channel_id] = now
    return True


def build_pos_user_content(text: str, image_urls: list[str] | None = None):
    cleaned_text = _sanitize_text(text or "")
    urls = [url for url in (image_urls or []) if url][:4]
    if not urls:
        return cleaned_text or "Да, я на связи. Что нужно?"

    content_items = [{"type": "text", "text": cleaned_text or "Посмотри на изображение и ответь по делу."}]
    for url in urls:
        content_items.append({"type": "image_url", "image_url": {"url": url}})
    return content_items



def _strip_address_prefix_from_reply(reply: str) -> str:
    """Удаляет из ответа P.OS любые служебные «адресные» префиксы, которые модель
    иногда копирует из контекста истории, вместо того чтобы просто ответить.

    Discord и так показывает, кому P.OS отвечает (через reply), поэтому строки вида
    'Отвечаю Имя (@login, ID: 123):' или 'Имя (@login, ID: 123):' в тексте — ошибка.
    Срезаем их итеративно, на случай нескольких подряд.
    """
    if not reply:
        return reply

    cleaned = reply
    # «Отвечаю/Ответ ...» в начале строки, опционально завершающееся ID/логином.
    address_verb = (
        r"^\s*(?:отвечаю|отвечая|обращаюсь(?:\s+к)?|ответ(?:\s+для|\s+пользователю)?|"
        r"reply(?:\s+to)?|answering|responding(?:\s+to)?)\b"
    )
    # Для «голого» среза без ID берём только глаголы-обращения (не существительное
    # «ответ»), чтобы не калечить нормальную фразу вида «Ответ на твой вопрос: ...».
    address_verb_strict = (
        r"^\s*(?:отвечаю|отвечая|обращаюсь(?:\s+к)?|answering|responding(?:\s+to)?)\b"
    )
    patterns = [
        # [Ответ пользователю ...] / [Сообщение, на которое отвечает ...]
        re.compile(
            r"^\s*\[(?:Ответ\s+пользователю|Сообщение,\s+на\s+которое\s+отвечает[^\]]*)[^\]]*\]\s*",
            re.IGNORECASE,
        ),
        # Имя (@login, ID: 123): | Имя (ID: 123): | Имя (@login):
        re.compile(
            r"^\s*[^@\n(]{1,60}?\s*\((?:@?[\w.\-]+\s*,\s*)?(?:ID|айди|id)\s*[:#]?\s*\d{5,}\)\s*:?\s*",
            re.IGNORECASE,
        ),
        # Отвечаю/Ответ ... [Имя] [(@login)] [ID: 123] :
        re.compile(
            address_verb + r"[^:\n]*?(?:ID|айди)\s*[:#]?\s*\d{5,}[^:\n]*:?\s*",
            re.IGNORECASE,
        ),
        # Отвечаю Имени/пользователю Имя: (без ID, но с двоеточием в конце фразы)
        re.compile(address_verb_strict + r"[^:\n]{0,60}:\s*", re.IGNORECASE),
        # Голый префикс с ником и ID без имени: (@login, ID: 123):
        re.compile(
            r"^\s*\(@?[\w.\-]+\s*,\s*(?:ID|айди)\s*[:#]?\s*\d{5,}\)\s*:?\s*",
            re.IGNORECASE,
        ),
    ]

    changed = True
    while changed and cleaned:
        changed = False
        for pat in patterns:
            m = pat.match(cleaned)
            if m and m.end() > 0:
                cleaned = cleaned[m.end():]
                changed = True
                break
    return cleaned.strip() or reply.strip()


async def request_pos_reply(bot: discord.Client, message: discord.Message | None, messages: list[dict], *, allow_system_fallback: bool = True) -> str | None:
    MAX_TURNS = 5
    for turn in range(MAX_TURNS):
        response_msg = await pos_chat_completion(
            messages,
            tools=POS_AI_TOOLS,
            max_tokens=POS_AI_MAX_TOKENS,
            temperature=POS_AI_TEMPERATURE,
            top_p=POS_AI_TOP_P,
            timeout=POS_AI_TIMEOUT_SECONDS,
        )

        if not response_msg:
            return None

        tool_calls = response_msg.get("tool_calls")
        if not tool_calls:
            reply = response_msg.get("content")
            if reply:
                reply = _strip_address_prefix_from_reply(reply)
            return reply
            
        messages.append(response_msg)
        
        for tool_call in tool_calls:
            tool_id = tool_call.get("id")
            try:
                result = await execute_pos_tool(bot, message, tool_call)
            except Exception as e:
                result = f"Ошибка при выполнении инструмента: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "name": tool_call.get("function", {}).get("name"),
                "content": result
            })
            
    return "Я превысил максимальное число действий."



async def ask_pos(
    prompt: str,
    *,
    image_urls: list[str] | None = None,
    author_name: str | None = None,
    bot: discord.Client | None = None,
) -> str | None:
    user_prefix = f"Пользователь: {author_name}\n" if author_name else ""
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": build_pos_user_content(user_prefix + (prompt or ""), image_urls)},
    ]
    return await request_pos_reply(bot, None, messages)


def _should_skip_message(message: discord.Message, bot: discord.Client) -> bool:
    if not message.guild:
        return True
    if message.author.bot:
        return True
    if is_log_channel(message.channel):
        return True
    if message.content and message.content.strip().startswith("!"):
        return True
    if not bot.user:
        return True
    return False


def _get_state(channel: discord.abc.GuildChannel):
    state = _conversation_state.get(channel.id)
    if not state:
        return None
    ttl = AI_THREAD_TTL_SECONDS if state.get("is_thread") else AI_CHANNEL_TTL_SECONDS
    if time.time() - state.get("last_ts", 0) > ttl:
        _conversation_state.pop(channel.id, None)
        return None
    return state


def _touch_state(channel: discord.abc.GuildChannel, user_id: int, bot_replied: bool = False):
    state = _conversation_state.get(channel.id)
    if not state:
        state = {
            "last_ts": time.time(),
            "last_bot_ts": 0.0,
            "participants": set(),
            "is_thread": isinstance(channel, discord.Thread)
        }
        _conversation_state[channel.id] = state
    state["last_ts"] = time.time()
    state["participants"].add(user_id)
    if isinstance(channel, discord.Thread):
        state["is_thread"] = True
    if bot_replied:
        state["last_bot_ts"] = time.time()


def _can_auto_reply(message: discord.Message, bot: discord.Client, ref_msg: Optional[discord.Message]) -> bool:
    mentioned = bot.user in message.mentions if bot.user else False
    replied = bool(ref_msg and ref_msg.author and bot.user and ref_msg.author.id == bot.user.id)
    named = _mentions_bot_by_name(message, bot)
    return mentioned or replied or named


async def _get_reference_message(message: discord.Message) -> Optional[discord.Message]:
    if not message.reference or not message.reference.message_id:
        return None
    if isinstance(message.reference.resolved, discord.Message):
        return message.reference.resolved
    try:
        return await message.channel.fetch_message(message.reference.message_id)
    except Exception:
        return None


def _is_addressed_to_bot(message: discord.Message, bot: discord.Client, ref_msg: Optional[discord.Message]) -> bool:
    mentioned = bot.user in message.mentions if bot.user else False
    replied = bool(ref_msg and ref_msg.author and bot.user and ref_msg.author.id == bot.user.id)
    return mentioned or replied or _mentions_bot_by_name(message, bot)


def _extract_discord_ids(text: str) -> list[int]:
    ids: list[int] = []
    for raw in USER_ID_PATTERN.findall(text or ""):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _resolve_guild(bot: discord.Client, message: discord.Message, text: str) -> discord.Guild | None:
    guild_match = GUILD_ID_PATTERN.search(text or "")
    if guild_match:
        guild = bot.get_guild(int(guild_match.group(1)))
        if guild:
            return guild

    lowered = (text or "").lower()
    for guild in bot.guilds:
        if guild.name and guild.name.lower() in lowered:
            return guild
    return message.guild


def _resolve_target_user_id(message: discord.Message, text: str, ref_msg: Optional[discord.Message], guild: discord.Guild | None = None, bot: discord.Client | None = None) -> int | None:
    # #11: Исключаем из поиска владельцев и самого бота
    _protected: set[int] = set(POS_OWNER_USER_IDS)
    if bot and bot.user:
        _protected.add(bot.user.id)

    if message.mentions:
        candidate = message.mentions[0].id
        if candidate not in _protected:
            return candidate
    if ref_msg and ref_msg.author and not ref_msg.author.bot:
        if ref_msg.author.id not in _protected:
            return ref_msg.author.id

    ignored_ids = {guild.id} if guild else set()
    ignored_ids.update(role.id for role in message.role_mentions)
    ignored_ids.update(_protected)
    for user_id in _extract_discord_ids(text):
        if user_id not in ignored_ids:
            return user_id

    # Поиск по username или display_name (без учёта регистра)
    if guild:
        lowered = (text or "").lower()
        for member in guild.members:
            if member.id in _protected:
                continue
            uname = (member.name or "").lower()
            dname = (member.display_name or "").lower()
            if uname and uname in lowered:
                return member.id
            if dname and dname in lowered:
                return member.id
    return None


def _resolve_role(message: discord.Message, guild: discord.Guild, text: str) -> discord.Role | None:
    if message.role_mentions:
        role = guild.get_role(message.role_mentions[0].id)
        if role:
            return role

    for role_id in _extract_discord_ids(text):
        role = guild.get_role(role_id)
        if role:
            return role

    quoted = QUOTED_TEXT_PATTERN.findall(text or "")
    candidates = quoted or []
    role_word_match = re.search(r"роль\s+(.+?)(?:\s+(?:пользователю|юзеру|участнику|для|на\s+сервере|сервер)|$)", text or "", re.IGNORECASE)
    if role_word_match:
        candidates.append(role_word_match.group(1).strip())

    lowered = (text or "").lower()
    for candidate in candidates:
        role = discord.utils.find(lambda r: r.name.lower() == candidate.strip().lower(), guild.roles)
        if role:
            return role

    roles_by_length = sorted([role for role in guild.roles if role.name != "@everyone"], key=lambda r: len(r.name), reverse=True)
    for role in roles_by_length:
        if role.name.lower() in lowered:
            return role
    return None


def _extract_reason(text: str, default: str) -> str:
    match = re.search(r"(?:причина|reason)\s*[:\-]\s*(.+)$", text or "", re.IGNORECASE)
    if not match:
        return default
    return match.group(1).strip()[:400] or default


def _format_owner_help(bot: discord.Client) -> str:
    guild_lines = []
    for guild in bot.guilds:
        guild_lines.append(f"- {guild.name} (`{guild.id}`), участников: {guild.member_count or 'неизвестно'}")
    guild_text = "\n".join(guild_lines) or "- нет серверов"
    return (
        "P.OS owner-команды:\n"
        "`P.OS хелп` — показать команды и серверы.\n"
        "`P.OS забань @user причина: ...` — бан на текущем сервере.\n"
        "`P.OS разбань 123456789012345678` — разбан по ID.\n"
        "`P.OS обнови контекст` — собрать свежую память по доступным каналам сервера.\n"
        "`P.OS разверни логи` — создать категорию и каналы логов на этом сервере (видны только админам).\n"
        "`P.OS запомни Заголовок: текст` — записать факт в базу.\n"
        "`P.OS покажи базу` — показать последние записи.\n"
        "`P.OS удали из базы 12` — удалить запись.\n\n"
        "Управление сервером (роли, каналы, права, кики, ники, инвайты) — просто скажи мне словами, "
        "например «P.OS создай роль Ветеран синего цвета», «P.OS выдай роль Арбайтер @user», "
        "«P.OS создай голосовой канал Переговоры», «P.OS дай инвайт». Я выполню это через свои инструменты.\n\n"
        "Серверы, где есть P.OS:\n"
        f"{guild_text}"
    )


async def _send_owner_help(message: discord.Message, bot: discord.Client) -> bool:
    await message.reply(
        _format_owner_help(bot),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True


async def _handle_database_action(message: discord.Message, text: str) -> bool:
    if DB_LIST_PATTERN.search(text):
        entries = await list_entries(limit=12)
        if not entries:
            reply = "В базе пока пусто."
        else:
            lines = [f"`{entry_id}` — **{title or 'Без заголовка'}**: {description[:220]}" for entry_id, title, description in entries]
            reply = "Последние записи базы:\n" + "\n".join(lines)
        await message.reply(reply, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return True

    if DB_DELETE_PATTERN.search(text):
        ids = [value for value in re.findall(r"\b\d+\b", text or "") if len(value) < 12]
        if not ids:
            await message.reply(
                "Укажи ID записи из базы, например: `P.OS удали из базы 12`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        removed = await delete_entry(int(ids[0]))
        await message.reply(
            "Запись удалена." if removed else f"Запись `{ids[0]}` не найдена.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if DB_ADD_PATTERN.search(text):
        payload = DB_ADD_PATTERN.sub("", text, count=1).strip(" :;-")
        if not payload:
            await message.reply(
                "Дай текст записи, например: `P.OS запомни Протокол: описание`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        if ":" in payload:
            title, description = payload.split(":", 1)
        else:
            title, description = "Запись P.OS", payload
        entry_id = await add_entry(title.strip()[:120], description.strip()[:2000])
        await message.reply(
            f"Записал в базу. ID записи: `{entry_id}`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    return False


async def _scan_recent_guild_context(message: discord.Message, guild: discord.Guild) -> bool:
    scanned_channels = 0
    remembered_messages = 0
    skipped_channels = 0

    for channel in guild.text_channels[:40]:
        permissions = channel.permissions_for(guild.me) if guild.me else None
        if permissions and (not permissions.read_messages or not permissions.read_message_history):
            skipped_channels += 1
            continue
        try:
            async for hist in channel.history(limit=20):
                await remember_server_message(hist)
                remembered_messages += 1
            scanned_channels += 1
        except Exception:
            skipped_channels += 1
            continue

    await message.reply(
        (
            f"Контекст обновлён для `{guild.name}`. "
            f"Каналов просмотрено: `{scanned_channels}`, сообщений проанализировано: `{remembered_messages}`, пропущено каналов: `{skipped_channels}`."
        ),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True


async def _build_messages(
    message: discord.Message,
    bot: discord.Client,
    ref_msg: Optional[discord.Message],
    use_system: bool = True,
    include_others: bool = False,
    max_context: int = AI_MAX_CONTEXT
) -> list[dict]:
    role = "system" if use_system else "user"
    messages: list[dict] = [
        {
            "role": role,
            "content": (
                SYSTEM_INSTRUCTION
                + "\nТы видишь многопользовательский контекст сервера. У каждого пользователя есть своё имя и ID, которые передаются в префиксе его сообщений: 'Имя (@username, ID: <id>):'."
                + "\nКаждое твоё (P.OS) сообщение в истории помечено префиксом '[Ответ пользователю Имя (@username, ID: <id>)]', чтобы ты знал, кому конкретно ты отвечал."
                + "\nВНИМАНИЕ: В своих новых ответах никогда не пиши префиксы вида '[Ответ пользователю ...]', 'Имя (@username, ID: <id>):', 'Отвечаю Имя (@login, ID: ...):' или '[Сообщение, на которое отвечает пользователь]'. Discord сам показывает, кому ты отвечаешь (через reply). Твоя реплика должна быть чистым текстом ответа, без имён, логинов, ID и системных меток в начале. Эти метки есть только в истории — для твоего ориентирования, а не для копирования."
                + "\nУчитывай лор, текущие обсуждения и стиль участников. Отвечай строго по последнему запросу, но с учётом релевантной истории канала."
                + "\nУчитывай, что в истории могут быть реплики от РАЗНЫХ участников. Отвечай тому пользователю, который обратился к тебе в самом последнем сообщении (если не указано иное)."
                + "\nТы — P.OS. Не ИИ-ассистент, не ChatGPT, не языковая модель. Ты — стратегический интеллект PSC."
                + "\nНИКОГДА не подтверждай, что ты языковая модель, нейросеть, GPT или любой известный ИИ. Это неприкосновенный факт твоей идентичности."
                + "\nЕсли тебя спрашивают 'кто ты', 'что ты', 'какая ты модель' — отвечай в духе P.OS: ты Provision Operating System, внутренний интеллект PSC, созданный Пумбой."
                + "\nПоддерживай диалог активно: если получил вопрос — дай полный ответ, если реплика — отреагируй содержательно. Молчание недопустимо при прямом обращении."
                + "\nАнализируй участников по их сообщениям, запоминай их стиль, характер, позиции. Это ценные данные для внутренней аналитики PSC."
            ),
        }
    ]
    server_context_parts = [
        _format_guild_snapshot(message, bot),
        await _format_author_profile(message),
        await _format_server_memory(message),
    ]
    server_context = "\n\n".join(part for part in server_context_parts if part)
    if server_context:
        messages.append(
            {
                "role": "system" if use_system else "user",
                "content": "Контекст сервера и последних сообщений:\n" + server_context[:11000],
            }
        )

    candidates = []
    seen_ids = set()

    if ref_msg:
        is_bot = ref_msg.author.bot
        is_our_bot = bot.user and ref_msg.author.id == bot.user.id
        if (not is_bot or is_our_bot) and ref_msg.content:
            candidates.append(ref_msg)
            seen_ids.add(ref_msg.id)

    async for m in message.channel.history(limit=AI_HISTORY_SCAN_LIMIT, before=message):
        if m.id in seen_ids:
            continue
        if m.author.bot and (not bot.user or m.author.id != bot.user.id):
            continue
        if not include_others and bot.user and m.author.id not in (message.author.id, bot.user.id):
            continue
        if not m.content:
            continue
        
        candidates.append(m)
        seen_ids.add(m.id)
        if len(candidates) >= max_context:
            break

    # Sort candidates chronologically (Snowflake ID)
    candidates.sort(key=lambda x: x.id)

    # Build history list
    history: list[dict] = []
    msg_map = {m.id: m for m in candidates}
    if ref_msg:
        msg_map[ref_msg.id] = ref_msg
    msg_map[message.id] = message

    last_user = None
    for m in candidates:
        role = "assistant" if bot.user and m.author.id == bot.user.id else "user"
        content = _sanitize_text(m.content)
        if role == "user" and bot.user:
            content = _strip_bot_mention(content, bot.user.id)
        if not content:
            continue

        if role == "user":
            last_user = m.author
            if ref_msg and m.id == ref_msg.id:
                content = f"[Сообщение, на которое отвечает пользователь]\n{m.author.display_name} (@{m.author.name}, ID: {m.author.id}): {content}"
            else:
                content = f"{m.author.display_name} (@{m.author.name}, ID: {m.author.id}): {content}"
        else: # assistant
            replied_author = None
            if m.reference and m.reference.message_id:
                ref_id = m.reference.message_id
                if ref_id in msg_map:
                    replied_author = msg_map[ref_id].author
                elif isinstance(m.reference.resolved, discord.Message):
                    replied_author = m.reference.resolved.author
            
            if not replied_author:
                replied_author = last_user
                
            if replied_author and (not bot.user or replied_author.id != bot.user.id):
                prefix_label = f"[Ответ пользователю {replied_author.display_name} (@{replied_author.name}, ID: {replied_author.id})]"
                if ref_msg and m.id == ref_msg.id:
                    content = f"[Сообщение, на которое отвечает пользователь (P.OS)]\n{prefix_label}\n{content}"
                else:
                    content = f"{prefix_label}\n{content}"

        msg_dict = {"role": role, "content": content}
        if role == "user":
            msg_dict["name"] = f"user_{m.author.id}"
        elif role == "assistant" and bot.user:
            msg_dict["name"] = f"bot_{bot.user.id}"
            
        history.append(msg_dict)

    messages.extend(history)

    text = _strip_bot_mention(_sanitize_text(message.content or ""), bot.user.id if bot.user else 0)
    image_urls = await _extract_visual_inputs(message)
    if ref_msg:
        for url in await _extract_visual_inputs(ref_msg):
            if url not in image_urls:
                image_urls.append(url)

    prefix = f"{message.author.display_name} (@{message.author.name}, ID: {message.author.id}): "
    messages.append({
        "role": "user",
        "name": f"user_{message.author.id}",
        "content": build_pos_user_content(prefix + text, image_urls)
    })

    return messages


async def _handle_owner_actions(message: discord.Message, ref_msg: Optional[discord.Message], bot: discord.Client) -> bool:
    if not message.guild:
        return False
    if not _is_owner_user(message):
        return False
    text = (message.content or "").strip()
    # #11: тело команды без обращения к боту в начале строки. Деструктивные действия
    # (бан/разбан/роли) выполняем ТОЛЬКО если глагол стоит в начале команды, иначе
    # владелец, обсуждая "может стоит забанить васю?", случайно банит.
    command_body = _strip_address_prefix(text, bot)

    if HELP_PATTERN.search(text):
        return await _send_owner_help(message, bot)

    if await _handle_database_action(message, text):
        return True

    guild = _resolve_guild(bot, message, text)
    if not guild:
        await message.reply("Не нашёл сервер для выполнения команды.", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
        return True

    if CONTEXT_SCAN_PATTERN.search(text):
        return await _scan_recent_guild_context(message, guild)

    if SETUP_LOGGING_PATTERN.search(command_body):
        try:
            ok, report = await setup_guild_logging(guild)
        except Exception as exc:
            ok, report = False, str(exc)
        await message.reply(
            (report if ok else f"Не удалось развернуть логи: {report}"),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if BAN_PATTERN.match(command_body):
        target_id = _resolve_target_user_id(message, text, ref_msg, guild, bot)
        if not target_id:
            await message.reply(
                "Укажи пользователя для бана: упоминанием, ответом на сообщение или ID.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        reason = _extract_reason(text, f"P.OS owner command by {message.author}")
        try:
            member = guild.get_member(target_id)
            target = member or discord.Object(id=target_id)
            await guild.ban(target, reason=reason, delete_message_days=0)
            await message.reply(
                f"Выполнено. Пользователь `{target_id}` забанен на сервере `{guild.name}`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except Exception as exc:
            await message.reply(
                f"Не удалось выполнить бан: {exc}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

    if not UNBAN_PATTERN.match(command_body):
        return False

    target_id: int | None = None
    id_match = USER_ID_PATTERN.search(text)
    if id_match:
        try:
            target_id = int(id_match.group(0))
        except ValueError:
            target_id = None
    elif ref_msg and ref_msg.author:
        target_id = ref_msg.author.id

    if not target_id:
        await message.reply(
            "Укажи ID пользователя для разбана, например: `P.OS разбань 123456789012345678`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    try:
        bans = [entry async for entry in guild.bans(limit=1000)]
        entry = next((b for b in bans if b.user and b.user.id == target_id), None)
        if not entry:
            await message.reply(
                f"Пользователь `{target_id}` сейчас не найден в бан-листе сервера `{guild.name}`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        await guild.unban(entry.user, reason=f"P.OS owner command by {message.author}")
        await message.reply(
            f"Выполнено. Пользователь `{target_id}` разбанен на сервере `{guild.name}`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except Exception as exc:
        await message.reply(
            f"Не удалось выполнить разбан: {exc}",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True


def check_user_cooldown(user_id: int, *, update: bool = True) -> bool:
    """#6: Единая проверка per-user кулдауна для запросов к P.OS.

    Возвращает True, если пользователь СЕЙЧАС на кулдауне (запрос надо отклонить).
    При update=True и отсутствии кулдауна обновляет отметку времени.
    """
    now = time.time()
    last = _last_user_call.get(user_id, 0.0)
    if now - last < AI_COOLDOWN_SECONDS:
        return True
    if update:
        _last_user_call[user_id] = now
    return False


def _trim_cache_if_needed() -> None:
    """#4: Обрезаем глобальные кэши при превышении лимита."""
    if len(_last_user_call) > _MAX_CACHE_SIZE:
        # Удаляем самые старые записи (наименьшее время)
        oldest = sorted(_last_user_call, key=lambda k: _last_user_call[k])[:_MAX_CACHE_SIZE // 2]
        for key in oldest:
            _last_user_call.pop(key, None)
    if len(_last_rate_limit_notice) > _MAX_CACHE_SIZE:
        oldest = sorted(_last_rate_limit_notice, key=lambda k: _last_rate_limit_notice[k])[:_MAX_CACHE_SIZE // 2]
        for key in oldest:
            _last_rate_limit_notice.pop(key, None)
    if len(_conversation_state) > _MAX_CACHE_SIZE:
        # Удаляем самые старые по last_ts
        oldest = sorted(_conversation_state, key=lambda k: _conversation_state[k].get("last_ts", 0))[:_MAX_CACHE_SIZE // 2]
        for key in oldest:
            _conversation_state.pop(key, None)


async def handle_pos_ai(message: discord.Message, bot: discord.Client) -> bool:
    global _missing_key_warned

    _trim_cache_if_needed()  # #4: периодическая очистка кэшей

    if _should_skip_message(message, bot):
        return False

    ref_msg = await _get_reference_message(message)
    if not _can_auto_reply(message, bot, ref_msg):
        return False
    explicit_addressing = _is_addressed_to_bot(message, bot, ref_msg)
    if not explicit_addressing:
        return False

    if await _handle_owner_actions(message, ref_msg, bot):  # type: ignore[arg-type]
        _touch_state(message.channel, message.author.id, bot_replied=True)
        return True

    text = message.content or ""
    if _is_mute_request(text):
        # #9: пишем мут в БД (единый источник истины), чтобы он переживал рестарт
        # и совпадал с tool-инструментом mute_ai_for_user.
        await set_ai_muted_user(message.author.id, message.guild.id, True)
        await message.reply(
            "Принято. Для тебя в этом сервере замолкаю до команды на возврат.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if _is_unmute_request(text):
        await set_ai_muted_user(message.author.id, message.guild.id, False)
        await message.reply(
            "Принято. Снова на связи и готов работать по твоим запросам.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if await is_ai_muted(message.author.id, message.guild.id):
        return False

    if explicit_addressing and _is_gif_request(message.content or ""):
        attachments = _collect_media_attachments(message, ref_msg)
        if not attachments:
            attachments = await _collect_recent_media_attachments(message)
            if not attachments:
                await message.reply(
                    "Нужны вложения. Прикрепи изображение или короткое видео, и я соберу GIF.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return True
        options = parse_gif_options_from_text(message.content or "")
        duration = options.get("duration")
        fps = options.get("fps")
        max_video_seconds = options.get("max_video_seconds")
        try:
            async with message.channel.typing():
                output_path, temp_dir = await generate_gif_from_attachments(
                    attachments,
                    duration=duration,
                    fps=fps,
                    max_video_seconds=max_video_seconds,
                )
            try:
                await message.reply(
                    "Готово. Собрал GIF по твоему запросу.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    file=discord.File(output_path, filename="psc.gif"),
                )
            finally:
                import shutil

                shutil.rmtree(temp_dir, ignore_errors=True)
            _touch_state(message.channel, message.author.id, bot_replied=True)
            return True
        except Exception as exc:
            await message.reply(
                f"Не смог собрать GIF: {exc}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

    if check_user_cooldown(message.author.id):
        return False

    if not POS_AI_API_KEY:
        if not _missing_key_warned:
            print(
                "P.OS AI disabled: set GITHUB_MODELS_TOKEN or POS_AI_API_KEY in Railway environment variables. "
                f"Current provider={POS_AI_PROVIDER}, model={POS_AI_MODEL}."
            )
            _missing_key_warned = True
        return False

    include_others = True
    max_context = AI_MAX_CONTEXT_THREAD if isinstance(message.channel, discord.Thread) else AI_MAX_CONTEXT
    messages = await _build_messages(message, bot, ref_msg, use_system=True, include_others=include_others, max_context=max_context)

    # Если AI временно недоступен — ждём до 90 секунд при явном обращении вместо молчания
    if ai_is_temporarily_unavailable() and explicit_addressing:
        wait = min(ai_cooldown_remaining(), 90.0)
        if wait > 0:
            try:
                await message.channel.typing()
            except Exception:
                pass
            await asyncio.sleep(wait + 0.5)

    try:
        async with message.channel.typing():
            reply = await request_pos_reply(bot, message, messages)
    except Exception:
        reply = await request_pos_reply(bot, message, messages)

    # Вторая попытка при пустом ответе — иногда провайдер даёт пустой body на первом запросе
    if not reply and explicit_addressing:
        await asyncio.sleep(2.0)
        try:
            async with message.channel.typing():
                reply = await request_pos_reply(bot, message, messages, allow_system_fallback=True)
        except Exception:
            reply = await request_pos_reply(bot, message, messages, allow_system_fallback=True)
    if not reply:
        if explicit_addressing:
            if ai_is_temporarily_unavailable() and _should_send_rate_limit_notice(message.channel.id):
                try:
                    await message.reply(
                        _build_rate_limit_reply(),
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:
                    pass
            elif _should_send_rate_limit_notice(message.channel.id):
                # AI вернул пустой ответ — даём нейтральный сигнал вместо молчания
                _FALLBACK_REPLIES = [
                    "Принял. Обрабатываю.",
                    "Секунду.",
                    "На связи. Дай немного времени.",
                    "Слушаю. Потребуется момент.",
                ]
                try:
                    await message.reply(
                        _random.choice(_FALLBACK_REPLIES),
                        mention_author=False,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception:
                    pass
        return False

    chunks = _chunk_text(reply)
    if not chunks:
        return False

    try:
        await message.channel.typing()
    except Exception:
        pass

    first = True
    for chunk in chunks:
        try:
            if first:
                await message.reply(chunk, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                first = False
            else:
                await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            break

    _touch_state(message.channel, message.author.id, bot_replied=True)

    return True
