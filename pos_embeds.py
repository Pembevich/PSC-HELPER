"""Consistent, bounded Discord embeds for P.OS control-plane responses."""
from __future__ import annotations

from dataclasses import dataclass

import discord


_MAX_DESCRIPTION = 4096
_MAX_TITLE = 256
_MAX_FOOTER = 2048


@dataclass(frozen=True)
class _StatusStyle:
    label: str
    color: int


_STYLES = {
    "success": _StatusStyle("ВЫПОЛНЕНО", 0x42C97A),
    "warning": _StatusStyle("ТРЕБУЕТ ВНИМАНИЯ", 0xF2C94C),
    "error": _StatusStyle("НЕ ВЫПОЛНЕНО", 0xF04444),
    "info": _StatusStyle("ИНФОРМАЦИЯ", 0x29B6D1),
}


def classify_action_result(result: str) -> str:
    normalized = (result or "").strip().casefold()
    if normalized.startswith(
        (
            "ошибка:",
            "отказано:",
            "действие не ",
            "запрос не ",
            "не удалось ",
            "остановка отменена:",
        )
    ):
        return "error"
    if (
        "на подтверждение" in normalized
        or "ожидает решения" in normalized
        or "⚠" in normalized
    ):
        return "warning"
    return "success"


def _clean(value: object, limit: int, fallback: str = "—") -> str:
    text = str(value or "").strip() or fallback
    return text[: max(0, limit)]


def build_action_result_embed(
    operation: str,
    result: str,
    *,
    guild: discord.Guild | None = None,
    status: str | None = None,
) -> discord.Embed:
    style = _STYLES.get(status or classify_action_result(result), _STYLES["info"])
    operation_text = _clean(operation, 180, "Серверная операция")
    guild_text = (
        f"{_clean(guild.name, 80)} · {guild.id}"
        if guild is not None
        else "Discord"
    )
    result_text = _clean(result, _MAX_DESCRIPTION, "Discord API не вернул описание.")
    embed = discord.Embed(
        title=_clean(operation_text, _MAX_TITLE),
        description=result_text,
        color=style.color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"P.OS // {style.label}")
    embed.set_footer(text=_clean(guild_text, _MAX_FOOTER))
    return embed


def build_service_status_embed(
    title: str,
    detail: str,
    *,
    guild: discord.Guild | None = None,
    warning: bool = True,
) -> discord.Embed:
    return build_action_result_embed(
        title,
        detail,
        guild=guild,
        status="warning" if warning else "info",
    )
