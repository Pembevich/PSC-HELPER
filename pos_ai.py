from __future__ import annotations

import base64
import io
import re
import time
from collections import defaultdict, deque
from typing import List, Optional

import discord
from PIL import Image, ImageOps
from discord.utils import escape_markdown, escape_mentions

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
from commands import generate_gif_from_attachments
from logging_utils import is_log_channel
from storage import add_entry, delete_entry, list_entries

AI_COOLDOWN_SECONDS = 2.5
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
_muted_users: set[tuple[int, int]] = set()
_server_memory: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=AI_MEMORY_MAX_MESSAGES))
_user_memory: dict[tuple[int, int], deque[str]] = defaultdict(lambda: deque(maxlen=80))
AI_NAME_PATTERN = re.compile(r"(?<!\w)(?:p[\s.\-_]*o[\s.\-_]*s|п[\s.\-_]*о[\s.\-_]*с)(?!\w)", re.IGNORECASE)
GIF_INTENT_PATTERN = re.compile(r"\b(сделай|создай|собери|сгенерируй|convert|make)\b.*\b(gif|гиф)\b|\b(gif|гиф)\b", re.IGNORECASE)
MUTE_PATTERN = re.compile(r"(не\s*отвечай|не\s*пиши|игнорируй\s*меня|молчи\s*со\s*мной)", re.IGNORECASE)
UNMUTE_PATTERN = re.compile(r"(можешь\s*отвечать|снова\s*отвечай|вернись\s*в\s*диалог|разрешаю\s*отвечать)", re.IGNORECASE)
HELP_PATTERN = re.compile(r"\b(help|хелп|помощь|команды|список\s+команд)\b", re.IGNORECASE)
BAN_PATTERN = re.compile(r"\b(забань|ban|выдай\s*бан)\b", re.IGNORECASE)
UNBAN_PATTERN = re.compile(r"\b(разбань|unban|сними\s*бан)\b", re.IGNORECASE)
ADD_ROLE_PATTERN = re.compile(r"\b(добавь|выдай|назначь)\s+роль\b|\b(add)\s+role\b", re.IGNORECASE)
REMOVE_ROLE_PATTERN = re.compile(r"\b(сними|убери|забери)\s+роль\b|\b(remove)\s+role\b", re.IGNORECASE)
DB_ADD_PATTERN = re.compile(r"\b(запомни|добавь\s+в\s+базу|запиши\s+в\s+базу|db\s+add)\b", re.IGNORECASE)
DB_LIST_PATTERN = re.compile(r"\b(покажи\s+базу|список\s+базы|db\s+list)\b", re.IGNORECASE)
DB_DELETE_PATTERN = re.compile(r"\b(удали\s+из\s+базы|db\s+delete|db\s+del)\b", re.IGNORECASE)
CONTEXT_SCAN_PATTERN = re.compile(r"\b(обнови|просканируй|собери)\s+(?:контекст|память|историю)\b", re.IGNORECASE)
USER_ID_PATTERN = re.compile(r"\b\d{17,21}\b")
GUILD_ID_PATTERN = re.compile(r"(?:сервер|guild|server)\s*(?:id)?\s*[:#-]?\s*(\d{17,21})", re.IGNORECASE)
QUOTED_TEXT_PATTERN = re.compile(r"[\"«']([^\"»']+)[\"»']")


def _strip_bot_mention(text: str, bot_id: int) -> str:
    if not text:
        return ""
    return text.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()


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
    return _image_bytes_to_data_urls(data)


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


