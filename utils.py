import os
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import Embed

from config import VIRUSTOTAL_KEY, GOOGLE_SAFEBROWSING_KEY, DEEPSEEK_API_KEY


def collect_runtime_health() -> dict:
    return {
        "DISCORD_TOKEN": bool(os.getenv("DISCORD_TOKEN")),
        "VIRUSTOTAL_KEY": bool(VIRUSTOTAL_KEY),
        "GOOGLE_SAFEBROWSING_KEY": bool(GOOGLE_SAFEBROWSING_KEY),
        "DEEPSEEK_API_KEY": bool(DEEPSEEK_API_KEY),
    }


def extract_clean_keyword(text: str):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def sanitize_discord_token(raw_token: str | None) -> str | None:
    if raw_token is None:
        return None
    token = raw_token.strip()
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    token = token.replace("\n", "").replace("\r", "")
    return token or None


async def safe_send_dm(user: discord.User, embed: Embed, file: discord.File = None):
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
    except Exception:
        pass


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
