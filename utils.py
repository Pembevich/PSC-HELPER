import os
import re
from datetime import datetime, timezone

import aiohttp
import discord
from discord import Embed

from config import (
    VIRUSTOTAL_KEY,
    GOOGLE_SAFEBROWSING_KEY,
    DEEPSEEK_API_KEY,
    POS_AI_API_KEY,
    POS_AI_PROVIDER_KEYS,
    GITHUB_MODELS_TOKEN,
    POS_OWNER_USER_IDS,
)


def collect_runtime_health() -> dict:
    return {
        "DISCORD_TOKEN": bool(os.getenv("DISCORD_TOKEN")),
        "VIRUSTOTAL_KEY": bool(VIRUSTOTAL_KEY),
        "GOOGLE_SAFEBROWSING_KEY": bool(GOOGLE_SAFEBROWSING_KEY),
        "DEEPSEEK_API_KEY": bool(DEEPSEEK_API_KEY),
        "GITHUB_MODELS_TOKEN": bool(GITHUB_MODELS_TOKEN),
        "POS_AI_API_KEY": bool(POS_AI_API_KEY),
        "POS_AI_PROVIDER_KEYS": bool(POS_AI_PROVIDER_KEYS),
        "AI_PROVIDER_CONFIGURED": bool(POS_AI_API_KEY or POS_AI_PROVIDER_KEYS),
        "POS_OWNER_USER_IDS": bool(POS_OWNER_USER_IDS),
    }


def extract_clean_keyword(text: str):
    # #10: оставляем латиницу И кириллицу. Раньше [^a-z] вырезал все русские
    # буквы, из-за чего русские ответы в формах превращались в пустую строку.
    return re.sub(r"[^a-zа-яё]", "", (text or "").lower())


# Ведущий номер пункта: "1." / "2)" / "3 -" / "1:" и т.п. — отрезаем, чтобы в
# проверку ника шёл только сам ник, а не номер строки из шаблона формы.
_LEADING_ENUM = re.compile(r"^\s*\d{1,2}\s*[.)\-:]+\s*")


def strip_leading_enumeration(text: str) -> str:
    """Убрать ведущий номер пункта формы ("1.", "2)", "3 -") из строки-ответа.

    Срезается ТОЛЬКО номер с разделителем (.)-:), чтобы не задеть ники, которые
    сами начинаются с цифр (например '123gamer' остаётся как есть)."""
    return _LEADING_ENUM.sub("", (text or "").strip()).strip()


def sanitize_discord_token(raw_token: str | None) -> str | None:
    if raw_token is None:
        return None
    token = raw_token.strip()
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    token = token.replace("\n", "").replace("\r", "")
    return token or None


async def safe_send_dm(
    user: discord.User | discord.Member,
    embed: Embed,
    file: discord.File | None = None,
) -> bool:
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
        return True
    except Exception:
        return False


def assess_applicant_risk(roblox_nick: str, discord_nick: str, member: discord.Member):
    reasons = []
    rb = (roblox_nick or "").strip()
    dn = (discord_nick or "").strip()

    if re.search(r"https?://|discord\.gg|t\.me", rb + " " + dn, re.IGNORECASE):
        reasons.append("в нике есть ссылка/инвайт")
    if any(ch in rb + dn for ch in ["$", "@everyone", "@here"]):
        reasons.append("подозрительные символы в нике")
    if len(re.sub(r"\D", "", rb)) >= 5 or len(re.sub(r"\D", "", dn)) >= 5:
        reasons.append("много цифр в никнейме")
    if member and member.created_at:
        created = member.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < 14:
            reasons.append(f"свежий Discord-аккаунт ({age_days} дн.)")
    return reasons


# Подозрительные маркеры в описании Roblox-профиля (скам/реклама/обход).
_ROBLOX_DESC_BAD = re.compile(
    r"https?://|discord\.gg|t\.me|free\s*robux|nitro|giveaway|sell|продаю|читы?|cheat|aimbot",
    re.IGNORECASE,
)


