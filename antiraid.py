"""
antiraid.py — детектор рейдов и скрининг подозрительных заходов (0.8).

Чистая логика без побочных эффектов Discord: считает заходы по окну, держит
состояние «режима рейда» на сервер, оценивает аккаунты (возраст, аватар, ник) и
возвращает решение. Реальные действия (kick/ban/lockdown/лог) выполняет ког
cogs/security.py — так логику легко тестировать.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any

import discord

# Заходы по серверам: guild_id -> deque[timestamp]
_joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=500))
# Активный режим рейда: guild_id -> время (monotonic-эпоха time.time()), до которого держится
_raid_until: dict[int, float] = {}
_MAX_GUILDS_TRACKED = 5000

# Ник из случайных символов / спам-паттерн.
_TRAILING_DIGITS = re.compile(r"\d{4,}$")
_INVITE_IN_NAME = re.compile(r"(discord\.gg|discord\.com/invite|free\s*nitro|t\.me/|@everyone)", re.IGNORECASE)
_NON_PRONOUNCEABLE = re.compile(r"^[bcdfghjklmnpqrstvwxyz]{7,}$", re.IGNORECASE)


def _prune(now: float) -> None:
    if len(_joins) > _MAX_GUILDS_TRACKED:
        stale = [gid for gid, dq in _joins.items() if not dq or now - dq[-1] > 3600]
        for gid in stale[: len(stale) // 2 + 1]:
            _joins.pop(gid, None)
    for gid, until in list(_raid_until.items()):
        if until <= now:
            _raid_until.pop(gid, None)


def account_age_hours(member: discord.abc.User) -> float | None:
    created = getattr(member, "created_at", None)
    if not created:
        return None
    try:
        now = discord.utils.utcnow()
        return max((now - created).total_seconds() / 3600.0, 0.0)
    except Exception:
        return None


def is_fresh_account(member: discord.abc.User, min_hours: int) -> bool:
    age = account_age_hours(member)
    if age is None:
        return False
    return age < float(min_hours)


def _has_default_avatar(member: discord.abc.User) -> bool:
    # У аккаунта нет своей аватарки (стоит дефолтная Discord).
    return getattr(member, "avatar", None) is None


def suspicious_join_signals(member: discord.abc.User, min_account_age_hours: int) -> list[str]:
    """Список «красных флагов» аккаунта (без побочных эффектов)."""
    signals: list[str] = []
    age = account_age_hours(member)
    if age is not None and age < float(min_account_age_hours):
        if age < 1:
            signals.append("аккаунт создан менее часа назад")
        else:
            signals.append(f"свежий аккаунт (~{int(age)} ч)")
    if _has_default_avatar(member):
        signals.append("нет аватара")
    name = (getattr(member, "name", "") or "")
    if _INVITE_IN_NAME.search(name):
        signals.append("инвайт/реклама в нике")
    if _TRAILING_DIGITS.search(name):
        signals.append("ник заканчивается на длинный набор цифр")
    if _NON_PRONOUNCEABLE.match(name):
        signals.append("ник похож на случайный набор букв")
    return signals


def register_join(guild_id: int, *, now: float, window: int, threshold: int) -> tuple[int, bool]:
    """Зарегистрировать заход и вернуть (кол-во_за_окно, сработал_ли_порог_рейда)."""
    _prune(now)
    dq = _joins[guild_id]
    dq.append(now)
    while dq and now - dq[0] > window:
        dq.popleft()
    count = len(dq)
    triggered = count >= threshold
    return count, triggered


def set_raid_mode(guild_id: int, *, now: float, cooldown: int) -> None:
    _raid_until[guild_id] = max(_raid_until.get(guild_id, 0.0), now + cooldown)


def is_raid_mode(guild_id: int, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    until = _raid_until.get(guild_id, 0.0)
    if until <= now:
        _raid_until.pop(guild_id, None)
        return False
    return True


def clear(guild_id: int | None = None) -> None:
    if guild_id is None:
        _joins.clear()
        _raid_until.clear()
    else:
        _joins.pop(guild_id, None)
        _raid_until.pop(guild_id, None)


def deactivate_raid_mode(guild_id: int, *, now: float | None = None) -> bool:
    """Принудительно снять режим рейда на сервере (по команде владельца).

    Возвращает True, если режим был активен и снят, иначе False."""
    was_active = is_raid_mode(guild_id, now=now)
    _raid_until.pop(guild_id, None)
    _joins.pop(guild_id, None)
    return was_active


def evaluate_join(member: discord.Member, settings: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Главная точка: обновить трекер и решить, что делать с этим заходом.

    Возвращает dict:
      raid (bool)        — сработал ли порог рейда прямо сейчас,
      raid_mode (bool)   — активен ли режим рейда (вкл. только что сработавший),
      join_count (int)   — заходов за окно,
      signals (list)     — красные флаги аккаунта,
      fresh (bool)       — свежий ли аккаунт,
      action (str)       — none | alert | kick | ban | lockdown,
      reason (str)       — человекочитаемая причина для лога/DM.
    """
    now = time.time() if now is None else now
    if not settings.get("filter_raid", True):
        return {"raid": False, "raid_mode": False, "join_count": 0, "signals": [],
                "fresh": False, "action": "none", "reason": ""}

    window = int(settings.get("raid_join_window_seconds", 60) or 60)
    threshold = int(settings.get("raid_join_threshold", 8) or 8)
    cooldown = int(settings.get("raid_mode_cooldown_seconds", 600) or 600)
    min_age = int(settings.get("min_account_age_hours", 72) or 72)
    raid_action = str(settings.get("raid_action", "kick") or "kick").lower()

    gid = member.guild.id
    count, triggered = register_join(gid, now=now, window=window, threshold=threshold)
    if triggered:
        set_raid_mode(gid, now=now, cooldown=cooldown)
    raid_mode = is_raid_mode(gid, now=now)

    signals = suspicious_join_signals(member, min_age)
    fresh = is_fresh_account(member, min_age)

    action = "none"
    reason = ""
    if raid_mode:
        # В режиме рейда: свежие/подозрительные аккаунты — под действие, остальные —
        # только алерт (не наказываем легитимных участников, зашедших в тот же момент).
        if fresh or signals:
            action = raid_action if raid_action in {"quarantine", "kick", "ban", "lockdown"} else "alert"
            reason = f"Режим рейда (заходов за {window}с: {count}). Аккаунт: " + (", ".join(signals) or "свежий")
        else:
            action = "alert"
            reason = f"Режим рейда (заходов за {window}с: {count}). Аккаунт без явных флагов — наблюдение."
    elif len(signals) >= 2:
        # Вне рейда: явно подозрительный аккаунт — только алерт, без наказания.
        action = "alert"
        reason = "Подозрительный заход: " + ", ".join(signals)

    return {
        "raid": triggered,
        "raid_mode": raid_mode,
        "join_count": count,
        "signals": signals,
        "fresh": fresh,
        "action": action,
        "reason": reason,
    }
