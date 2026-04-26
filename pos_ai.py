from __future__ import annotations

import re
import time
from typing import List, Optional

import discord
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
    POS_OWNER_FALLBACK_NAME,
    POS_OWNER_USER_IDS,
)
from commands import generate_gif_from_attachments
from logging_utils import is_log_channel

AI_COOLDOWN_SECONDS = 2.5
AI_MAX_CONTEXT = 64
AI_MAX_CONTEXT_THREAD = 140
AI_MAX_RESPONSE_CHARS = 1900
AI_THREAD_TTL_SECONDS = 20 * 60
AI_CHANNEL_TTL_SECONDS = 8 * 60
AI_HISTORY_SCAN_LIMIT = 450

SYSTEM_INSTRUCTION = POS_AI_SYSTEM_PROMPT

_last_user_call: dict[int, float] = {}
_conversation_state: dict[int, dict] = {}
_last_rate_limit_notice: dict[int, float] = {}
_missing_key_warned = False
_muted_users: set[tuple[int, int]] = set()
AI_NAME_PATTERN = re.compile(r"(?<!\w)(?:p[\s.\-_]*o[\s.\-_]*s|п[\s.\-_]*о[\s.\-_]*с)(?!\w)", re.IGNORECASE)
GIF_INTENT_PATTERN = re.compile(r"\b(сделай|создай|собери|сгенерируй|convert|make)\b.*\b(gif|гиф)\b|\b(gif|гиф)\b", re.IGNORECASE)
MUTE_PATTERN = re.compile(r"(не\s*отвечай|не\s*пиши|игнорируй\s*меня|молчи\s*со\s*мной)", re.IGNORECASE)
UNMUTE_PATTERN = re.compile(r"(можешь\s*отвечать|снова\s*отвечай|вернись\s*в\s*диалог|разрешаю\s*отвечать)", re.IGNORECASE)
UNBAN_PATTERN = re.compile(r"\b(разбань|unban|сними\s*бан)\b", re.IGNORECASE)
USER_ID_PATTERN = re.compile(r"\b\d{17,21}\b")


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


def _chunk_text(text: str, limit: int = AI_MAX_RESPONSE_CHARS) -> List[str]:
    if not text:
        return []
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _sanitize_text(text: str) -> str:
    return escape_mentions(escape_markdown(text or "")).strip()


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
    if message.author.id in POS_OWNER_USER_IDS:
        return True
    name = (message.author.name or "").strip().lower()
    display = (message.author.display_name or "").strip().lower()
    fallback = POS_OWNER_FALLBACK_NAME
    return bool(fallback and (name == fallback or display == fallback))


def _is_user_muted(message: discord.Message) -> bool:
    key = _mute_key(message)
    return bool(key and key in _muted_users)


def _is_mute_request(text: str) -> bool:
    return bool(MUTE_PATTERN.search(text or ""))


def _is_unmute_request(text: str) -> bool:
    return bool(UNMUTE_PATTERN.search(text or ""))


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
                + "\nОтвечай строго по последнему запросу, но с учетом релевантной истории канала."
            ),
        }
    ]
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
    image_urls = _extract_image_urls(message)
    if ref_msg:
        for url in _extract_image_urls(ref_msg):
            if url not in image_urls:
                image_urls.append(url)

    messages.append({"role": "user", "content": build_pos_user_content(text, image_urls)})

    return messages


async def _handle_owner_actions(message: discord.Message, ref_msg: Optional[discord.Message]) -> bool:
    if not message.guild:
        return False
    if not _is_owner_user(message):
        return False
    text = (message.content or "").strip()
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
        bans = [entry async for entry in message.guild.bans(limit=1000)]
        entry = next((b for b in bans if b.user and b.user.id == target_id), None)
        if not entry:
            await message.reply(
                f"Пользователь `{target_id}` сейчас не найден в бан-листе.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        await message.guild.unban(entry.user, reason=f"P.OS owner command by {message.author}")
        await message.reply(
            f"Выполнено. Пользователь `{target_id}` разбанен.",
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

    if await _handle_owner_actions(message, ref_msg):
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

    include_others = isinstance(message.channel, discord.Thread)
    max_context = AI_MAX_CONTEXT_THREAD if include_others else AI_MAX_CONTEXT
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
