from __future__ import annotations

import discord
from discord import Embed, Color
from typing import Dict

from config import LOG_CATEGORY_ID, LOG_CATEGORY_NAME, PRIMARY_LOG_CHANNEL_ID

LOG_CHANNEL_CONFIGS = [
    {
        "key": "moderation",
        "names": ["логи-модерации", "mod-logs", "moderation-logs"],
        "topic": "Логи модерации и автомодерации"
    },
    {
        "key": "messages",
        "names": ["логи-сообщений", "message-logs", "messages"],
        "topic": "Логи всех сообщений"
    },
    {
        "key": "message_edits",
        "names": ["логи-правок", "message-edits", "edits"],
        "topic": "Логи редактирования сообщений"
    },
    {
        "key": "message_deletes",
        "names": ["логи-удалений", "message-deletes", "deletes"],
        "topic": "Логи удалённых сообщений"
    },
    {
        "key": "members",
        "names": ["логи-участников", "member-logs", "members"],
        "topic": "Вход/выход, никнеймы, роли"
    },
    {
        "key": "voice",
        "names": ["логи-голоса", "voice-logs", "voice"],
        "topic": "События голосовых каналов"
    },
    {
        "key": "roles",
        "names": ["логи-ролей", "role-logs", "roles"],
        "topic": "Создание/изменение/удаление ролей"
    },
    {
        "key": "channels",
        "names": ["логи-каналов", "channel-logs", "channels"],
        "topic": "Создание/изменение/удаление каналов"
    },
    {
        "key": "server",
        "names": ["логи-сервера", "server-logs", "server"],
        "topic": "Прочие серверные события"
    },
    {
        "key": "commands",
        "names": ["логи-команд", "command-logs", "commands"],
        "topic": "Использование команд"
    },
    {
        "key": "forms",
        "names": ["логи-форм", "form-logs", "forms"],
        "topic": "Жалобы/формы/заявки"
    },
    {
        "key": "errors",
        "names": ["логи-ошибок", "error-logs", "errors"],
        "topic": "Ошибки и исключения"
    }
]

_LOG_CHANNEL_CACHE: Dict[int, Dict[str, int]] = {}
_LOG_INIT_DONE: set[int] = set()
LOG_TYPE_LABELS = {
    "moderation": "Модерация",
    "messages": "Сообщения",
    "message_edits": "Правки",
    "message_deletes": "Удаления",
    "members": "Участники",
    "voice": "Голос",
    "roles": "Роли",
    "channels": "Каналы",
    "server": "Сервер",
    "commands": "Команды",
    "forms": "Формы",
    "errors": "Ошибки",
}


def _safe_lower(value: str | None) -> str:
    return (value or "").lower()


def is_log_category(category: discord.CategoryChannel | None) -> bool:
    if not category:
        return False
    if LOG_CATEGORY_ID and category.id == LOG_CATEGORY_ID:
        return True
    name_l = _safe_lower(category.name)
    target = _safe_lower(LOG_CATEGORY_NAME)
    if target and target in name_l:
        return True
    return "лог" in name_l or "log" in name_l


def _find_category(guild: discord.Guild | None) -> discord.CategoryChannel | None:
    if not guild:
        return None
    if LOG_CATEGORY_ID:
        cat = guild.get_channel(LOG_CATEGORY_ID)
        if isinstance(cat, discord.CategoryChannel):
            return cat
    for cat in guild.categories:
        if is_log_category(cat):
            return cat
    return None


def _find_channel_by_names(
    guild: discord.Guild,
    names: list[str],
    category: discord.CategoryChannel | None = None
) -> discord.TextChannel | None:
    names_l = {n.lower() for n in names}

    if category:
        cat_channels = [ch for ch in category.channels if isinstance(ch, discord.TextChannel)]
        for ch in cat_channels:
            if ch.name.lower() in names_l:
                return ch
        for ch in cat_channels:
            cname = ch.name.lower()
            for name in names_l:
                if name and name in cname:
                    return ch

    for ch in guild.text_channels:
        if ch.name.lower() in names_l:
            return ch
    for ch in guild.text_channels:
        cname = ch.name.lower()
        for name in names_l:
            if name and name in cname:
                return ch
    return None


