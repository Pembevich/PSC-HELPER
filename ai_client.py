from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from config import (
    GITHUB_MODELS_API_VERSION,
    POS_AI_API_KEY,
    POS_AI_API_PROVIDER,
    POS_AI_MAX_CONCURRENT_REQUESTS,
    POS_AI_MAX_TOKENS,
    POS_AI_PROVIDER_KEYS,
    POS_AI_PROVIDER_MODELS,
    POS_AI_PROVIDER_URLS,
    POS_AI_API_URL,
    POS_AI_RATE_LIMIT_FALLBACK_SECONDS,
    POS_AI_MODEL,
    POS_AI_TIMEOUT_SECONDS,
    POS_AI_TOP_P,
    POS_AI_TEMPERATURE,
)


logger = logging.getLogger(__name__)

_AI_REQUEST_SEMAPHORE = asyncio.Semaphore(max(1, POS_AI_MAX_CONCURRENT_REQUESTS))
# #14: защищаем read-modify-write общих _provider_cursor/_provider_backoff_until,
# чтобы при POS_AI_MAX_CONCURRENT_REQUESTS > 1 два запроса не выбрали один индекс
# и курсор не «перескакивал».
_provider_lock = asyncio.Lock()
_ai_backoff_until = 0.0
_ai_backoff_reason = ""
_ai_last_backoff_log_at = 0.0
_provider_cursor = 0
_provider_backoff_until: dict[int, float] = {}
_MAX_UPSTREAM_RESPONSE_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class _AIQueueTimeout(Exception):
    pass


@asynccontextmanager
async def _request_slot(total_timeout: int):
    acquired = False
    queue_timeout = max(3.0, min(float(total_timeout) * 0.25, 15.0))
    try:
        try:
            await asyncio.wait_for(_AI_REQUEST_SEMAPHORE.acquire(), timeout=queue_timeout)
        except asyncio.TimeoutError as exc:
            raise _AIQueueTimeout from exc
        acquired = True
        yield
    finally:
        if acquired:
            _AI_REQUEST_SEMAPHORE.release()


def _is_safe_provider_url(url: str) -> bool:
    """Require HTTPS, except for explicit loopback-only development endpoints."""
    if not isinstance(url, str) or not url or any(char.isspace() for char in url):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        _ = parsed.port  # Validate a malformed/non-numeric port eagerly.
    except (TypeError, ValueError):
        return False
    if not host or parsed.username is not None or parsed.password is not None:
        return False
    if parsed.fragment:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and host in _LOOPBACK_HOSTS