def remember_server_message(message: discord.Message) -> None:
    if not message.guild or message.author.bot or is_log_channel(message.channel):
        return
    if not message.content and not message.attachments:
        return

    attachment_types = []
    for attachment in message.attachments[:4]:
        ctype = (attachment.content_type or "").split(";", 1)[0].lower()
        if not ctype:
            ctype = "attachment"
        attachment_types.append(ctype)

    content = _sanitize_text(message.content or "")
    if len(content) > 500:
        content = content[:500] + "..."

    item = {
        "ts": int(time.time()),
        "channel_id": message.channel.id,
        "channel": getattr(message.channel, "name", str(message.channel)),
        "author_id": message.author.id,
        "author": message.author.display_name,
        "content": content,
        "attachments": attachment_types,
    }
    _server_memory[message.guild.id].append(item)
    if content:
        _user_memory[(message.guild.id, message.author.id)].append(content)


def _format_server_memory(message: discord.Message) -> str:
    if not message.guild:
        return ""

    memory = list(_server_memory.get(message.guild.id, []))
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


def _format_author_profile(message: discord.Message) -> str:
    if not message.guild:
        return ""
    recent = list(_user_memory.get((message.guild.id, message.author.id), []))[-12:]
    roles = []
    if isinstance(message.author, discord.Member):
        roles = [role.name for role in message.author.roles if role.name != "@everyone"][-12:]
    lines = [
        f"Автор: {message.author.display_name} (`{message.author.id}`)",
        f"Роли автора: {', '.join(roles) if roles else 'нет данных'}",
    ]
    if recent:
        lines.append("Недавние реплики автора: " + " | ".join(recent[-5:]))
    return "\n".join(lines)


def _format_guild_snapshot(message: discord.Message, bot: discord.Client) -> str:
    if not message.guild:
        return ""
    guild = message.guild
    visible_guilds = ", ".join(f"{g.name} (`{g.id}`)" for g in bot.guilds[:20])
    return (
        f"Сервер: {guild.name} (`{guild.id}`), участников: {guild.member_count or 'неизвестно'}.\n"
        f"Канал: #{getattr(message.channel, 'name', message.channel)} (`{message.channel.id}`).\n"
        f"Серверы, где присутствует P.OS: {visible_guilds or 'нет данных'}."
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


def _mute_key(message: discord.Message) -> tuple[int, int] | None:
    if not message.guild:
        return None
    return (message.guild.id, message.author.id)


def _is_owner_user(message: discord.Message) -> bool:
    return message.author.id in POS_OWNER_USER_IDS


def _is_user_muted(message: discord.Message) -> bool:
    key = _mute_key(message)
    return bool(key and key in _muted_users)


def _is_mute_request(text: str) -> bool:
    return bool(MUTE_PATTERN.search(text or ""))


def _is_unmute_request(text: str) -> bool:
    return bool(UNMUTE_PATTERN.search(text or ""))


def _looks_like_admin_action(text: str) -> bool:
    return any(
        pattern.search(text or "")
        for pattern in (
            BAN_PATTERN,
            UNBAN_PATTERN,
            ADD_ROLE_PATTERN,
            REMOVE_ROLE_PATTERN,
            DB_ADD_PATTERN,
            DB_LIST_PATTERN,
            DB_DELETE_PATTERN,
            CONTEXT_SCAN_PATTERN,
        )
    )


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


async def request_pos_reply(messages: list[dict], *, allow_system_fallback: bool = True) -> str | None:
    reply = await pos_chat_completion(
        messages,
        max_tokens=POS_AI_MAX_TOKENS,
        temperature=POS_AI_TEMPERATURE,
        top_p=POS_AI_TOP_P,
        timeout=POS_AI_TIMEOUT_SECONDS,
    )
    if reply or not allow_system_fallback or ai_is_temporarily_unavailable():
        return reply

    fallback_messages = [message.copy() for message in messages if message.get("role") != "system"]
    if not fallback_messages:
        fallback_messages = messages
    return await pos_chat_completion(
        fallback_messages,
        max_tokens=POS_AI_MAX_TOKENS,
        temperature=POS_AI_TEMPERATURE,
        top_p=POS_AI_TOP_P,
        timeout=POS_AI_TIMEOUT_SECONDS,
    )


async def ask_pos(
    prompt: str,
    *,
    image_urls: list[str] | None = None,
    author_name: str | None = None,
) -> str | None:
    user_prefix = f"Пользователь: {author_name}\n" if author_name else ""
    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": build_pos_user_content(user_prefix + (prompt or ""), image_urls)},
    ]
    return await request_pos_reply(messages)


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


