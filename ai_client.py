from __future__ import annotations

import json
from typing import Any

import aiohttp

from config import POS_AI_API_KEY, POS_AI_API_URL, POS_AI_MODEL


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
    max_tokens: int = 700,
    temperature: float = 1.0,
    top_p: float = 1.0,
    timeout: int = 60,
) -> str | None:
    if not POS_AI_API_KEY:
        return None

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
        "Accept": "application/json",
    }

    response_text = ""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(POS_AI_API_URL, headers=headers, json=payload, timeout=timeout) as resp:
                response_text = await resp.text()
                if resp.status >= 400:
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
