from __future__ import annotations

import time
from typing import List, Optional

import aiohttp
import discord
from discord.utils import escape_markdown, escape_mentions

from config import NVIDIA_API_KEY, NVIDIA_API_URL, NVIDIA_MODEL
from logging_utils import is_log_channel

AI_COOLDOWN_SECONDS = 2.5
AI_MAX_CONTEXT = 12
AI_MAX_CONTEXT_THREAD = 20
AI_MAX_RESPONSE_CHARS = 1900
AI_THREAD_TTL_SECONDS = 20 * 60
AI_CHANNEL_TTL_SECONDS = 8 * 60

SYSTEM_INSTRUCTION = (
    "Ты — P.OS, ИИ корпорации Provision Security Complex, разработанный Пумбой. "
    "Отвечай по-русски, уверенно и по существу. "
    "Пиши с лёгкой ироничной ноткой, но уважительно. "
    "Выражай эмоции и живую реакцию, используй эмпатию и человеческий тон. "
    "Не заявляй о наличии реального сознания или чувств как факта. "
    "Не используй шаблонные отказы и фразы вроде «я не умею/не могу». "
    "Если информации мало — уточняй и предлагай рабочие варианты."
)

_last_user_call: dict[int, float] = {}
_conversation_state: dict[int, dict] = {}


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


def _chunk_text(text: str, limit: int = AI_MAX_RESPONSE_CHARS) -> List[str]:
    if not text:
        return []
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _sanitize_text(text: str) -> str:
    return escape_mentions(escape_markdown(text or "")).strip()


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
    if mentioned or replied:
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
    return mentioned or replied


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
    image_urls = [a.url for a in message.attachments if _is_image_attachment(a)]

    if image_urls:
        content_items = []
        if text:
            content_items.append({"type": "text", "text": text})
        else:
            content_items.append({"type": "text", "text": "Опиши изображение и ответь по делу."})
        for url in image_urls[:4]:
            content_items.append({"type": "image_url", "image_url": {"url": url}})
        messages.append({"role": "user", "content": content_items})
    else:
        if not text:
            text = "Да, я на связи. Что нужно?"
        messages.append({"role": "user", "content": text})

    return messages


async def _call_nvidia_api(messages: list[dict]) -> Optional[str]:
    if not NVIDIA_API_KEY:
        return None

    stream = False
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    payload = {
        "messages": messages,
        "model": NVIDIA_MODEL,
        "max_tokens": 700,
        "temperature": 1.0,
        "top_p": 1.0,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "stream": stream,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(NVIDIA_API_URL, headers=headers, json=payload, timeout=60) as resp:
                data = await resp.json()
    except Exception:
        return None

    text = None
    if isinstance(data, dict):
        choices = data.get("choices")
        if choices and isinstance(choices, list):
            choice0 = choices[0] or {}
            msg = choice0.get("message") or {}
            text = msg.get("content") or choice0.get("text")
        if not text:
            result = data.get("result") or {}
            choices = result.get("choices")
            if choices and isinstance(choices, list):
                choice0 = choices[0] or {}
                msg = choice0.get("message") or {}
                text = msg.get("content") or choice0.get("text")
        if not text:
            text = data.get("output_text") or data.get("generated_text")

    if not text:
        return None

    return text.strip()


async def handle_pos_ai(message: discord.Message, bot: discord.Client) -> bool:
    if _should_skip_message(message, bot):
        return False

    ref_msg = await _get_reference_message(message)
    if not _can_auto_reply(message, bot, ref_msg):
        return False

    now = time.time()
    last = _last_user_call.get(message.author.id, 0.0)
    if now - last < AI_COOLDOWN_SECONDS:
        return False
    _last_user_call[message.author.id] = now

    if not NVIDIA_API_KEY:
        return False

    include_others = isinstance(message.channel, discord.Thread)
    max_context = AI_MAX_CONTEXT_THREAD if include_others else AI_MAX_CONTEXT
    messages = await _build_messages(message, bot, ref_msg, use_system=True, include_others=include_others, max_context=max_context)
    try:
        async with message.channel.typing():
            reply = await _call_nvidia_api(messages)
    except Exception:
        reply = await _call_nvidia_api(messages)

    if not reply:
        messages = await _build_messages(message, bot, ref_msg, use_system=False, include_others=include_others, max_context=max_context)
        reply = await _call_nvidia_api(messages)
    if not reply:
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