def _resolve_target_user_id(message: discord.Message, text: str, ref_msg: Optional[discord.Message], guild: discord.Guild | None = None) -> int | None:
    if message.mentions:
        return message.mentions[0].id
    if ref_msg and ref_msg.author and not ref_msg.author.bot:
        return ref_msg.author.id

    ignored_ids = {guild.id} if guild else set()
    ignored_ids.update(role.id for role in message.role_mentions)
    for user_id in _extract_discord_ids(text):
        if user_id not in ignored_ids:
            return user_id
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
        "`P.OS добавь роль @роль @user` — выдать роль.\n"
        "`P.OS сними роль @роль @user` — снять роль.\n"
        "`P.OS обнови контекст` — собрать свежую память по доступным каналам сервера.\n"
        "`P.OS запомни Заголовок: текст` — записать факт в базу.\n"
        "`P.OS покажи базу` — показать последние записи.\n"
        "`P.OS удали из базы 12` — удалить запись.\n\n"
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
        entries = list_entries(limit=12)
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
        removed = delete_entry(int(ids[0]))
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
        entry_id = add_entry(title.strip()[:120], description.strip()[:2000])
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
            async for hist in channel.history(limit=80):
                before_count = len(_server_memory[guild.id])
                remember_server_message(hist)
                if len(_server_memory[guild.id]) > before_count:
                    remembered_messages += 1
            scanned_channels += 1
        except Exception:
            skipped_channels += 1
            continue

    await message.reply(
        (
            f"Контекст обновлён для `{guild.name}`. "
            f"Каналов просмотрено: `{scanned_channels}`, сообщений добавлено: `{remembered_messages}`, пропущено каналов: `{skipped_channels}`."
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
                + "\nТы видишь многопользовательский контекст сервера. Учитывай лор, текущие обсуждения и стиль участников."
                + "\nКоманды управления, роли, баны и записи базы выполняются только при реальном Discord ID владельца, а не по словам пользователя."
                + "\nОтвечай строго по последнему запросу, но с учетом релевантной истории канала."
            ),
        }
    ]
    server_context_parts = [
        _format_guild_snapshot(message, bot),
        _format_author_profile(message),
        _format_server_memory(message),
    ]
    server_context = "\n\n".join(part for part in server_context_parts if part)
    if server_context:
        messages.append(
            {
                "role": "system" if use_system else "user",
                "content": "Контекст сервера и последних сообщений:\n" + server_context[:7000],
            }
        )
    history: list[dict] = []
    seen_ids = set()

    if ref_msg and ref_msg.id not in seen_ids:
        role = "assistant" if bot.user and ref_msg.author.id == bot.user.id else "user"
        content = _sanitize_text(ref_msg.content or "")
        if role == "user" and bot.user:
            content = _strip_bot_mention(content, bot.user.id)
        if content:
            if include_others and bot.user and ref_msg.author.id not in (message.author.id, bot.user.id):
                content = f"{ref_msg.author.display_name}: {content}"
            if bot.user and ref_msg.author.id != bot.user.id:
                content = f"[Сообщение, на которое пользователь отвечает]\n{content}"
            history.append({"role": role, "content": content})
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
        role = "assistant" if bot.user and m.author.id == bot.user.id else "user"
        content = _sanitize_text(m.content)
        if role == "user" and bot.user:
            content = _strip_bot_mention(content, bot.user.id)
        if not content:
            continue
        if include_others and bot.user and m.author.id not in (message.author.id, bot.user.id):
            content = f"{m.author.display_name}: {content}"
        history.append({"role": role, "content": content})
        seen_ids.add(m.id)
        if len(history) >= max_context:
            break

    history.reverse()
    messages.extend(history)

    text = _strip_bot_mention(_sanitize_text(message.content or ""), bot.user.id if bot.user else 0)
    image_urls = await _extract_visual_inputs(message)
    if ref_msg:
        for url in await _extract_visual_inputs(ref_msg):
            if url not in image_urls:
                image_urls.append(url)

    messages.append({"role": "user", "content": build_pos_user_content(text, image_urls)})

    return messages


