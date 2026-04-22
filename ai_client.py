from __future__ import annotations

import asyncio
import json
import time
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from config import (
    GITHUB_MODELS_API_VERSION,
    POS_AI_API_KEY,
    POS_AI_MAX_CONCURRENT_REQUESTS,
    POS_AI_MAX_TOKENS,
    POS_AI_PROVIDER,
    POS_AI_API_URL,
    POS_AI_RATE_LIMIT_FALLBACK_SECONDS,
    POS_AI_MODEL,
    POS_AI_TIMEOUT_SECONDS,
    POS_AI_TOP_P,
    POS_AI_TEMPERATURE,
)

_AI_REQUEST_SEMAPHORE = asyncio.Semaphore(max(1, POS_AI_MAX_CONCURRENT_REQUESTS))
_ai_backoff_until = 0.0
_ai_backoff_reason = ""
_ai_last_backoff_log_at = 0.0


def ai_cooldown_remaining() -> float:
    remaining = _ai_backoff_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def ai_is_temporarily_unavailable() -> bool:
    return ai_cooldown_remaining() > 0


def ai_unavailable_reason() -> str:
    return _ai_backoff_reason or "temporarily_unavailable"


def _set_ai_backoff(seconds: float, reason: str) -> None:
    global _ai_backoff_until, _ai_backoff_reason
    cooldown = max(float(seconds or 0), 1.0)
    _ai_backoff_until = max(_ai_backoff_until, time.monotonic() + cooldown)
    _ai_backoff_reason = reason


def _parse_retry_after(headers: aiohttp.typedefs.LooseHeaders) -> float | None:
    if not headers:
        return None

    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        try:
            return max(float(retry_after), 1.0)
        except ValueError:
            try:
                retry_dt = parsedate_to_datetime(retry_after)
                return max((retry_dt.timestamp() - time.time()), 1.0)
            except Exception:
                pass

    reset_header = headers.get("x-ratelimit-reset")
    if reset_header:
        try:
            reset_at = float(reset_header)
            return max(reset_at - time.time(), 1.0)
        except ValueError:
            return None
    return None


def _looks_like_rate_limit(status: int, body_text: str, headers: aiohttp.typedefs.LooseHeaders) -> bool:
    if status == 429:
        return True
    if status != 403:
        return False
    lowered = (body_text or "").lower()
    if "rate limit" in lowered or "too many requests" in lowered:
        return True
    remaining = headers.get("x-ratelimit-remaining")
    return remaining == "0"


def _log_ai_backoff_once(message: str) -> None:
    global _ai_last_backoff_log_at
    now = time.monotonic()
    if now - _ai_last_backoff_log_at < 15:
        return
    _ai_last_backoff_log_at = now
    print(message)


def _extract_text_from_payload(data: dict[str, Any]) -> str | None:
    text = None

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

    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        text = "\n".join(part for part in parts if part).strip()

    if isinstance(text, str):
        return text.strip()
    return None


async def pos_chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = POS_AI_MAX_TOKENS,
    temperature: float = POS_AI_TEMPERATURE,
    top_p: float = POS_AI_TOP_P,
    timeout: int = POS_AI_TIMEOUT_SECONDS,
) -> str | None:
    if not POS_AI_API_KEY:
        return None
    if ai_is_temporarily_unavailable():
        _log_ai_backoff_once(
            f"P.OS AI cooldown active: {ai_unavailable_reason()} ({ai_cooldown_remaining():.0f}s remaining)."
        )
        return None

    accept_header = "application/vnd.github+json" if POS_AI_PROVIDER == "github_models" else "application/json"
    payload = {
        "messages": messages,
        "model": POS_AI_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {POS_AI_API_KEY}",
        "Content-Type": "application/json",
        "Accept": accept_header,
    }
    if POS_AI_PROVIDER == "github_models":
        headers["X-GitHub-Api-Version"] = GITHUB_MODELS_API_VERSION

    response_text = ""
    try:
        async with _AI_REQUEST_SEMAPHORE:
            if ai_is_temporarily_unavailable():
                _log_ai_backoff_once(
                    f"P.OS AI cooldown active: {ai_unavailable_reason()} ({ai_cooldown_remaining():.0f}s remaining)."
                )
                return None

            timeout_config = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(POS_AI_API_URL, headers=headers, json=payload, timeout=timeout) as resp:
                    response_text = await resp.text()
                    if _looks_like_rate_limit(resp.status, response_text, resp.headers):
                        retry_after = _parse_retry_after(resp.headers) or POS_AI_RATE_LIMIT_FALLBACK_SECONDS
                        _set_ai_backoff(retry_after, "rate_limited")
                        _log_ai_backoff_once(
                            f"P.OS API rate limited: pause for {retry_after:.0f}s before next request."
                        )
                        return None

                    if resp.status >= 500:
                        _set_ai_backoff(10, "upstream_error")
                        print(f"P.OS upstream error {resp.status}: {response_text[:500]}")
                        return None

                    if resp.status >= 400:
                        if POS_AI_PROVIDER == "github_models" and resp.status in {401, 403}:
                            print(
                                "P.OS GitHub Models auth error: проверь, что GITHUB_MODELS_TOKEN существует "
                                "и имеет доступ к Models API (PAT со scope/permission models)."
                            )
                        print(f"P.OS API error {resp.status}: {response_text[:500]}")
                        return None
    except Exception as exc:
        print(f"P.OS API request failed: {exc}")
        return None

    try:
        data = json.loads(response_text)
    except Exception:
        print(f"P.OS API returned non-JSON: {response_text[:500]}")
        return None

    text = _extract_text_from_payload(data)
    if not text:
        print(f"P.OS API empty response: {response_text[:500]}")
        return None
    return text


def extract_json_block(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    candidates = [raw]
    if "```" in raw:
        parts = raw.split("```")
        candidates.extend(part.strip() for part in parts if part.strip())

    for candidate in candidates:
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            continue
        blob = candidate[start:end + 1]
        try:
            parsed = json.loads(blob)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
