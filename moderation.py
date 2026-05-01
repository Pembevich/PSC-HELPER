from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlparse, unquote

import asyncio
import aiohttp
import discord
import idna
from PIL import Image, ImageOps
from discord import Color, Embed

from ai_client import ai_is_temporarily_unavailable, extract_json_block, pos_chat_completion
from config import (
    AD_FILENAME_KEYWORDS,
    AD_TEXT_KEYWORDS,
    ALLOWED_DISCORD_INVITE_PATTERNS,
    GOOGLE_SAFEBROWSING_KEY,
    NSFW_FILENAME_KEYWORDS,
    POS_AI_API_KEY,
    SPAM_DUPLICATES_THRESHOLD,
    SPAM_WINDOW_SECONDS,
    SUSPICIOUS_DOMAINS,
    SUSPICIOUS_INVITE_PATTERNS,
    SUSPICIOUS_KEYWORDS,
    SUSPICIOUS_PATH_KEYWORDS,
    URL_REGEX,
    VIRUSTOTAL_KEY,
    VIOLATION_ATTACHMENT_LIMIT,
    VOICE_TIMEOUT_HOURS,
    WHITELIST_DOMAINS,
    _VT_CACHE_TTL,
)
from logging_utils import get_log_channel

try:
    from moviepy import VideoFileClip
except Exception:
    VideoFileClip = None

HIGH_CONFIDENCE_PATH_MARKERS = {
    "free-nitro",
    "discord-gift",
    "discordgift",
    "nitroclaim",
    "free-robux",
    "wallet-connect",
    "accountverify",
    "password-reset",
}
AI_URL_CACHE_TTL = 6 * 60 * 60
AI_MEDIA_MAX_ATTACHMENTS = 2
AI_MEDIA_MAX_BYTES = 20 * 1024 * 1024
AI_IMAGE_MAX_SIDE = 1280
AI_VIDEO_FRAME_COUNT = 3
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".avi", ".mkv")

_vt_cache: dict[str, tuple[bool, float]] = {}
_ai_url_cache: dict[str, tuple[str, str, float]] = {}

# Per-user rate limit для AI-проверок медиа: не более 1 AI-вызова за 15 сек на пользователя
_AI_MEDIA_USER_LAST_CHECK: dict[int, float] = {}
AI_MEDIA_USER_COOLDOWN_SECONDS = 15

recent_messages = defaultdict(lambda: deque())


