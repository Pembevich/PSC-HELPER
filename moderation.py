from __future__ import annotations

import time
from datetime import timedelta
from collections import defaultdict, deque
from urllib.parse import urlparse, unquote

import aiohttp
import discord
import idna
from discord import Embed, Color

from config import (
    VIRUSTOTAL_KEY,
    GOOGLE_SAFEBROWSING_KEY,
    SUSPICIOUS_KEYWORDS,
    SUSPICIOUS_DOMAINS,
    SUSPICIOUS_PATH_KEYWORDS,
    WHITELIST_DOMAINS,
    URL_REGEX,
    _VT_CACHE_TTL,
    VOICE_TIMEOUT_HOURS,
    VIOLATION_ATTACHMENT_LIMIT,
    NSFW_FILENAME_KEYWORDS,
    AD_FILENAME_KEYWORDS,
    AD_TEXT_KEYWORDS,
    SUSPICIOUS_INVITE_PATTERNS,
    ALLOWED_DISCORD_INVITE_PATTERNS,
    SPAM_WINDOW_SECONDS,
    SPAM_DUPLICATES_THRESHOLD,
)
from logging_utils import get_log_channel

_vt_cache: dict[str, tuple[bool, float]] = {}
recent_messages = defaultdict(lambda: deque())


def extract_urls(text: str) -> list[str]:
    raw = URL_REGEX.findall(text or "")
    if not raw:
        return []
    cleaned = []
    for url in raw:
        url = url.rstrip(").,!?]>\"'")
        if url:
            cleaned.append(url)
    return cleaned


def _normalize_domain(raw_domain: str) -> str:
    try:
        d = raw_domain.split(":")[0].lower()
        try:
            d = idna.encode(d).decode("ascii")
        except Exception:
            pass
        return d
    except Exception:
        return raw_domain.lower()


def _domain_matches_blacklist(domain: str) -> bool:
    dom = _normalize_domain(domain)
    for bad in SUSPICIOUS_DOMAINS:
        if dom == bad or dom.endswith("." + bad):
            return True
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in dom:
            return True
    return False


def _domain_matches_whitelist(domain: str) -> bool:
    dom = _normalize_domain(domain)
    for good in WHITELIST_DOMAINS:
        if dom == good or dom.endswith("." + good):
            return True
    return False


async def check_google_safe_browsing(session: aiohttp.ClientSession, url: str) -> bool:
    key = GOOGLE_SAFEBROWSING_KEY
    if not key:
        return False
    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}"
    payload = {
        "client": {"clientId": "discord-bot", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}]
        }
    }
    try:
        async with session.post(endpoint, json=payload, timeout=10) as resp:
            if resp.status != 200:
                return False
            js = await resp.json()
            return "matches" in js and bool(js["matches"])
    except Exception as e:
        print(f"GSB error for {url}: {e}")
        return False


async def check_virustotal(session: aiohttp.ClientSession, url: str) -> bool:
    if not VIRUSTOTAL_KEY:
        return False

    headers = {"x-apikey": VIRUSTOTAL_KEY}
    try:
        async with session.post(
            "https://www.virustotal.com/api/v3/urls",
            data={"url": url},
            headers=headers,
            timeout=15
        ) as resp:
            if resp.status not in (200, 201):
                return False
            js = await resp.json()
            url_id = js.get("data", {}).get("id")
            if not url_id:
                return False

        async with session.get(
            f"https://www.virustotal.com/api/v3/analyses/{url_id}",
            headers=headers,
            timeout=15
        ) as resp2:
            if resp2.status != 200:
                return False
            res = await resp2.json()
            stats = res.get("data", {}).get("attributes", {}).get("stats", {})
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)

            return (malicious + suspicious) > 0

    except Exception as e:
        print(f"Ошибка VirusTotal: {e}")
        return False


async def check_and_handle_urls(message: discord.Message) -> bool:
    text = message.content or ""
    urls = extract_urls(text)
    if not urls:
        return False

    suspicious = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                parsed = urlparse(url if url.startswith("http") else "http://" + url)
                domain = (parsed.hostname or parsed.netloc or "").lower()
                path = (parsed.path or "") + ("?" + (parsed.query or "") if parsed.query else "")
                try:
                    path_dec = unquote(path).lower()
                except Exception:
                    path_dec = path.lower()
            except Exception:
                continue

            if _domain_matches_whitelist(domain):
                continue

            if _domain_matches_blacklist(domain):
                suspicious.append((url, "local-domain/keyword"))
                continue

            for pk in SUSPICIOUS_PATH_KEYWORDS:
                if pk in path_dec:
                    suspicious.append((url, f"path={pk}"))
                    break
            if any(u == url for u, _ in suspicious):
                continue

            try:
                cached = _vt_cache.get(url)
                if cached and (time.time() - cached[1]) < _VT_CACHE_TTL:
                    if cached[0]:
                        suspicious.append((url, "vt-cache"))
                        continue
                    else:
                        continue
            except Exception:
                pass

            try:
                gsb_bad = await check_google_safe_browsing(session, url)
                if gsb_bad:
                    suspicious.append((url, "GoogleSafeBrowsing"))
                    _vt_cache[url] = (True, time.time())
                    continue
            except Exception as e:
                print(f"GSB check error: {e}")

            try:
                vt_bad = await check_virustotal(session, url)
                _vt_cache[url] = (bool(vt_bad), time.time())
                if vt_bad:
                    suspicious.append((url, "VirusTotal"))
                    continue
            except Exception as e:
                print(f"VirusTotal check error: {e}")

    if not suspicious:
        return False

    reason_text = "\n".join(f"{u} -> {why}" for u, why in suspicious)
    reasons = [f"Подозрительная ссылка: {u} ({why})" for u, why in suspicious]

    try:
        await message.delete()
    except Exception:
        pass

    await apply_max_timeout(message.author, "Автомодерация: опасные ссылки")
    await log_violation_with_evidence(message, "🚨 Опасная ссылка", reasons)

    try:
        dm = Embed(
            title="⚠️ Ссылка заблокирована",
            description="Ваше сообщение удалено: обнаружены подозрительные ссылки. Вам выдано ограничение голоса на 24 часа.",
            color=Color.red()
        )
        dm.add_field(name="Детали", value=f"```{reason_text[:1900]}```", inline=False)
        await message.author.send(embed=dm)
    except Exception:
        pass

    return True


