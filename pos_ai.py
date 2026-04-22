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
)
from logging_utils import is_log_channel

AI_COOLDOWN_SECONDS = 2.5
AI_MAX_CONTEXT = 12
AI_MAX_CONTEXT_THREAD = 20
AI_MAX_RESPONSE_CHARS = 1900
AI_THREAD_TTL_SECONDS = 20 * 60
AI_CHANNEL_TTL_SECONDS = 8 * 60

SYSTEM_INSTRUCTION = POS_AI_SYSTEM_PROMPT

_last_user_call: dict[int, float] = {}
_conversation_state: dict[int, dict] = {}
_last_rate_limit_notice: dict[int, float] = {}
_missing_key_warned = False
AI_NAME_PATTERN = re.compile(r"(?<!\w)(?:p[\s.\-_]*o[\s.\-_]*s|п[\s.\-_]*о[\s.\-_]*с)(?!\w)", re.IGNORECASE)


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
    if ai_unavailable_reason() == "rate_limited":
        return (
            f"Сейчас я упёрся в лимит GitHub Models. Дай мне около {seconds} сек., "
            "и я снова включусь в разговор без истерик и белого шума."
        )
    return "Сейчас внешний AI-сервис подзадумался. Через минуту попробуй ещё раз — я вернусь в строй."


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
    if mentioned or replied or named:
        return True

    state = _get_state(message.channel)
    if not state:
        return False

    ttl = AI_THREAD_TTL_SECONDS if state.get("is_thread") else AI_CHANNEL_TTL_SECONDS
    if not state.get("last_bot_ts") or time.time() - state["last_bot_ts"] > ttl:
        return False

    if state.get("is_thread"):
        return True

    return message.author.id in state.get("participants", set())


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
    messages: list[dict] = [{"role": role, "content": SYSTEM_INSTRUCTION + "\nОтвечай только на последний запрос."}]
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
            history.append({"role": role, "content": content})
            seen_ids.add(ref_msg.id)

    async for m in message.channel.history(limit=60, before=message):
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


async def handle_pos_ai(message: discord.Message, bot: discord.Client) -> bool:
    global _missing_key_warned

    if _should_skip_message(message, bot):
        return False

    ref_msg = await _get_reference_message(message)
    if not _can_auto_reply(message, bot, ref_msg):
        return False
    explicit_addressing = _is_addressed_to_bot(message, bot, ref_msg)

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