def _provider_kind(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "models.github.ai":
        return "github_models"
    if host == "googleapis.com" or host.endswith(".googleapis.com"):
        return "gemini"
    return POS_AI_API_PROVIDER


def _build_provider_pool() -> list[dict[str, str]]:
    if POS_AI_PROVIDER_KEYS:
        if POS_AI_PROVIDER_URLS and len(POS_AI_PROVIDER_URLS) != len(POS_AI_PROVIDER_KEYS):
            logger.error(
                "POS_AI_PROVIDER_URLS must contain exactly one URL per POS_AI_PROVIDER_KEYS entry; AI pool disabled."
            )
            return []
        if POS_AI_PROVIDER_MODELS and len(POS_AI_PROVIDER_MODELS) != len(POS_AI_PROVIDER_KEYS):
            logger.error(
                "POS_AI_PROVIDER_MODELS must contain exactly one model per POS_AI_PROVIDER_KEYS entry; AI pool disabled."
            )
            return []
        pool: list[dict[str, str]] = []
        for index, key in enumerate(POS_AI_PROVIDER_KEYS):
            url = POS_AI_PROVIDER_URLS[index] if index < len(POS_AI_PROVIDER_URLS) else POS_AI_API_URL
            model = POS_AI_PROVIDER_MODELS[index] if index < len(POS_AI_PROVIDER_MODELS) else POS_AI_MODEL
            if not _is_safe_provider_url(url):
                logger.error("AI provider %s has an unsafe or invalid URL and was skipped.", index + 1)
                continue
            if not key.strip() or not model.strip():
                logger.error("AI provider %s is missing a key or model and was skipped.", index + 1)
                continue
            pool.append(
                {
                    "name": f"provider_{index + 1}",
                    "api_key": key.strip(),
                    "api_url": url,
                    "model": model.strip(),
                    "provider": _provider_kind(url),
                }
            )
        if pool:
            return pool

    if not _is_safe_provider_url(POS_AI_API_URL):
        logger.error("Default AI provider has an unsafe or invalid URL; AI is disabled.")
        return []
    if not (POS_AI_MODEL or "").strip():
        logger.error("Default AI provider model is empty; AI is disabled.")
        return []
    return [
        {
            "name": "default",
            "api_key": (POS_AI_API_KEY or "").strip(),
            "api_url": POS_AI_API_URL,
            "model": POS_AI_MODEL.strip(),
            "provider": _provider_kind(POS_AI_API_URL),
        }
    ]


_AI_PROVIDER_POOL = _build_provider_pool()


def ai_has_configured_provider() -> bool:
    """True, если есть хотя бы один реально настроенный AI-провайдер."""
    return bool(_AI_PROVIDER_POOL and any(provider.get("api_key") for provider in _AI_PROVIDER_POOL))


def ai_cooldown_remaining() -> float:
    remaining = _ai_backoff_until - time.monotonic()
    return remaining if remaining > 0 else 0.0


def ai_is_temporarily_unavailable() -> bool:
    return ai_cooldown_remaining() > 0


def ai_unavailable_reason() -> str:
    return _ai_backoff_reason or "temporarily_unavailable"


def _provider_cooldown_remaining(index: int) -> float:
    until = _provider_backoff_until.get(index, 0.0)
    remaining = until - time.monotonic()
    return remaining if remaining > 0 else 0.0


# 0.8: Gemini — приоритетный провайдер для ВСЕХ запросов (и чат P.OS, и
# модерация). Остальные провайдеры из пула задействуются ТОЛЬКО когда все
# Gemini-провайдеры на cooldown. Если явно запрошен provider_type — он имеет
# наивысший приоритет; иначе предпочитаем "gemini".
PRIMARY_PROVIDER = "gemini"


def _pick_provider_index(provider_type: str | None = None) -> int | None:
    if not _AI_PROVIDER_POOL:
        return None
    total = len(_AI_PROVIDER_POOL)
    start = _provider_cursor % total

    # Порядок предпочтений по типу провайдера:
    # 1) явно запрошенный provider_type (если задан),
    # 2) Gemini как первичный провайдер,
    # 3) любой доступный — как запасной.
    preferred: list[str] = []
    if provider_type:
        preferred.append(provider_type)
    if PRIMARY_PROVIDER not in preferred:
        preferred.append(PRIMARY_PROVIDER)

    # Проходим тиры предпочтений: внутри каждого тира — round-robin от курсора.
    for wanted in preferred:
        for offset in range(total):
            idx = (start + offset) % total
            if _provider_cooldown_remaining(idx) <= 0 and _AI_PROVIDER_POOL[idx]["provider"] == wanted:
                return idx

    # Запасной тир: любой доступный (uncool) провайдер.
    for offset in range(total):
        idx = (start + offset) % total
        if _provider_cooldown_remaining(idx) <= 0:
            return idx

    return None


async def _reserve_provider_index(provider_type: str | None = None) -> int | None:
    """#14: Атомарно выбрать провайдера и сдвинуть курсор под локом."""
    global _provider_cursor
    async with _provider_lock:
        idx = _pick_provider_index(provider_type)
        if idx is not None:
            _provider_cursor = (idx + 1) % len(_AI_PROVIDER_POOL)
        return idx


async def _mark_provider_backoff(index: int, seconds: float) -> None:
    """#14: Атомарно продлить кулдаун провайдера под локом."""
    async with _provider_lock:
        _provider_backoff_until[index] = max(
            _provider_backoff_until.get(index, 0.0), time.monotonic() + seconds
        )


def _set_ai_backoff(seconds: float, reason: str) -> None:
    global _ai_backoff_until, _ai_backoff_reason
    cooldown = _bounded_float(seconds, 1.0, 1.0, 3600.0)
    _ai_backoff_until = max(_ai_backoff_until, time.monotonic() + cooldown)
    _ai_backoff_reason = reason


def _parse_retry_after(headers: Mapping[str, str]) -> float | None:
    if not headers:
        return None

    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        try:
            parsed_seconds = float(retry_after)
            if math.isfinite(parsed_seconds):
                return max(1.0, min(parsed_seconds, 3600.0))
        except ValueError:
            try:
                retry_dt = parsedate_to_datetime(retry_after)
                return max(1.0, min(retry_dt.timestamp() - time.time(), 3600.0))
            except Exception:
                pass

    reset_header = headers.get("x-ratelimit-reset")
    if reset_header:
        try:
            reset_at = float(reset_header)
            if math.isfinite(reset_at):
                return max(1.0, min(reset_at - time.time(), 3600.0))
        except ValueError:
            pass
    return None


def _looks_like_rate_limit(status: int, body_text: str, headers: Mapping[str, str]) -> bool:
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
    logger.warning("%s", message)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(parsed, maximum))