async def apply_max_timeout(member: discord.Member, reason: str):
    until = discord.utils.utcnow() + timedelta(hours=VOICE_TIMEOUT_HOURS)

    me = member.guild.me if member.guild else None
    if me and not me.guild_permissions.moderate_members:
        print("Не удалось выдать ограничение голоса: у бота нет права Moderate Members")
        return False

    try:
        await member.edit(timed_out_until=until, reason=reason)
        return True
    except TypeError:
        try:
            await member.edit(timeout=until, reason=reason)
            return True
        except Exception as e:
            print(f"Не удалось выдать ограничение голоса: {e}")
            return False
    except Exception as e:
        print(f"Не удалось выдать ограничение голоса: {e}")
        return False


async def log_violation_with_evidence(message: discord.Message, title: str, reasons: list[str]):
    if not message.guild:
        return

    emb = Embed(title=title, color=Color.red(), timestamp=discord.utils.utcnow())
    emb.add_field(name="Пользователь", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
    emb.add_field(name="Канал", value=message.channel.mention, inline=False)
    emb.add_field(name="Причины", value="\n".join(f"• {r}" for r in reasons)[:1024], inline=False)
    emb.add_field(name="Контент", value=(message.content or "(без текста)")[:1000], inline=False)

    if message.attachments:
        links = "\n".join(a.url for a in message.attachments[:5])
        emb.add_field(name="Вложения (URL)", value=links[:1024], inline=False)

    files = []
    for a in message.attachments[:VIOLATION_ATTACHMENT_LIMIT]:
        try:
            files.append(await a.to_file())
        except Exception:
            pass

    try:
        channel = get_log_channel(message.guild, "moderation")
        if not channel:
            return
        if files:
            await channel.send(embed=emb, files=files)
        else:
            await channel.send(embed=emb)
    except Exception as e:
        print(f"Ошибка логирования нарушения: {e}")


def detect_advertising_or_scam_text(text: str):
    t = (text or "").lower()
    reasons = []

    has_discord_invite = any(p in t for p in ALLOWED_DISCORD_INVITE_PATTERNS)

    if any(p in t for p in SUSPICIOUS_INVITE_PATTERNS):
        reasons.append("инвайт/внешняя ссылка для рекламы")

    if any(k in t for k in AD_TEXT_KEYWORDS):
        reasons.append("рекламные/скам ключевые слова")

    if has_discord_invite and reasons == ["инвайт/внешняя ссылка для рекламы"]:
        return []

    return reasons


def detect_attachment_violations(attachments):
    reasons = []
    for att in attachments:
        name = (att.filename or "").lower()
        ctype = (att.content_type or "").lower()
        if any(k in name for k in NSFW_FILENAME_KEYWORDS):
            reasons.append(f"NSFW по имени файла: {att.filename}")
        if any(k in name for k in AD_FILENAME_KEYWORDS):
            reasons.append(f"реклама/скам по имени файла: {att.filename}")
        if ctype.startswith("video/") and any(k in name for k in ["casino", "porn", "nitro", "robux"]):
            reasons.append(f"подозрительное видео: {att.filename}")
    return reasons


def message_key_for_spam(message: discord.Message):
    text = (message.content or "").strip()
    att_summary = [a.filename for a in message.attachments]
    return f"{text}||{'|'.join(att_summary)}"


async def handle_spam_if_needed(message: discord.Message):
    user_id = message.author.id
    key = message_key_for_spam(message)
    now = time.time()
    dq = recent_messages[user_id]
    dq.append((key, now, message.id, message.channel.id))

    while dq and now - dq[0][1] > SPAM_WINDOW_SECONDS:
        dq.popleft()

    count = sum(1 for k, _, _, _ in dq if k == key)
    if count < SPAM_DUPLICATES_THRESHOLD:
        return False

    to_delete = [mid for k0, _, mid, cid in dq if k0 == key and cid == message.channel.id]
    for mid in to_delete:
        try:
            msg = await message.channel.fetch_message(mid)
            if msg:
                await msg.delete()
        except Exception:
            pass

    reasons = [f"Спам одинаковыми сообщениями: {count} шт. за {SPAM_WINDOW_SECONDS} сек."]
    await apply_max_timeout(message.author, "Автомодерация: спам")
    await log_violation_with_evidence(message, "🚨 STOPREID: спам", reasons)

    try:
        dm = Embed(
            title="🚫 Обнаружен спам",
            description="Вы отправляли повторяющиеся сообщения. Выдано ограничение голоса на 24 часа.",
            color=Color.orange()
        )
        dm.add_field(name="Детали", value=reasons[0], inline=False)
        await message.author.send(embed=dm)
    except Exception:
        pass

    recent_messages[user_id].clear()
    return True
