from __future__ import annotations

import logging
from typing import Dict, List, TypedDict

import discord
from discord import Embed, Color

from config import LOG_CATEGORY_ID, LOG_CATEGORY_NAME, PRIMARY_LOG_CHANNEL_ID

logger = logging.getLogger(__name__)

class LogChannelConfig(TypedDict):
    key: str
    names: List[str]
    topic: str


LOG_CHANNEL_CONFIGS: List[LogChannelConfig] = [
    {
        "key": "moderation",
        "names": ["логи-модерации", "mod-logs", "moderation-logs"],
        "topic": "Логи модерации и автомодерации"
    },
    {
        "key": "security",
        "names": ["логи-безопасности", "security-logs"],
        "topic": "Антирейд, AI-инструменты управления, security presets и аудит защиты"
    },
    {
        "key": "messages",
        "names": ["логи-сообщений", "message-logs"],
        "topic": "Логи всех сообщений"
    },
    {
        "key": "message_edits",
        "names": ["логи-правок", "message-edits"],
        "topic": "Логи редактирования сообщений"
    },
    {
        "key": "message_deletes",
        "names": ["логи-удалений", "message-deletes"],
        "topic": "Логи удалённых сообщений"
    },
    {
        "key": "members",
        "names": ["логи-участников", "member-logs"],
        "topic": "Вход/выход, никнеймы, роли"
    },
    {
        "key": "voice",
        "names": ["логи-голоса", "voice-logs"],
        "topic": "События голосовых каналов"
    },
    {
        "key": "roles",
        "names": ["логи-ролей", "role-logs"],
        "topic": "Создание/изменение/удаление ролей"
    },
    {
        "key": "channels",
        "names": ["логи-каналов", "channel-logs"],
        "topic": "Создание/изменение/удаление каналов"
    },
    {
        "key": "server",
        "names": ["логи-сервера", "server-logs"],
        "topic": "Прочие серверные события"
    },
    {
        "key": "commands",
        "names": ["логи-команд", "command-logs"],
        "topic": "Использование команд"
    },
    {
        "key": "forms",
        "names": ["логи-форм", "form-logs"],
        "topic": "Жалобы/формы/заявки"
    },
    {
        "key": "errors",
        "names": ["логи-ошибок", "error-logs"],
        "topic": "Ошибки и исключения"
    }
]

_LOG_CHANNEL_CACHE: Dict[int, Dict[str, int]] = {}
_LOG_INIT_DONE: set[int] = set()
LOG_TYPE_LABELS = {
    "moderation": "Модерация",
    "security": "Безопасность",
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
    if target and target == name_l:
        return True
    return name_l in {"логи", "logs", "p.os logs", "p.os логи"}


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
    # Support a custom name created by setup_guild_logging without trusting the
    # name itself: require a private category containing several exact log names.
    known_names = {
        name.lower()
        for cfg in LOG_CHANNEL_CONFIGS
        for name in cfg["names"]
    }
    for cat in guild.categories:
        exact_log_channels = sum(
            1
            for channel in cat.channels
            if isinstance(channel, discord.TextChannel) and channel.name.lower() in known_names
        )
        default_overwrite = cat.overwrites_for(guild.default_role)
        if exact_log_channels >= 3 and default_overwrite.view_channel is False:
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
    return None


async def ensure_log_category_and_channels(guild: discord.Guild) -> dict[str, discord.TextChannel]:
    """Discover an explicitly configured private log area without mutating Discord."""
    _LOG_CHANNEL_CACHE.pop(guild.id, None)
    if PRIMARY_LOG_CHANNEL_ID:
        explicit_channel = guild.get_channel(PRIMARY_LOG_CHANNEL_ID)
        if isinstance(explicit_channel, discord.TextChannel):
            explicit_channels = {cfg["key"]: explicit_channel for cfg in LOG_CHANNEL_CONFIGS}
            _LOG_CHANNEL_CACHE[guild.id] = {key: channel.id for key, channel in explicit_channels.items()}
            _LOG_INIT_DONE.add(guild.id)
            return explicit_channels

    category = _find_category(guild)

    resolved: dict[str, discord.TextChannel] = {}
    for cfg in LOG_CHANNEL_CONFIGS:
        ch = _find_channel_by_names(guild, cfg["names"], category)
        if ch:
            resolved[cfg["key"]] = ch

    if resolved:
        _LOG_CHANNEL_CACHE[guild.id] = {k: v.id for k, v in resolved.items()}
    _LOG_INIT_DONE.add(guild.id)
    return resolved


def _admin_only_overwrites(guild: discord.Guild) -> dict:
    """Права для лог-категории: всё скрыто от @everyone, на чтение видно только
    самому боту и ролям с правом «Администратор» или «Просмотр журнала аудита»
    (модераторы). Писать в логи может только бот."""
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True, manage_channels=True
        )
    for role in guild.roles:
        if role.is_default():
            continue
        if role.permissions.administrator or role.permissions.view_audit_log:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
    return overwrites