def _text_has_media_risk_signals(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    keywords = {
        *AD_TEXT_KEYWORDS,
        *NSFW_FILENAME_KEYWORDS,
        "скам",
        "фишинг",
        "эрот",
        "18+",
        "adult",
        "porn",
        "casino",
        "казино",
    }
    return any(keyword in lowered for keyword in keywords)


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
        domain = raw_domain.split(":")[0].lower()
        try:
            domain = idna.encode(domain).decode("ascii")
        except Exception:
            pass
        return domain
    except Exception:
        return raw_domain.lower()


def _domain_matches_blacklist(domain: str) -> bool:
    normalized = _normalize_domain(domain)
    for bad in SUSPICIOUS_DOMAINS:
        if normalized == bad or normalized.endswith("." + bad):
            return True
    for keyword in SUSPICIOUS_KEYWORDS:
        if keyword in normalized:
            return True
    return False


def _domain_matches_whitelist(domain: str) -> bool:
    normalized = _normalize_domain(domain)
    for good in WHITELIST_DOMAINS:
        if normalized == good or normalized.endswith("." + good):
            return True
    return False


def _is_image_attachment(att: discord.Attachment) -> bool:
    content_type = (att.content_type or "").lower()
    name = (att.filename or "").lower()
    return content_type.startswith("image/") or name.endswith(IMAGE_EXTENSIONS)


def _is_video_attachment(att: discord.Attachment) -> bool:
    content_type = (att.content_type or "").lower()
    name = (att.filename or "").lower()
    return content_type.startswith("video/") or name.endswith(VIDEO_EXTENSIONS)


def _extract_path_keywords(path_text: str) -> list[str]:
    text = path_text.lower()
    matched = [keyword for keyword in SUSPICIOUS_PATH_KEYWORDS if keyword in text]
    seen = set()
    unique = []
    for keyword in matched:
        if keyword not in seen:
            unique.append(keyword)
            seen.add(keyword)
    return unique


def _build_data_url_from_image_bytes(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            frame = ImageOps.exif_transpose(image)
            if getattr(frame, "is_animated", False):
                frame.seek(0)
            if frame.mode not in ("RGB", "RGBA"):
                frame = frame.convert("RGBA")

            if max(frame.size) > AI_IMAGE_MAX_SIDE:
                frame.thumbnail((AI_IMAGE_MAX_SIDE, AI_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            if frame.mode == "RGBA":
                frame.save(output, format="PNG", optimize=True)
                mime = "image/png"
            else:
                frame = frame.convert("RGB")
                frame.save(output, format="JPEG", quality=88, optimize=True)
                mime = "image/jpeg"
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
    except Exception:
        return None


def _build_data_url_from_frame(frame) -> str | None:
    try:
        image = Image.fromarray(frame)
        if max(image.size) > AI_IMAGE_MAX_SIDE:
            image.thumbnail((AI_IMAGE_MAX_SIDE, AI_IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=84, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return None


async def _attachment_image_to_data_url(att: discord.Attachment) -> str | None:
    if att.size and att.size > AI_MEDIA_MAX_BYTES:
        return None
    try:
        data = await att.read(use_cached=True)
    except Exception:
        return None
    return _build_data_url_from_image_bytes(data)


async def _attachment_video_to_data_urls(att: discord.Attachment) -> list[str]:
    if not VideoFileClip:
        return []
    if att.size and att.size > AI_MEDIA_MAX_BYTES:
        return []

    temp_path = ""
    try:
        suffix = os.path.splitext(att.filename or "video.mp4")[1] or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            data = await att.read(use_cached=True)
            temp_file.write(data)

        previews: list[str] = []
        with VideoFileClip(temp_path) as clip:
            if not clip.duration or clip.duration <= 0:
                return []

            sample_duration = min(float(clip.duration), 6.0)
            if AI_VIDEO_FRAME_COUNT == 1:
                timestamps = [min(sample_duration / 2, sample_duration)]
            else:
                step = sample_duration / (AI_VIDEO_FRAME_COUNT + 1)
                timestamps = [step * (idx + 1) for idx in range(AI_VIDEO_FRAME_COUNT)]

            for timestamp in timestamps:
                frame = clip.get_frame(max(0.0, min(timestamp, max(sample_duration - 0.05, 0.0))))
                data_url = _build_data_url_from_frame(frame)
                if data_url:
                    previews.append(data_url)
        return previews
    except Exception:
        return []
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


async def check_google_safe_browsing(session: aiohttp.ClientSession, url: str) -> bool:
    if not GOOGLE_SAFEBROWSING_KEY:
        return False

    endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={GOOGLE_SAFEBROWSING_KEY}"
    payload = {
        "client": {"clientId": "discord-bot", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}],
        },
    }
    try:
        async with session.post(endpoint, json=payload, timeout=10) as response:
            if response.status != 200:
                return False
            payload = await response.json()
            return "matches" in payload and bool(payload["matches"])
    except Exception as exc:
        print(f"GSB error for {url}: {exc}")
        return False


async def check_virustotal(session: aiohttp.ClientSession, url: str) -> bool:
    if not VIRUSTOTAL_KEY:
        return False

    headers = {
        "x-apikey": VIRUSTOTAL_KEY,
        "User-Agent": "PSC-HELPER/2026.04",
    }
    try:
        async with session.post(
            "https://www.virustotal.com/api/v3/urls",
            data={"url": url},
            headers=headers,
            timeout=15,
        ) as response:
            if response.status not in (200, 201):
                return False
            payload = await response.json()
            url_id = payload.get("data", {}).get("id")
            if not url_id:
                return False

        async with session.get(
            f"https://www.virustotal.com/api/v3/analyses/{url_id}",
            headers=headers,
            timeout=15,
        ) as response:
            if response.status != 200:
                return False
            payload = await response.json()
            stats = payload.get("data", {}).get("attributes", {}).get("stats", {})
            return (stats.get("malicious", 0) + stats.get("suspicious", 0)) > 0
    except Exception as exc:
        print(f"Ошибка VirusTotal: {exc}")
        return False


async def _classify_urls_with_ai(url_contexts: list[dict]) -> list[tuple[str, str]]:
    if not POS_AI_API_KEY or not url_contexts or ai_is_temporarily_unavailable():
        return []

    now = time.time()
    blocked: list[tuple[str, str]] = []
    pending: list[dict] = []
    for item in url_contexts:
        cached = _ai_url_cache.get(item["url"])
        if cached and now - cached[2] < AI_URL_CACHE_TTL:
            label, reason, _ = cached
            if label == "block":
                blocked.append((item["url"], reason or "ai"))
            continue
        pending.append(item)

    if not pending:
        return blocked

    system_prompt = (
        "Ты классификатор автомодерации Discord. "
        "Нужно определить, какие URL реально стоит блокировать. "
        "Блокируй только явные случаи: скам, фишинг, казино, NSFW, вредоносные ссылки, агрессивная реклама. "
        "Надёжные медиадомены и CDN вроде tenor.com, giphy.com, discord CDN нельзя блокировать просто из-за слов в URL. "
        "Если нет явной уверенности — ставь allow. Верни только JSON."
    )
    user_payload = {
        "policy": "Если сомневаешься, не блокируй.",
        "urls": pending,
        "response_schema": {
            "results": [
                {
                    "url": "string",
                    "label": "allow|block",
                    "reason": "string",
                    "confidence": 0.0,
                }
            ]
        },
    }
    response = await pos_chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        max_tokens=450,
        temperature=0.1,
        top_p=0.9,
        timeout=45,
    )
    parsed = extract_json_block(response or "")
    if not parsed:
        return blocked

    results = parsed.get("results")
    if not isinstance(results, list):
        return blocked

    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        if not url:
            continue
        label = str(result.get("label") or "allow").strip().lower()
        reason = str(result.get("reason") or "").strip()
        confidence = float(result.get("confidence") or 0)
        if label not in {"allow", "block"}:
            label = "allow"
        _ai_url_cache[url] = (label, reason, time.time())
        if label == "block" and confidence >= 0.75:
            blocked.append((url, reason or "ai-url"))
    return blocked


def _detect_attachment_metadata_flags(attachments: list[discord.Attachment]) -> list[tuple[str, str]]:
    flags: list[tuple[str, str]] = []
    for attachment in attachments:
        name = (attachment.filename or "").lower()
        content_type = (attachment.content_type or "").lower()
        if any(keyword in name for keyword in NSFW_FILENAME_KEYWORDS):
            flags.append((attachment.filename, f"NSFW по имени файла: {attachment.filename}"))
        if any(keyword in name for keyword in AD_FILENAME_KEYWORDS):
            flags.append((attachment.filename, f"подозрительное имя файла: {attachment.filename}"))
        if content_type.startswith("video/") and any(keyword in name for keyword in ["casino", "porn", "nitro", "robux"]):
            flags.append((attachment.filename, f"подозрительное видео по имени файла: {attachment.filename}"))
    return flags


async def _classify_media_with_ai(attachments: list[discord.Attachment], text: str = "") -> dict[str, tuple[str, str]]:
    if not POS_AI_API_KEY or ai_is_temporarily_unavailable():
        return {}

    media = [attachment for attachment in attachments if _is_image_attachment(attachment) or _is_video_attachment(attachment)]
    media = media[:AI_MEDIA_MAX_ATTACHMENTS]
    if not media:
        return {}

    content_items: list[dict] = [
        {
            "type": "text",
            "text": (
                "Проверь вложения для Discord-модерации. "
                "Блокировать только явные случаи NSFW, порнографии, казино, скама, фишинга или откровенно рекламного мусора. "
                "Обычные мемы, GIF, скриншоты, арты и нейтральные видео не блокируй. "
                "Если уверенность низкая — allow. "
                f"Текст сообщения: {text[:600] or '(без текста)'}\n"
                "Верни только JSON формата "
                "{\"results\":[{\"file\":\"name\",\"label\":\"allow|block\",\"reason\":\"...\",\"confidence\":0.0}]}"
            ),
        }
    ]
    processed_files: list[str] = []

    for attachment in media:
        if _is_image_attachment(attachment):
            data_url = await _attachment_image_to_data_url(attachment)
            if not data_url:
                continue
            content_items.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )
            content_items.append(
                {
                    "type": "text",
                    "text": f"Файл: {attachment.filename} | тип: {attachment.content_type or 'unknown'} | размер: {attachment.size or 0}",
                }
            )
            processed_files.append(attachment.filename)
            continue

        if _is_video_attachment(attachment):
            frame_urls = await _attachment_video_to_data_urls(attachment)
            if not frame_urls:
                continue
            for frame_url in frame_urls:
                content_items.append({"type": "image_url", "image_url": {"url": frame_url}})
            content_items.append(
                {
                    "type": "text",
                    "text": f"Видео: {attachment.filename} | тип: {attachment.content_type or 'unknown'} | размер: {attachment.size or 0}",
                }
            )
            processed_files.append(attachment.filename)

    if not processed_files:
        return {}

    response = await pos_chat_completion(
        [
            {
                "role": "system",
                "content": "Ты точный модератор медиаконтента. Отвечай только JSON и не выдумывай нарушения, если они неочевидны.",
            },
            {"role": "user", "content": content_items},
        ],
        max_tokens=500,
        temperature=0.1,
        top_p=0.9,
        timeout=60,
    )
    parsed = extract_json_block(response or "")
    if not parsed:
        return {}

    results = parsed.get("results")
    if not isinstance(results, list):
        return {}

    verdicts: dict[str, tuple[str, str]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        file_name = str(result.get("file") or "").strip()
        if not file_name:
            continue
        label = str(result.get("label") or "allow").strip().lower()
        reason = str(result.get("reason") or "").strip()
        confidence = float(result.get("confidence") or 0)
        if label == "block" and confidence >= 0.72:
            verdicts[file_name] = ("block", reason or "ai-media")
        else:
            verdicts[file_name] = ("allow", reason)
    return verdicts


async def check_and_handle_urls(message: discord.Message) -> bool:
    urls = extract_urls(message.content or "")
    if not urls:
        return False

    suspicious: list[tuple[str, str]] = []
    borderline: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                parsed = urlparse(url if url.startswith("http") else "http://" + url)
                domain = (parsed.hostname or parsed.netloc or "").lower()
                path = (parsed.path or "") + (f"?{parsed.query}" if parsed.query else "")
                path_decoded = unquote(path).lower()
            except Exception:
                continue

            if _domain_matches_whitelist(domain):
                continue

            if _domain_matches_blacklist(domain):
                suspicious.append((url, "local-domain/keyword"))
                continue

            path_keywords = _extract_path_keywords(path_decoded)
            if any(marker in path_decoded for marker in HIGH_CONFIDENCE_PATH_MARKERS):
                suspicious.append((url, "high-confidence-path"))
                continue
            if len(path_keywords) >= 2:
                borderline.append(
                    {
                        "url": url,
                        "domain": _normalize_domain(domain),
                        "path_keywords": path_keywords,
                        "path": path_decoded[:300],
                    }
                )

            cached = _vt_cache.get(url)
            if cached and (time.time() - cached[1]) < _VT_CACHE_TTL:
                if cached[0]:
                    suspicious.append((url, "vt-cache"))
                continue

            try:
                if await check_google_safe_browsing(session, url):
                    suspicious.append((url, "GoogleSafeBrowsing"))
                    _vt_cache[url] = (True, time.time())
                    continue
            except Exception as exc:
                print(f"GSB check error: {exc}")

            try:
                vt_bad = await check_virustotal(session, url)
                _vt_cache[url] = (bool(vt_bad), time.time())
                if vt_bad:
                    suspicious.append((url, "VirusTotal"))
            except Exception as exc:
                print(f"VirusTotal check error: {exc}")

    ai_suspicious = await _classify_urls_with_ai(borderline)
    if ai_suspicious:
        suspicious.extend(ai_suspicious)

    if not suspicious:
        return False

    reason_text = "\n".join(f"{url} -> {why}" for url, why in suspicious)
    reasons = [f"Подозрительная ссылка: {url} ({why})" for url, why in suspicious]

    try:
        await message.delete()
    except Exception:
        pass

    await apply_max_timeout(message.author, "Автомодерация: опасные ссылки")
    await log_violation_with_evidence(message, "🚨 Опасная ссылка", reasons)

    try:
        dm = Embed(
            title="⚠️ Ссылка заблокирована",
            description="Сообщение удалено: ссылка выглядит опасной или слишком подозрительной. Выдано ограничение голоса на 24 часа.",
            color=Color.red(),
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
        except Exception as exc:
            print(f"Не удалось выдать ограничение голоса: {exc}")
            return False
    except Exception as exc:
        print(f"Не удалось выдать ограничение голоса: {exc}")
        return False


async def log_violation_with_evidence(message: discord.Message, title: str, reasons: list[str]):
    if not message.guild:
        return

    embed = Embed(title=title, color=Color.red(), timestamp=discord.utils.utcnow())
    embed.set_author(name="P.S.C Logs • Модерация")
    embed.set_footer(text="Модерация")
    embed.add_field(name="Пользователь", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
    embed.add_field(name="Канал", value=message.channel.mention, inline=False)
    embed.add_field(name="Причины", value="\n".join(f"• {reason}" for reason in reasons)[:1024], inline=False)
    embed.add_field(name="Контент", value=(message.content or "(без текста)")[:1000], inline=False)

    if message.attachments:
        links = "\n".join(attachment.url for attachment in message.attachments[:5])
        embed.add_field(name="Вложения (URL)", value=links[:1024], inline=False)

    files = []
    for attachment in message.attachments[:VIOLATION_ATTACHMENT_LIMIT]:
        try:
            files.append(await attachment.to_file())
        except Exception:
            pass

    try:
        channel = get_log_channel(message.guild, "moderation")
        if not channel:
            return
        kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if files:
            kwargs["files"] = files
        await channel.send(**kwargs)
    except Exception as exc:
        print(f"Ошибка логирования нарушения: {exc}")


def detect_advertising_or_scam_text(text: str):
    lowered = (text or "").lower()
    reasons = []

    has_discord_invite = any(pattern in lowered for pattern in ALLOWED_DISCORD_INVITE_PATTERNS)

    if any(pattern in lowered for pattern in SUSPICIOUS_INVITE_PATTERNS):
        reasons.append("инвайт/внешняя ссылка для рекламы")

    if any(keyword in lowered for keyword in AD_TEXT_KEYWORDS):
        reasons.append("рекламные/скам ключевые слова")

    if has_discord_invite and reasons == ["инвайт/внешняя ссылка для рекламы"]:
        return []
    return reasons


async def detect_attachment_violations(attachments, text: str = "", user_id: int = 0):
    attachment_list = list(attachments)
    metadata_flags = _detect_attachment_metadata_flags(attachment_list)
    has_video = any(_is_video_attachment(attachment) for attachment in attachment_list)
    should_run_ai_review = bool(metadata_flags) or _text_has_media_risk_signals(text) or has_video
    # Per-user rate limit: если пользователь недавно уже проходил AI-проверку — пропускаем
    if should_run_ai_review and user_id:
        _now = time.time()
        _last = _AI_MEDIA_USER_LAST_CHECK.get(user_id, 0.0)
        if _now - _last < AI_MEDIA_USER_COOLDOWN_SECONDS:
            should_run_ai_review = False
        else:
            _AI_MEDIA_USER_LAST_CHECK[user_id] = _now
    ai_verdicts = await _classify_media_with_ai(attachment_list, text=text) if should_run_ai_review else {}

    reasons: list[str] = []
    already_added = set()

    for file_name, reason in metadata_flags:
        verdict = ai_verdicts.get(file_name)
        if verdict and verdict[0] == "allow":
            continue
        final_reason = verdict[1] if verdict and verdict[0] == "block" and verdict[1] else reason
        if final_reason not in already_added:
            reasons.append(final_reason)
            already_added.add(final_reason)

    for file_name, verdict in ai_verdicts.items():
        if verdict[0] != "block":
            continue
        final_reason = f"{file_name}: {verdict[1] or 'подозрительный медиаконтент'}"
        if final_reason not in already_added:
            reasons.append(final_reason)
            already_added.add(final_reason)

    return reasons


def message_key_for_spam(message: discord.Message):
    text = (message.content or "").strip()
    attachment_summary = [attachment.filename for attachment in message.attachments]
    return f"{text}||{'|'.join(attachment_summary)}"


async def handle_spam_if_needed(message: discord.Message):
    user_id = message.author.id
    key = message_key_for_spam(message)
    now = time.time()
    queue = recent_messages[user_id]
    queue.append((key, now, message.id, message.channel.id))

    while queue and now - queue[0][1] > SPAM_WINDOW_SECONDS:
        queue.popleft()

    count = sum(1 for entry_key, _, _, _ in queue if entry_key == key)
    if count < SPAM_DUPLICATES_THRESHOLD:
        return False

    to_delete = [message_id for entry_key, _, message_id, channel_id in queue if entry_key == key and channel_id == message.channel.id]
    for message_id in to_delete:
        try:
            duplicate = await message.channel.fetch_message(message_id)
            if duplicate:
                await duplicate.delete()
        except Exception:
            pass

    reasons = [f"Спам одинаковыми сообщениями: {count} шт. за {SPAM_WINDOW_SECONDS} сек."]
    await apply_max_timeout(message.author, "Автомодерация: спам")
    await log_violation_with_evidence(message, "🚨 STOPREID: спам", reasons)

    try:
        dm = Embed(
            title="🚫 Обнаружен спам",
            description="Вы слишком бодро заспамили одинаковыми сообщениями. Выдано ограничение голоса на 24 часа.",
            color=Color.orange(),
        )
        dm.add_field(name="Детали", value=reasons[0], inline=False)
        await message.author.send(embed=dm)
    except Exception:
        pass

    recent_messages[user_id].clear()
    return True

