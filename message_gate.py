from __future__ import annotations

import asyncio
import time


_events: dict[int, asyncio.Event] = {}
_results: dict[int, tuple[bool, float]] = {}
_RESULT_TTL_SECONDS = 120.0
_MAX_RESULTS = 5000


def _prune(now: float) -> None:
    if len(_results) <= _MAX_RESULTS:
        expired = [message_id for message_id, (_blocked, ts) in _results.items() if now - ts > _RESULT_TTL_SECONDS]
    else:
        expired = sorted(_results, key=lambda message_id: _results[message_id][1])[: len(_results) // 2]
    for message_id in expired:
        _results.pop(message_id, None)
        _events.pop(message_id, None)


def begin_moderation(message_id: int) -> None:
    _events.setdefault(message_id, asyncio.Event())


def finish_moderation(message_id: int, blocked: bool) -> None:
    now = time.monotonic()
    _results[message_id] = (bool(blocked), now)
    event = _events.pop(message_id, None)
    if event is not None:
        event.set()
    _prune(now)


async def wait_for_moderation(message_id: int, timeout: float = 75.0) -> bool | None:
    """Return blocked state, or None when moderation did not finish safely."""
    cached = _results.get(message_id)
    if cached is not None:
        return cached[0]

    event = _events.setdefault(message_id, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        if _events.get(message_id) is event:
            _events.pop(message_id, None)
        return None
    result = _results.get(message_id)
    return result[0] if result is not None else None
