from __future__ import annotations

import asyncio
import time


_events: dict[tuple[int, int], asyncio.Event] = {}
_results: dict[tuple[int, int], tuple[bool, float]] = {}
_RESULT_TTL_SECONDS = 120.0
_MAX_RESULTS = 5000


def _prune(now: float) -> None:
    if len(_results) <= _MAX_RESULTS:
        expired = [key for key, (_suppress, ts) in _results.items() if now - ts > _RESULT_TTL_SECONDS]
    else:
        expired = sorted(_results, key=lambda key: _results[key][1])[: len(_results) // 2]
    for key in expired:
        _results.pop(key, None)
        _events.pop(key, None)


def begin_join_security(guild_id: int, user_id: int) -> None:
    _events.setdefault((guild_id, user_id), asyncio.Event())


def finish_join_security(guild_id: int, user_id: int, *, suppress_roles: bool) -> None:
    key = (guild_id, user_id)
    now = time.monotonic()
    _results[key] = (bool(suppress_roles), now)
    event = _events.pop(key, None)
    if event is not None:
        event.set()
    _prune(now)


async def wait_for_join_security(
    guild_id: int,
    user_id: int,
    *,
    timeout: float = 15.0,
) -> bool | None:
    """Return whether welcome/roles must be suppressed, or None on timeout."""
    key = (guild_id, user_id)
    cached = _results.get(key)
    if cached is not None:
        return cached[0]

    event = _events.setdefault(key, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        if _events.get(key) is event:
            _events.pop(key, None)
        return None
    result = _results.get(key)
    return result[0] if result is not None else None
