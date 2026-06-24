"""
guild_config.py — per-guild runtime settings (0.8).

Тонкая обёртка над storage.guild_settings: хранит JSON-настройки модерации/
поведения на каждый сервер, сливает их с дефолтами из config и кэширует в
памяти. Менять настройки может только владелец — это проверяется в pos_ai.

guild_id=0 трактуется как глобальный слот значений по умолчанию.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any

from config import DEFAULT_MODERATION_SETTINGS
from storage import get_guild_settings_raw, set_guild_settings_raw

# Кэш слитых настроек на guild_id, чтобы не дёргать БД на каждое сообщение.
_cache: dict[int, dict[str, Any]] = {}
_cache_ts: dict[int, float] = {}
_CACHE_TTL = 60.0

# Допустимые ключи и их типы для валидации пользовательского ввода через ИИ.
_BOOL_KEYS = {
    "enabled", "filter_ads", "filter_spam", "filter_flood",
    "filter_scam", "filter_nsfw", "allow_profanity",
    "filter_raid", "filter_mention_spam", "filter_crosschannel", "ai_moderation",
}
_INT_KEYS = {
    "spam_window_seconds", "spam_duplicates_threshold",
    "flood_window_seconds", "flood_messages_threshold",
    "timeout_hours",
    "mention_limit", "raid_join_window_seconds", "raid_join_threshold",
    "min_account_age_hours",
}
# Строковые ключи-перечисления.
_ENUM_KEYS = {
    "raid_action": {"alert", "quarantine", "kick", "ban", "lockdown"},
}
SETTING_KEYS = _BOOL_KEYS | _INT_KEYS | set(_ENUM_KEYS)

# Разумные границы для целочисленных настроек (защита от абсурдных значений).
_INT_BOUNDS = {
    "spam_window_seconds": (1, 120),
    "spam_duplicates_threshold": (2, 50),
    "flood_window_seconds": (1, 120),
    "flood_messages_threshold": (3, 100),
    "timeout_hours": (1, 672),  # до 28 суток (лимит Discord timeout)
    "mention_limit": (3, 50),
    "raid_join_window_seconds": (10, 600),
    "raid_join_threshold": (3, 100),
    "min_account_age_hours": (0, 8760),  # до года
}


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"true", "1", "да", "yes", "on", "вкл", "включить", "включи"}:
        return True
    if s in {"false", "0", "нет", "no", "off", "выкл", "выключить", "выключи"}:
        return False
    return None


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_MODERATION_SETTINGS)


def _merge_with_defaults(stored: dict[str, Any] | None) -> dict[str, Any]:
    merged = defaults()
    if stored:
        for key, value in stored.items():
            if key in merged:
                merged[key] = value
    return merged


async def get_settings(guild_id: int) -> dict[str, Any]:
    """Слитые с дефолтами настройки сервера (с кэшированием)."""
    now = time.time()
    cached = _cache.get(guild_id)
    if cached is not None and now - _cache_ts.get(guild_id, 0.0) < _CACHE_TTL:
        return cached

    raw = await get_guild_settings_raw(guild_id)
    stored: dict[str, Any] | None = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                stored = parsed
        except Exception:
            stored = None

    merged = _merge_with_defaults(stored)
    _cache[guild_id] = merged
    _cache_ts[guild_id] = now
    return merged


def coerce_value(key: str, value: Any) -> tuple[bool, Any]:
    """Привести значение к корректному типу/диапазону для ключа.

    Возвращает (ok, coerced_value). ok=False, если значение невалидно.
    """
    if key in _BOOL_KEYS:
        parsed = _parse_bool(value)
        if parsed is None:
            return False, None
        return True, parsed
    if key in _INT_KEYS:
        try:
            num = int(str(value).strip())
        except (ValueError, TypeError):
            return False, None
        lo, hi = _INT_BOUNDS.get(key, (0, 10 ** 9))
        return True, max(lo, min(num, hi))
    if key in _ENUM_KEYS:
        s = str(value).strip().lower()
        if s in _ENUM_KEYS[key]:
            return True, s
        return False, None
    return False, None


async def update_settings(guild_id: int, changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Применить изменения к настройкам сервера.

    Возвращает (новые_настройки, список_отклонённых_ключей). Неизвестные или
    невалидные ключи игнорируются и попадают в список отклонённых.
    """
    current = await get_settings(guild_id)
    updated = copy.deepcopy(current)
    rejected: list[str] = []

    for key, value in (changes or {}).items():
        if key not in SETTING_KEYS:
            rejected.append(key)
            continue
        ok, coerced = coerce_value(key, value)
        if not ok:
            rejected.append(key)
            continue
        updated[key] = coerced

    await set_guild_settings_raw(guild_id, json.dumps(updated, ensure_ascii=False))
    _cache[guild_id] = updated
    _cache_ts[guild_id] = time.time()
    return updated, rejected


def invalidate(guild_id: int | None = None) -> None:
    if guild_id is None:
        _cache.clear()
        _cache_ts.clear()
    else:
        _cache.pop(guild_id, None)
        _cache_ts.pop(guild_id, None)