async def _read_bounded_response(response: aiohttp.ClientResponse) -> str:
    raw = await response.content.read(_MAX_UPSTREAM_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_UPSTREAM_RESPONSE_BYTES:
        raise ValueError("upstream response exceeds the configured size limit")
    encoding = response.charset or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _extract_message_from_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    choices = data.get("choices")
    if choices and isinstance(choices, list):
        choice0 = choices[0] or {}
        msg = choice0.get("message")
        if msg:
            return msg
        text = choice0.get("text")
        if text:
            return {"role": "assistant", "content": text}

    result = data.get("result") or {}
    choices = result.get("choices")
    if choices and isinstance(choices, list):
        choice0 = choices[0] or {}
        msg = choice0.get("message")
        if msg:
            return msg
        text = choice0.get("text")
        if text:
            return {"role": "assistant", "content": text}

    text = data.get("output_text") or data.get("generated_text")
    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        text = "\n".join(part for part in parts if part).strip()

    if isinstance(text, str):
        return {"role": "assistant", "content": text.strip()}

    return None


def _upstream_body_fingerprint(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8", errors="replace")).hexdigest()[:16]


async def pos_chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = POS_AI_MAX_TOKENS,
    temperature: float = POS_AI_TEMPERATURE,
    top_p: float = POS_AI_TOP_P,
    timeout: int = POS_AI_TIMEOUT_SECONDS,
    provider_type: str | None = None,
) -> dict[str, Any] | None:
    if not ai_has_configured_provider():
        return None

    request_max_tokens = _bounded_int(max_tokens, POS_AI_MAX_TOKENS, 1, 32_768)
    request_temperature = _bounded_float(temperature, POS_AI_TEMPERATURE, 0.0, 2.0)
    request_top_p = _bounded_float(top_p, POS_AI_TOP_P, 0.0, 1.0)
    request_timeout = _bounded_int(timeout, POS_AI_TIMEOUT_SECONDS, 5, 300)
    max_attempts = len(_AI_PROVIDER_POOL)

    for attempt in range(max_attempts):
        response_text = ""
        provider_index: int | None = None
        provider: dict[str, str] | None = None
        try:
            async with _request_slot(request_timeout):
                if ai_is_temporarily_unavailable():
                    _log_ai_backoff_once(
                        f"P.OS AI cooldown active: {ai_unavailable_reason()} ({ai_cooldown_remaining():.0f}s remaining)."
                    )
                    return None

                provider_index = await _reserve_provider_index(provider_type)
                if provider_index is None:
                    shortest = min((_provider_cooldown_remaining(i) for i in range(len(_AI_PROVIDER_POOL))), default=5.0)
                    _set_ai_backoff(shortest, "all_providers_rate_limited")
                    _log_ai_backoff_once(
                        f"P.OS AI provider pool cooldown: all providers limited, retry in {shortest:.0f}s."
                    )
                    return None

                provider = _AI_PROVIDER_POOL[provider_index]

                accept_header = "application/vnd.github+json" if provider["provider"] == "github_models" else "application/json"
                payload = {
                    "messages": messages,
                    "model": provider["model"],
                    "max_tokens": request_max_tokens,
                    "temperature": request_temperature,
                    "top_p": request_top_p,
                    "stream": False,
                }
                if "googleapis.com" not in provider["api_url"] and provider["provider"] != "gemini":
                    payload["frequency_penalty"] = 0.35
                    payload["presence_penalty"] = 0.2
                if tools:
                    payload["tools"] = tools
                headers = {
                    "Authorization": f"Bearer {provider['api_key']}",
                    "Content-Type": "application/json",
                    "Accept": accept_header,
                }
                if provider["provider"] == "github_models":
                    headers["X-GitHub-Api-Version"] = GITHUB_MODELS_API_VERSION

                timeout_config = aiohttp.ClientTimeout(total=request_timeout)
                async with aiohttp.ClientSession(timeout=timeout_config) as session:
                    async with session.post(
                        provider["api_url"],
                        headers=headers,
                        json=payload,
                        allow_redirects=False,
                    ) as resp:
                        response_text = await _read_bounded_response(resp)
                        if _looks_like_rate_limit(resp.status, response_text, resp.headers):
                            retry_after = _parse_retry_after(resp.headers) or POS_AI_RATE_LIMIT_FALLBACK_SECONDS
                            await _mark_provider_backoff(provider_index, retry_after)
                            _log_ai_backoff_once(
                                f"P.OS API rate limited ({provider['name']}): pause for {retry_after:.0f}s."
                            )
                            # Только проверяем наличие свободного провайдера — резерв
                            # выполнит следующая итерация цикла (иначе курсор
                            # сдвигался дважды и провайдеры пропускались).
                            if _pick_provider_index(provider_type) is not None:
                                continue  # retry next provider
                            _set_ai_backoff(min(retry_after, 30.0), "rate_limited")
                            return None

                        if resp.status >= 500:
                            await _mark_provider_backoff(provider_index, 8.0)
                            logger.warning(
                                "P.OS upstream error %s (%s), body_sha256=%s",
                                resp.status,
                                provider["name"],
                                _upstream_body_fingerprint(response_text),
                            )
                            if _pick_provider_index() is not None:
                                continue  # retry next provider
                            _set_ai_backoff(5.0, "upstream_error")
                            return None

                        if 300 <= resp.status < 400:
                            await _mark_provider_backoff(provider_index, 300.0)
                            logger.warning(
                                "P.OS AI endpoint returned an unexpected redirect (%s, %s).",
                                resp.status,
                                provider["name"],
                            )
                            if attempt < max_attempts - 1:
                                continue
                            return None

                        if resp.status >= 400:
                            if provider["provider"] == "github_models" and resp.status in {401, 403}:
                                logger.error("P.OS GitHub Models authentication failed.")
                            logger.warning(
                                "P.OS API error %s (%s), body_sha256=%s",
                                resp.status,
                                provider["name"],
                                _upstream_body_fingerprint(response_text),
                            )
                            # 400/413/422 are request-specific and must not take a
                            # healthy provider out of rotation. Auth and endpoint
                            # failures are provider-specific and do get cooldowns.
                            if resp.status in {401, 403}:
                                await _mark_provider_backoff(provider_index, 3600.0)
                            elif resp.status == 404:
                                await _mark_provider_backoff(provider_index, 300.0)
                            elif resp.status in {408, 409, 425}:
                                await _mark_provider_backoff(provider_index, 10.0)
                            if attempt < max_attempts - 1:
                                continue
                            return None
        except _AIQueueTimeout:
            _log_ai_backoff_once("P.OS AI queue is full; request rejected before provider call.")
            return None
        except asyncio.TimeoutError:
            name = provider["name"] if provider else "unknown"
            logger.warning("P.OS API timeout for %s; attempting fallback.", name)
            if attempt < max_attempts - 1:
                continue
            return None
        except Exception as exc:
            # provider_index может быть не присвоен, если исключение случилось до
            # выбора провайдера — иначе тут вылетал NameError вместо возврата None.
            if provider_index is not None:
                exc_str = str(exc).lower()
                if "rate" in exc_str or "limit" in exc_str or "quota" in exc_str:
                    await _mark_provider_backoff(provider_index, 20.0)
                else:
                    await _mark_provider_backoff(provider_index, 60.0)
            name = provider["name"] if provider else "unknown"
            logger.warning(
                "P.OS API request failed (%s, %s); attempting fallback.",
                name,
                type(exc).__name__,
            )
            if attempt < max_attempts - 1:
                continue
            return None

        # Success
        try:
            data = json.loads(response_text)
        except Exception:
            logger.warning(
                "P.OS API returned non-JSON: body_sha256=%s",
                _upstream_body_fingerprint(response_text),
            )
            return None

        msg = _extract_message_from_payload(data)
        if not msg:
            logger.warning(
                "P.OS API response had no message: body_sha256=%s",
                _upstream_body_fingerprint(response_text),
            )
            return None
        return msg

    return None


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
