"""Bounded Google Safe Browsing v5 URL lookups with protocol-compliant caching."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass

import aiohttp


logger = logging.getLogger(__name__)

SAFE_BROWSING_V5_URL = "https://safebrowsing.googleapis.com/v5/urls:search"
MAX_URLS_PER_LOOKUP = 50
MAX_RESPONSE_BYTES = 256_000
DEFAULT_SAFE_CACHE_SECONDS = 10 * 60
DEFAULT_THREAT_CACHE_SECONDS = 60 * 60
MAX_CACHE_SECONDS = 24 * 60 * 60
MAX_CACHE_ENTRIES = 4_000
_DURATION_PATTERN = re.compile(r"^(\d+)(?:\.(\d{1,9}))?s$")


@dataclass(frozen=True)
class SafeBrowsingVerdict:
    checked: bool
    matched: bool
    threat_types: tuple[str, ...] = ()
    cache_seconds: float = 0.0


_CACHE: dict[str, tuple[SafeBrowsingVerdict, float]] = {}


def _parse_cache_duration(value: object, *, matched: bool) -> float:
    default = (
        DEFAULT_THREAT_CACHE_SECONDS
        if matched
        else DEFAULT_SAFE_CACHE_SECONDS
    )
    match = _DURATION_PATTERN.fullmatch(str(value or "").strip())
    if match is None:
        return float(default)
    seconds = float(match.group(1))
    fractional = match.group(2)
    if fractional:
        seconds += int(fractional) / (10 ** len(fractional))
    if not math.isfinite(seconds) or seconds <= 0:
        return float(default)
    return min(seconds, float(MAX_CACHE_SECONDS))


def _cached(url: str, now: float) -> SafeBrowsingVerdict | None:
    entry = _CACHE.get(url)
    if entry is None:
        return None
    verdict, expires_at = entry
    if now >= expires_at:
        _CACHE.pop(url, None)
        return None
    return verdict


def _store(
    verdicts: dict[str, SafeBrowsingVerdict],
    now: float,
) -> None:
    for url, verdict in verdicts.items():
        expires_at = now + max(1.0, verdict.cache_seconds)
        _CACHE[url] = (verdict, expires_at)
    if len(_CACHE) <= MAX_CACHE_ENTRIES:
        return
    for url, _entry in sorted(
        _CACHE.items(),
        key=lambda item: item[1][1],
    )[: MAX_CACHE_ENTRIES // 2]:
        _CACHE.pop(url, None)


async def lookup_urls(
    session: aiohttp.ClientSession,
    urls: list[str] | tuple[str, ...],
    *,
    api_key: str,
) -> SafeBrowsingVerdict:
    """Look up up to 50 browser URLs using Safe Browsing v5.

    Safe and unsafe responses are cached for the exact duration returned by
    Google. Network and protocol failures are explicit ``checked=False``
    results and are never cached as safe.
    """
    clean_urls = list(dict.fromkeys(
        str(url).strip()
        for url in urls
        if isinstance(url, str) and str(url).strip()
    ))[:MAX_URLS_PER_LOOKUP]
    if not api_key or not clean_urls:
        return SafeBrowsingVerdict(checked=False, matched=False)

    now = time.monotonic()
    cached_verdicts = [_cached(url, now) for url in clean_urls]
    if cached_verdicts and all(verdict is not None for verdict in cached_verdicts):
        matched = any(bool(verdict and verdict.matched) for verdict in cached_verdicts)
        threat_types = sorted({
            threat_type
            for verdict in cached_verdicts
            if verdict is not None
            for threat_type in verdict.threat_types
        })
        remaining = min(
            max(1.0, _CACHE[url][1] - now)
            for url in clean_urls
        )
        return SafeBrowsingVerdict(
            checked=True,
            matched=matched,
            threat_types=tuple(threat_types),
            cache_seconds=remaining,
        )

    params = [("urls", url) for url in clean_urls]
    headers = {
        "x-goog-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": "P.OS/0.8",
    }
    try:
        async with session.get(
            SAFE_BROWSING_V5_URL,
            params=params,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                return SafeBrowsingVerdict(checked=False, matched=False)
    except Exception as exc:
        fingerprint = hashlib.sha256(
            "\n".join(clean_urls).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        logger.warning(
            "Safe Browsing v5 lookup failed for urls_sha256=%s (%s).",
            fingerprint,
            type(exc).__name__,
        )
        return SafeBrowsingVerdict(checked=False, matched=False)

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (TypeError, UnicodeError, json.JSONDecodeError):
        return SafeBrowsingVerdict(checked=False, matched=False)
    if not isinstance(payload, dict):
        return SafeBrowsingVerdict(checked=False, matched=False)

    threats = payload.get("threats")
    if threats is None:
        threats = []
    elif not isinstance(threats, list):
        return SafeBrowsingVerdict(checked=False, matched=False)
    threat_types_by_url: dict[str, set[str]] = {}
    matched_urls: set[str] = set()
    unmatched_threat_types: set[str] = set()
    has_unmatched_threat = False
    for item in threats:
        if not isinstance(item, dict):
            has_unmatched_threat = True
            continue
        raw_item_types = item.get("threatTypes")
        item_type_values: list[object] = (
            raw_item_types
            if isinstance(raw_item_types, list)
            else []
        )
        item_types = {
            str(threat_type)
            for threat_type in item_type_values
            if isinstance(threat_type, str) and threat_type
        }
        threat_url = str(item.get("url") or "").strip()
        if threat_url in clean_urls:
            matched_urls.add(threat_url)
            threat_types_by_url.setdefault(threat_url, set()).update(item_types)
        else:
            # v5 may return a host/path expression rather than the exact input.
            # Without implementing Google's full URL-expression algorithm,
            # conservatively associate that match with every URL in this batch.
            has_unmatched_threat = True
            unmatched_threat_types.update(item_types)

    matched = bool(threats)
    cache_seconds = _parse_cache_duration(
        payload.get("cacheDuration"),
        matched=matched,
    )
    verdicts = {
        url: SafeBrowsingVerdict(
            checked=True,
            matched=url in matched_urls or has_unmatched_threat,
            threat_types=tuple(sorted(
                threat_types_by_url.get(url, set()) | unmatched_threat_types
            )),
            cache_seconds=cache_seconds,
        )
        for url in clean_urls
    }
    _store(verdicts, now)
    aggregate_threat_types = sorted({
        threat_type
        for verdict in verdicts.values()
        for threat_type in verdict.threat_types
    })
    return SafeBrowsingVerdict(
        checked=True,
        matched=any(verdict.matched for verdict in verdicts.values()),
        threat_types=tuple(aggregate_threat_types),
        cache_seconds=cache_seconds,
    )


async def lookup_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    api_key: str,
) -> SafeBrowsingVerdict:
    return await lookup_urls(session, [url], api_key=api_key)


def clear_cache() -> None:
    """Test/support hook for clearing process-local Safe Browsing state."""
    _CACHE.clear()