async def assess_roblox_account(roblox_nick: str, *, timeout: float = 8.0) -> dict:
    """Проверяет Roblox-аккаунт по логину через публичный Roblox API.

    Возвращает словарь с данными аккаунта и списком флагов рисков. Никогда не
    кидает исключений — при сетевой ошибке/недоступности возвращает found=None и
    флаг о том, что проверку выполнить не удалось.
    """
    result: dict = {
        "query": (roblox_nick or "").strip(),
        "found": None,          # True/False/None(не удалось проверить)
        "user_id": None,
        "name": None,
        "display_name": None,
        "banned": False,
        "age_days": None,
        "created": None,
        "description": "",
        "profile_url": None,
        "flags": [],
    }
    nick = (roblox_nick or "").strip().strip("@")
    if not nick:
        result["found"] = False
        result["flags"].append("Roblox-логин не указан")
        return result

    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        # Явный SSL-контекст через certifi, если доступен — чтобы проверка работала
        # в любой среде (на некоторых хостах нет системных корневых сертификатов).
        connector = None
        try:
            import ssl
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        except Exception:
            connector = None
        async with aiohttp.ClientSession(timeout=client_timeout, connector=connector) as session:
            # 1. Логин -> userId
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [nick], "excludeBannedUsers": False},
            ) as resp:
                if resp.status != 200:
                    result["flags"].append("не удалось проверить Roblox (API недоступен)")
                    return result
                data = await resp.json()
            users = data.get("data") or []
            if not users:
                result["found"] = False
                result["flags"].append("Roblox-аккаунт с таким логином не найден")
                return result

            user = users[0]
            user_id = user.get("id")
            result["user_id"] = user_id
            result["name"] = user.get("name")
            result["display_name"] = user.get("displayName")
            result["profile_url"] = f"https://www.roblox.com/users/{user_id}/profile" if user_id else None

            # 2. Детали аккаунта
            async with session.get(f"https://users.roblox.com/v1/users/{user_id}") as resp2:
                if resp2.status == 200:
                    details = await resp2.json()
                    result["found"] = True
                    result["banned"] = bool(details.get("isBanned"))
                    result["description"] = (details.get("description") or "").strip()
                    created_raw = details.get("created")
                    if created_raw:
                        try:
                            created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                            if created_dt.tzinfo is None:
                                created_dt = created_dt.replace(tzinfo=timezone.utc)
                            result["created"] = created_dt
                            result["age_days"] = (datetime.now(timezone.utc) - created_dt).days
                        except (ValueError, AttributeError):
                            pass
                else:
                    result["found"] = True  # логин валиден, но детали не получили
    except Exception:
        result["found"] = None
        result["flags"].append("не удалось проверить Roblox (ошибка сети)")
        return result

    # --- Анализ рисков ---
    if result["banned"]:
        result["flags"].append("Roblox-аккаунт ЗАБАНЕН")
    age = result["age_days"]
    if age is not None:
        if age < 30:
            result["flags"].append(f"очень свежий Roblox-аккаунт ({age} дн.)")
        elif age < 90:
            result["flags"].append(f"молодой Roblox-аккаунт ({age} дн.)")
    if result["description"] and _ROBLOX_DESC_BAD.search(result["description"]):
        result["flags"].append("подозрительное описание Roblox-профиля (ссылки/реклама)")
    return result


def classify_applicant_danger(discord_flags: list, roblox: dict) -> tuple[str, bool]:
    """Сводит флаги Discord и Roblox в общий вердикт.

    Возвращает (уровень, опасен_ли): уровень ∈ {"low","medium","high"}.
    "Слишком опасен" (high) → True, чтобы форма явно предупредила проверяющих.
    """
    high = False
    if roblox.get("banned"):
        high = True
    if roblox.get("found") is False:
        high = True
    total = len(discord_flags) + len(roblox.get("flags", []))
    if high or total >= 4:
        return "high", True
    if total >= 2:
        return "medium", False
    return "low", False