def _find_log_channel_in_category(
    category: discord.CategoryChannel | None,
    names: list[str],
) -> discord.TextChannel | None:
    """Строгий поиск лог-канала ТОЛЬКО внутри уже существующей лог-категории.

    В отличие от _find_channel_by_names, не делает поиск по всему серверу и не
    цепляет обычные каналы по подстроке (voice, roles, server и т.п.). Используется
    в setup_guild_logging, чтобы случайно не перенести/не скрыть посторонний канал.
    """
    if not category:
        return None
    names_l = {n.lower() for n in names}
    for ch in category.channels:
        if isinstance(ch, discord.TextChannel) and ch.name.lower() in names_l:
            return ch
    return None


async def setup_guild_logging(
    guild: discord.Guild,
    category_name: str | None = None,
) -> tuple[bool, str]:
    """Разворачивает систему логов на сервере по запросу: создаёт (или дополняет)
    категорию и каналы логов, видимые только администраторам.

    Возвращает (успех, текст-отчёт для пользователя).
    """
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        return False, "У меня нет права «Управление каналами» на этом сервере — не могу развернуть логи."

    overwrites = _admin_only_overwrites(guild)
    cat_name = (category_name or LOG_CATEGORY_NAME or "логи").strip()[:100]

    category = _find_category(guild)
    created_category = False
    if not category:
        try:
            category = await guild.create_category(
                name=cat_name,
                overwrites=overwrites,
                reason="P.OS: разворачивание системы логов по запросу",
            )
            created_category = True
        except Exception:
            logger.exception("Не удалось создать категорию логов на сервере %s.", guild.id)
            return False, "Не смог создать категорию логов: Discord отклонил операцию."
    else:
        try:
            await category.edit(overwrites=overwrites, reason="P.OS: обновление прав категории логов")
        except Exception:
            logger.exception("Не удалось обновить права категории логов на сервере %s.", guild.id)
            return False, "Не смог закрыть категорию логов от обычных участников: Discord отклонил операцию."

    created_channels: list[str] = []
    existing_channels = 0
    failed = 0
    for cfg in LOG_CHANNEL_CONFIGS:
        # Строго ищем только внутри лог-категории — не трогаем посторонние каналы.
        ch = _find_log_channel_in_category(category, cfg["names"])
        if ch:
            existing_channels += 1
            try:
                await ch.edit(sync_permissions=True, reason="P.OS: синхронизация прав лог-канала")
            except Exception:
                failed += 1
            continue
        try:
            ch = await guild.create_text_channel(
                name=cfg["names"][0],
                category=category,
                topic=cfg.get("topic"),
                overwrites=overwrites,
                reason="P.OS: создание канала логов по запросу",
            )
            created_channels.append(ch.name)
        except Exception:
            failed += 1

    resolved: dict[str, int] = {}
    for cfg in LOG_CHANNEL_CONFIGS:
        ch = _find_log_channel_in_category(category, cfg["names"])
        if ch:
            resolved[cfg["key"]] = ch.id
    if resolved:
        _LOG_CHANNEL_CACHE[guild.id] = resolved
    _LOG_INIT_DONE.add(guild.id)

    report_parts = [
        f"Категория `{category.name}` {'создана' if created_category else 'уже была'}.",
        f"Создано каналов: `{len(created_channels)}`, уже существовало: `{existing_channels}`.",
    ]
    if failed:
        report_parts.append(f"Не удалось создать: `{failed}` (проверь права и лимит каналов).")
    report_parts.append("Доступ к логам открыт только администраторам и ролям с доступом к журналу аудита.")
    return True, " ".join(report_parts)


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

    # Strict discovery inside the exact log category only.
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