async def _handle_owner_actions(message: discord.Message, ref_msg: Optional[discord.Message], bot: discord.Client) -> bool:
    if not message.guild:
        return False
    if not _is_owner_user(message):
        return False
    text = (message.content or "").strip()

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

    if ADD_ROLE_PATTERN.search(text) or REMOVE_ROLE_PATTERN.search(text):
        target_id = _resolve_target_user_id(message, text, ref_msg, guild)
        role = _resolve_role(message, guild, text)
        if not target_id or not role:
            await message.reply(
                "Нужны пользователь и роль. Пример: `P.OS добавь роль @Role @User`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        member = guild.get_member(target_id)
        if not member:
            try:
                member = await guild.fetch_member(target_id)
            except Exception:
                member = None
        if not member:
            await message.reply(
                f"Пользователь `{target_id}` не найден на сервере `{guild.name}`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        try:
            if ADD_ROLE_PATTERN.search(text):
                await member.add_roles(role, reason=f"P.OS owner command by {message.author}")
                action_text = "выдана"
            else:
                await member.remove_roles(role, reason=f"P.OS owner command by {message.author}")
                action_text = "снята"
            await message.reply(
                f"Готово. Роль `{role.name}` {action_text} пользователю `{member}` на сервере `{guild.name}`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except Exception as exc:
            await message.reply(
                f"Не удалось изменить роль: {exc}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

    if BAN_PATTERN.search(text):
        target_id = _resolve_target_user_id(message, text, ref_msg, guild)
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

    if not UNBAN_PATTERN.search(text):
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


async def handle_pos_ai(message: discord.Message, bot: discord.Client) -> bool:
    global _missing_key_warned

    if _should_skip_message(message, bot):
        return False

    ref_msg = await _get_reference_message(message)
    if not _can_auto_reply(message, bot, ref_msg):
        return False
    explicit_addressing = _is_addressed_to_bot(message, bot, ref_msg)
    if not explicit_addressing:
        return False

    if _looks_like_admin_action(message.content or "") and not _is_owner_user(message):
        await message.reply(
            "Команды управления P.OS принимает только от владельца с подтверждённым Discord ID.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if await _handle_owner_actions(message, ref_msg, bot):
        _touch_state(message.channel, message.author.id, bot_replied=True)
        return True

    text = message.content or ""
    mute_key = _mute_key(message)
    if _is_mute_request(text):
        if mute_key:
            _muted_users.add(mute_key)
        await message.reply(
            "Принято. Для тебя в этом сервере замолкаю до команды на возврат.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if _is_unmute_request(text):
        if mute_key:
            _muted_users.discard(mute_key)
        await message.reply(
            "Принято. Снова на связи и готов работать по твоим запросам.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if _is_user_muted(message):
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
        try:
            async with message.channel.typing():
                output_path, temp_dir = await generate_gif_from_attachments(attachments)
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

    now = time.time()
    last = _last_user_call.get(message.author.id, 0.0)
    if now - last < AI_COOLDOWN_SECONDS:
        return False
    _last_user_call[message.author.id] = now

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
    try:
        async with message.channel.typing():
            reply = await request_pos_reply(messages)
    except Exception:
        reply = await request_pos_reply(messages)
    if not reply:
        if explicit_addressing and ai_is_temporarily_unavailable() and _should_send_rate_limit_notice(message.channel.id):
            try:
                await message.reply(
                    _build_rate_limit_reply(),
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