async def ensure_log_category_and_channels(guild: discord.Guild) -> dict[str, discord.TextChannel]:
    if PRIMARY_LOG_CHANNEL_ID:
        explicit_channel = guild.get_channel(PRIMARY_LOG_CHANNEL_ID)
        if isinstance(explicit_channel, discord.TextChannel):
            resolved = {cfg["key"]: explicit_channel for cfg in LOG_CHANNEL_CONFIGS}
            _LOG_CHANNEL_CACHE[guild.id] = {key: channel.id for key, channel in resolved.items()}
            _LOG_INIT_DONE.add(guild.id)
            return resolved

    can_manage = bool(guild.me and guild.me.guild_permissions.manage_channels)

    category = _find_category(guild)
    if not category and can_manage:
        try:
            category = await await_create_category(guild, LOG_CATEGORY_NAME or "логи")
        except Exception:
            category = None

    resolved: dict[str, discord.TextChannel] = {}
    for cfg in LOG_CHANNEL_CONFIGS:
        ch = _find_channel_by_names(guild, cfg["names"], category)
        if not ch and can_manage:
            try:
                if category:
                    ch = await await_create_text_channel(guild, cfg["names"][0], category, cfg.get("topic"))
                else:
                    ch = await await_create_text_channel(guild, cfg["names"][0], None, cfg.get("topic"))
            except Exception:
                ch = None
        if ch:
            resolved[cfg["key"]] = ch

    if resolved:
        _LOG_CHANNEL_CACHE[guild.id] = {k: v.id for k, v in resolved.items()}
    _LOG_INIT_DONE.add(guild.id)
    return resolved


async def await_create_category(guild: discord.Guild, name: str):
    return await guild.create_category(name=name, reason="Автосоздание категории логов")


async def await_create_text_channel(
    guild: discord.Guild,
    name: str,
    category: discord.CategoryChannel | None,
    topic: str | None
):
    return await guild.create_text_channel(
        name=name,
        category=category,
        topic=topic,
        reason="Автосоздание канала логов"
    )


async def await_move_channel(channel: discord.TextChannel, category: discord.CategoryChannel):
    await channel.edit(category=category, reason="Перенос в категорию логов")


def get_log_channel(guild: discord.Guild | None, log_type: str = "server") -> discord.TextChannel | None:
    if not guild:
        return None

    if PRIMARY_LOG_CHANNEL_ID:
        explicit_channel = guild.get_channel(PRIMARY_LOG_CHANNEL_ID)
        if isinstance(explicit_channel, discord.TextChannel):
            _LOG_CHANNEL_CACHE.setdefault(guild.id, {})[log_type] = explicit_channel.id
            return explicit_channel

    cache = _LOG_CHANNEL_CACHE.get(guild.id)
    if cache and log_type in cache:
        ch = guild.get_channel(cache[log_type])
        if isinstance(ch, discord.TextChannel):
            return ch

    if guild.id not in _LOG_INIT_DONE:
        # попробуем создать/обновить
        try:
            # ensure_log_category_and_channels is async, но вызываем синхронно
            # реальная инициализация выполняется в on_ready
            pass
        except Exception:
            pass

    # fallback: поиск по имени
    category = _find_category(guild)
    for cfg in LOG_CHANNEL_CONFIGS:
        if cfg["key"] == log_type:
            ch = _find_channel_by_names(guild, cfg["names"], category)
            if ch:
                _LOG_CHANNEL_CACHE.setdefault(guild.id, {})[log_type] = ch.id
                return ch

    # fallback на server
    if log_type != "server":
        return get_log_channel(guild, "server")
    return None


def is_log_channel(channel: discord.abc.GuildChannel | None) -> bool:
    if not channel or not isinstance(channel, discord.TextChannel):
        return False
    if PRIMARY_LOG_CHANNEL_ID and channel.id == PRIMARY_LOG_CHANNEL_ID:
        return True
    if is_log_category(channel.category):
        return True
    cache = _LOG_CHANNEL_CACHE.get(channel.guild.id, {})
    return channel.id in cache.values()


async def send_log_embed(
    guild: discord.Guild | None,
    log_type: str,
    title: str,
    description: str,
    *,
    color: Color = Color.orange(),
    fields: list[tuple[str, str, bool]] | None = None,
    files: list[discord.File] | None = None,
    footer: str | None = None
) -> bool:
    channel = get_log_channel(guild, log_type)
    if not channel:
        return False

    emb = Embed(title=title, description=(description or "")[:4000], color=color, timestamp=discord.utils.utcnow())
    if fields:
        for name, value, inline in fields:
            emb.add_field(name=name, value=(value or "")[:1024], inline=inline)
    if footer:
        footer_text = footer[:2048]
    else:
        footer_text = LOG_TYPE_LABELS.get(log_type, "Логи")
    emb.set_footer(text=footer_text)
    emb.set_author(name=f"P.S.C Logs • {LOG_TYPE_LABELS.get(log_type, 'Логи')}")

    try:
        if files:
            await channel.send(embed=emb, files=files, allowed_mentions=discord.AllowedMentions.none())
        else:
            await channel.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        return False