def is_log_channel(channel: object) -> bool:
    if not channel:
        return False
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if parent is None:
            return False
        if PRIMARY_LOG_CHANNEL_ID and parent.id == PRIMARY_LOG_CHANNEL_ID:
            return True
        if is_log_category(getattr(parent, "category", None)):
            return True
        cache = _LOG_CHANNEL_CACHE.get(channel.guild.id, {})
        return parent.id in cache.values()
    if not isinstance(channel, discord.TextChannel):
        return False
    if PRIMARY_LOG_CHANNEL_ID and channel.id == PRIMARY_LOG_CHANNEL_ID:
        return True
    if is_log_category(channel.category):
        return True
    cache = _LOG_CHANNEL_CACHE.get(channel.guild.id, {})
    return channel.id in cache.values()


def _truncate_log_text(value: object, limit: int, fallback: str = "") -> str:
    text = str(value or "").strip() or fallback
    return text[:max(0, limit)]


def _build_log_embed(
    log_type: str,
    title: str,
    description: str,
    *,
    color: Color,
    fields: list[tuple[str, str, bool]] | None,
    footer: str | None,
) -> Embed:
    """Build an embed that always respects Discord's per-part and 6000-char limits."""
    label = LOG_TYPE_LABELS.get(log_type, "Логи")
    author_text = _truncate_log_text(f"P.S.C Logs • {label}", 256, "P.S.C Logs")
    title_text = _truncate_log_text(title, 256, "Журнал P.OS")
    footer_text = _truncate_log_text(footer or label, 2048, label)
    total_budget = 6000 - len(author_text) - len(title_text) - len(footer_text)

    description_limit = 3500 if fields else 4096
    description_text = _truncate_log_text(
        description,
        min(description_limit, max(0, total_budget)),
    )
    total_budget -= len(description_text)

    emb = Embed(
        title=title_text,
        description=description_text or None,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    for raw_name, raw_value, inline in (fields or [])[:25]:
        if total_budget < 2:
            break
        name = _truncate_log_text(raw_name, min(256, total_budget - 1), "—")
        value_budget = min(1024, total_budget - len(name))
        if value_budget < 1:
            break
        value = _truncate_log_text(raw_value, value_budget, "—")
        emb.add_field(name=name, value=value, inline=bool(inline))
        total_budget -= len(name) + len(value)

    emb.set_footer(text=footer_text)
    emb.set_author(name=author_text)
    return emb


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
    if guild:
        try:
            from storage import add_ai_event
            await add_ai_event(
                guild_id=guild.id,
                event_type=f"log:{log_type}",
                summary=f"{title}: {(description or '')[:900]}",
                details={
                    "title": title,
                    "description": description or "",
                    "fields": [
                        {"name": name, "value": value, "inline": inline}
                        for name, value, inline in (fields or [])
                    ],
                },
            )
        except Exception:
            logger.exception("Не удалось сохранить log:%s в SQLite для сервера %s.", log_type, guild.id)

    channel = get_log_channel(guild, log_type)
    if not channel:
        return False

    emb = _build_log_embed(
        log_type,
        title,
        description,
        color=color,
        fields=fields,
        footer=footer,
    )

    try:
        if files:
            await channel.send(
                embed=emb,
                files=files[:10],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await channel.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as exc:
        logger.warning(
            "Не удалось доставить log:%s в канал %s сервера %s (%s).",
            log_type,
            channel.id,
            guild.id if guild else 0,
            type(exc).__name__,
        )
        return False
