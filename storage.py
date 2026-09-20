"""
storage.py — async SQLite через aiosqlite.

Изменения по аудиту:
- единое долгоживущее соединение на путь к БД (раньше connect() на каждый запрос
  блокировал event loop и давал "database is locked" при параллельных сообщениях);
- режим WAL для конкурентного чтения/записи;
- целостный снапшот для бэкапа через `VACUUM INTO` (раньше копировался живой файл,
  что при активном WAL давало повреждённую копию);
- восстановление из Discord только из сообщений, отправленных САМИМ ботом
  (раньше любой, кто мог писать в backup-канал, мог подменить базу);
- удалены неиспользуемые таблицы private_chats (plaintext-пароли) и chat_messages.
"""
from __future__ import annotations

import os
import asyncio
import gzip
import hashlib
import io
import logging
import json
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Any
from weakref import WeakKeyDictionary

import discord
import aiosqlite

DEFAULT_DB_PATH = "bot_data.db"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Единое соединение на путь к БД
# ---------------------------------------------------------------------------
_connections: dict[str, aiosqlite.Connection] = {}
_conn_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
# A single aiosqlite connection serializes individual statements, but it does
# not make a multi-statement transaction atomic. Keep every write transaction
# and VACUUM snapshot behind the same lock.
_write_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _loop_lock(
    registry: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock],
) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = registry.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        registry[loop] = lock
    return lock


async def _get_conn(db_path: str = DEFAULT_DB_PATH) -> aiosqlite.Connection:
    """Вернуть (создав при необходимости) общее соединение для db_path."""
    conn = _connections.get(db_path)
    if conn is not None:
        return conn
    async with _loop_lock(_conn_locks):
        conn = _connections.get(db_path)
        if conn is not None:
            return conn
        conn = await aiosqlite.connect(db_path)
        # WAL даёт одновременное чтение во время записи и снижает блокировки.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        _connections[db_path] = conn
        return conn


async def close_all_connections() -> None:
    """Корректно закрыть все соединения (вызывать при остановке бота)."""
    async with _loop_lock(_write_locks):
        async with _loop_lock(_conn_locks):
            for path, conn in list(_connections.items()):
                try:
                    await conn.close()
                except Exception:
                    logger.exception("Не удалось закрыть соединение %s.", path)
                _connections.pop(path, None)


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    async with _loop_lock(_write_locks):
        await _init_db_unlocked(db_path)


async def _init_db_unlocked(db_path: str) -> None:
    conn = await _get_conn(db_path)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_context (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            context TEXT,
            PRIMARY KEY (user_id, guild_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_muted (
            user_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            muted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, guild_id)
        )
    """)
    # 0.8: настройки модерации/поведения на сервер. Хранятся как JSON-строка,
    # чтобы схему можно было расширять без миграций. guild_id=0 — глобальный слот
    # значений по умолчанию для всех серверов.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            settings TEXT
        )
    """)
    # Фактический журнал для P.OS: server logs, tool-действия, пинги и удаления.
    # Это не заменяет Discord log-каналы, но даёт ИИ проверяемую память, чтобы он
    # отвечал по данным, а не по догадкам.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_id INTEGER,
            actor_name TEXT,
            target_user_id INTEGER,
            target_role_id INTEGER,
            channel_id INTEGER,
            message_id INTEGER,
            summary TEXT,
            details TEXT,
            deleted INTEGER NOT NULL DEFAULT 0
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_guild_ts ON ai_event_log(guild_id, ts)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_type ON ai_event_log(event_type)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_target_user ON ai_event_log(target_user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_target_role ON ai_event_log(target_role_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_message ON ai_event_log(guild_id, message_id)")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_event_recipients (
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            source_role_id INTEGER,
            PRIMARY KEY (event_id, user_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_event_recipient_user ON ai_event_recipients(user_id, event_id)")
    # Structured execution journal. Unlike ai_event_log summaries, these rows
    # are machine-readable and can safely drive a deterministic undo without
    # asking the language model to rediscover IDs from prose.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pos_tool_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            source_guild_id INTEGER NOT NULL,
            source_channel_id INTEGER,
            source_message_id INTEGER NOT NULL,
            actor_id INTEGER NOT NULL,
            target_guild_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            args_json TEXT NOT NULL,
            result TEXT NOT NULL,
            success INTEGER NOT NULL,
            inverse_operation TEXT,
            inverse_args_json TEXT,
            undo_status TEXT NOT NULL,
            undo_started_at INTEGER,
            undone_at INTEGER,
            undo_result TEXT
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pos_actions_actor_source "
        "ON pos_tool_actions(actor_id, source_guild_id, source_channel_id, ts DESC, id DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pos_actions_message "
        "ON pos_tool_actions(source_message_id, id)"
    )
    # Telegram bridge requests are durable so retries cannot duplicate an
    # owner notification and Telegram replies stay bound to the Discord author.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_contact_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            discord_message_id INTEGER NOT NULL UNIQUE,
            discord_user_id INTEGER NOT NULL,
            discord_username TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            message_text TEXT NOT NULL,
            urgency TEXT NOT NULL,
            urgency_reason TEXT NOT NULL,
            status TEXT NOT NULL,
            telegram_message_id INTEGER UNIQUE,
            response_text TEXT,
            responded_at INTEGER,
            delivery_target TEXT,
            delivery_error TEXT
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tg_contact_user_time "
        "ON telegram_contact_requests(discord_user_id, created_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tg_contact_status_time "
        "ON telegram_contact_requests(status, created_at DESC)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS integration_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS security_state (
            guild_id INTEGER PRIMARY KEY,
            raid_until REAL NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS security_posture (
            guild_id INTEGER PRIMARY KEY,
            snapshot TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS form_decisions (
            message_id INTEGER PRIMARY KEY,
            decided_at INTEGER NOT NULL
        )
    """)
    # Rules and their delivery ledger share the backed-up database. A reserved
    # run is deliberately never retried: Discord may have accepted its action
    # before the process stopped without recording the response.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pos_automations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            trigger_events_json TEXT NOT NULL,
            channel_id INTEGER NOT NULL,
            definition_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            UNIQUE (guild_id, owner_id, name_key)
        )
    """)
    # Seconds-resolution timestamps cannot identify two edits in one second.
    # A persisted revision also protects queued work across process restarts.
    cursor = await conn.execute("PRAGMA table_info(pos_automations)")
    if "revision" not in {row[1] for row in await cursor.fetchall()}:
        await conn.execute("ALTER TABLE pos_automations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pos_automations_guild_enabled "
        "ON pos_automations(guild_id, enabled)"
    )
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS pos_automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            automation_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            finished_at INTEGER,
            UNIQUE (automation_id, event_key)
        )
    """)
    # No cascading delete: a rule's history and deduplication keys outlive it.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pos_automation_runs_guild "
        "ON pos_automation_runs(guild_id, id DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pos_automation_runs_time "
        "ON pos_automation_runs(automation_id, created_at DESC)"
    )
    # Чистим устаревшие таблицы со старых версий (plaintext-пароли и т.п.).
    await conn.execute("DROP TABLE IF EXISTS private_chats")
    await conn.execute("DROP TABLE IF EXISTS chat_messages")
    await conn.commit()


# ---------------------------------------------------------------------------
# Persistent P.OS event automations
# ---------------------------------------------------------------------------

_AUTOMATION_JSON_LIMIT = 32_768
_AUTOMATION_MAX_PER_GUILD = 50
_AUTOMATION_TERMINAL_STATUSES = {"completed", "failed", "partial", "skipped", "unknown"}


def _automation_json(value: dict) -> str:
    """Preserve JSON exactly or reject it; never stringify unknown objects."""
    if not isinstance(value, dict):
        raise ValueError("automation payload must be a JSON object")

    def validate(item: Any, depth: int = 0) -> None:
        if depth > 20:
            raise ValueError("automation payload is nested too deeply")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("automation JSON keys must be strings")
                validate(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                validate(child, depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("automation payload contains a non-JSON value")

    validate(value)
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        size = len(text.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("automation payload must be valid JSON") from exc
    if size > _AUTOMATION_JSON_LIMIT:
        raise ValueError("automation payload exceeds 32768 bytes")
    return text


def _automation_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ValueError("automation IDs must be positive SQLite integers")
    return value


def _automation_row_to_dict(row: Any) -> dict:
    item = dict(zip(
        (
            "id", "guild_id", "owner_id", "name", "trigger_events_json", "channel_id",
            "definition_json", "enabled", "created_at", "updated_at", "revision",
        ),
        row,
    ))
    item["trigger_events"] = json.loads(item.pop("trigger_events_json"))
    item["definition"] = json.loads(item.pop("definition_json"))
    item["enabled"] = bool(item["enabled"])
    return item


async def upsert_pos_automation(
    guild_id: int,
    owner_id: int,
    name: str,
    trigger_events: list[str],
    channel_id: int,
    definition: dict,
    automation_id: int | None = None,
    db_path: str = DEFAULT_DB_PATH,
    *,
    enabled: bool | None = None,
) -> dict:
    """Save definition and optional state atomically for this guild and owner."""
    guild_id = _automation_id(guild_id)
    owner_id = _automation_id(owner_id)
    channel_id = _automation_id(channel_id)
    if automation_id is not None:
        automation_id = _automation_id(automation_id)
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("automation enabled state must be boolean")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("automation name must contain 1 to 100 characters")
    name = name.strip()
    if not isinstance(trigger_events, list) or not 1 <= len(trigger_events) <= 16:
        raise ValueError("automation must have 1 to 16 trigger events")
    if any(not isinstance(event, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event) is None
           for event in trigger_events):
        raise ValueError("invalid automation trigger event")
    events_json = json.dumps(list(dict.fromkeys(trigger_events)), separators=(",", ":"))
    definition_json = _automation_json(definition)
    now = int(time.time())
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute("BEGIN IMMEDIATE")
        try:
            if automation_id is None:
                cursor = await conn.execute(
                    "SELECT id FROM pos_automations WHERE guild_id = ? AND owner_id = ? AND name_key = ?",
                    (guild_id, owner_id, name.casefold()),
                )
                row = await cursor.fetchone()
                if row is not None:
                    automation_id = int(row[0])
            if automation_id is not None:
                cursor = await conn.execute(
                    "UPDATE pos_automations SET name = ?, name_key = ?, trigger_events_json = ?, "
                    "channel_id = ?, definition_json = ?, updated_at = ?, revision = revision + 1, "
                    "enabled = COALESCE(?, enabled) "
                    "WHERE id = ? AND guild_id = ? AND owner_id = ?",
                    (
                        name, name.casefold(), events_json, channel_id, definition_json, now, enabled,
                        automation_id, guild_id, owner_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("automation does not belong to this guild and owner")
            else:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM pos_automations WHERE guild_id = ?", (guild_id,),
                )
                count_row = await cursor.fetchone()
                if count_row is not None and int(count_row[0]) >= _AUTOMATION_MAX_PER_GUILD:
                    raise ValueError("guild has reached its limit of 50 automations")
                cursor = await conn.execute(
                    "INSERT INTO pos_automations (guild_id, owner_id, name, name_key, "
                    "trigger_events_json, channel_id, definition_json, created_at, updated_at, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guild_id, owner_id, name, name.casefold(), events_json, channel_id, definition_json,
                     now, now, enabled if enabled is not None else True),
                )
                automation_id = cursor.lastrowid
                if automation_id is None:
                    raise RuntimeError("SQLite did not return an automation ID")
            cursor = await conn.execute(
                "SELECT id, guild_id, owner_id, name, trigger_events_json, channel_id, "
                "definition_json, enabled, created_at, updated_at, revision "
                "FROM pos_automations WHERE id = ?", (automation_id,),
            )
            saved_row = await cursor.fetchone()
            if saved_row is None:
                raise RuntimeError("SQLite did not return the saved automation")
            result = _automation_row_to_dict(saved_row)
            await conn.commit()
            return result
        except sqlite3.IntegrityError as exc:
            await conn.rollback()
            raise ValueError("an automation with this name already belongs to this owner") from exc
        except BaseException:
            await conn.rollback()
            raise


async def list_pos_automations(
    guild_id: int,
    *,
    enabled_only: bool = False,
    trigger_event: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT id, guild_id, owner_id, name, trigger_events_json, channel_id, "
        "definition_json, enabled, created_at, updated_at, revision FROM pos_automations "
        "WHERE guild_id = ? AND (? = 0 OR enabled = 1) ORDER BY id",
        (_automation_id(guild_id), int(bool(enabled_only))),
    )
    rules = [_automation_row_to_dict(row) for row in await cursor.fetchall()]
    if trigger_event is not None:
        rules = [rule for rule in rules if trigger_event in rule["trigger_events"]]
    return rules


async def set_pos_automation_enabled(
    guild_id: int,
    automation_id: int,
    enabled: bool,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    if not isinstance(enabled, bool):
        raise ValueError("automation enabled state must be boolean")
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "UPDATE pos_automations SET enabled = ?, updated_at = ?, revision = revision + 1 "
            "WHERE id = ? AND guild_id = ?",
            (int(enabled), int(time.time()), _automation_id(automation_id), _automation_id(guild_id)),
        )
        await conn.commit()
        return cursor.rowcount == 1


async def delete_pos_automation(
    guild_id: int,
    automation_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "DELETE FROM pos_automations WHERE id = ? AND guild_id = ?",
            (_automation_id(automation_id), _automation_id(guild_id)),
        )
        await conn.commit()
        return cursor.rowcount == 1


async def claim_pos_automation_run(
    guild_id: int,
    automation_id: int,
    event_key: str,
    *,
    cooldown_seconds: int = 0,
    expected_revision: int | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int | None:
    """Reserve an enabled rule's event once, including after unknown outcomes."""
    guild_id = _automation_id(guild_id)
    automation_id = _automation_id(automation_id)
    if expected_revision is not None:
        expected_revision = _automation_id(expected_revision)
    if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 256:
        raise ValueError("automation event key must contain 1 to 256 characters")
    if isinstance(cooldown_seconds, bool) or not isinstance(cooldown_seconds, int) or not 0 <= cooldown_seconds <= 604_800:
        raise ValueError("automation cooldown must be between 0 and 604800 seconds")
    now = int(time.time())
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            """
            INSERT INTO pos_automation_runs (guild_id, automation_id, event_key, status, created_at)
            SELECT guild_id, id, ?, 'reserved', ? FROM pos_automations
            WHERE guild_id = ? AND id = ? AND enabled = 1
              AND (? IS NULL OR revision = ?)
              AND (? = 0 OR NOT EXISTS (
                SELECT 1 FROM pos_automation_runs
                WHERE automation_id = ? AND created_at > ? AND status != 'skipped'
              ))
            ON CONFLICT (automation_id, event_key) DO NOTHING
            """,
            (event_key, now, guild_id, automation_id, expected_revision, expected_revision,
             cooldown_seconds, automation_id, now - cooldown_seconds),
        )
        await conn.commit()
        return int(cursor.lastrowid) if cursor.rowcount == 1 and cursor.lastrowid is not None else None


async def finish_pos_automation_run(
    run_id: int,
    *,
    status: str,
    result: dict,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    run_id = _automation_id(run_id)
    if not isinstance(status, str) or status not in _AUTOMATION_TERMINAL_STATUSES:
        raise ValueError("invalid terminal automation run status")
    result_json = _automation_json(result)
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE pos_automation_runs SET status = ?, result_json = ?, finished_at = ? "
            "WHERE id = ? AND status = 'reserved'",
            (status, result_json, int(time.time()), run_id),
        )
        await conn.commit()


async def list_pos_automation_runs(
    guild_id: int,
    automation_id: int | None = None,
    limit: int = 10,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    guild_id = _automation_id(guild_id)
    if automation_id is not None:
        automation_id = _automation_id(automation_id)
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT id, guild_id, automation_id, event_key, status, result_json, created_at, finished_at "
        "FROM pos_automation_runs WHERE guild_id = ? AND (? IS NULL OR automation_id = ?) "
        "ORDER BY id DESC LIMIT ?",
        (guild_id, automation_id, automation_id, max(1, min(int(limit), 50))),
    )
    results = []
    for row in await cursor.fetchall():
        item = dict(zip(
            ("id", "guild_id", "automation_id", "event_key", "status", "result_json", "created_at", "finished_at"),
            row,
        ))
        item["result"] = json.loads(item.pop("result_json"))
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# entries helpers
# ---------------------------------------------------------------------------

async def add_entry(title: str, description: str, db_path: str = DEFAULT_DB_PATH) -> int:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "INSERT INTO entries (title, description) VALUES (?, ?)",
            ((title or "").strip(), (description or "").strip()),
        )
        await conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an ID for the inserted entry")
        return cursor.lastrowid


async def delete_entry(entry_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def list_entries(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> list[tuple[int, str, str]]:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT id, title, description FROM entries ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    )
    rows = await cursor.fetchall()
    return [(int(row[0]), str(row[1] or ""), str(row[2] or "")) for row in rows]


# ---------------------------------------------------------------------------
# AI context helpers  (keyed by user_id + guild_id)
# user_id=0 is used as the guild-level shared memory slot.
# ---------------------------------------------------------------------------

async def get_ai_context(user_id: int, guild_id: int, db_path: str = DEFAULT_DB_PATH) -> str | None:
    """Return stored AI context JSON string for (user_id, guild_id), or None."""
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT context FROM ai_context WHERE user_id = ? AND guild_id = ?",
        (user_id, guild_id),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else None


async def update_ai_context(user_id: int, guild_id: int, context: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Upsert AI context JSON string for (user_id, guild_id)."""
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "INSERT INTO ai_context (user_id, guild_id, context) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET context = excluded.context",
            (user_id, guild_id, context),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# AI mute helpers
# ---------------------------------------------------------------------------

async def is_ai_muted(user_id: int, guild_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Return True if P.OS is muted for this user on this guild."""
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT muted FROM ai_muted WHERE user_id = ? AND guild_id = ?",
        (user_id, guild_id),
    )
    row = await cursor.fetchone()
    return bool(row and row[0])


async def set_ai_muted_user(user_id: int, guild_id: int, muted: bool, db_path: str = DEFAULT_DB_PATH) -> None:
    """Add a user to P.OS ignore or physically remove the ignore record."""
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        if muted:
            await conn.execute(
                "INSERT INTO ai_muted (user_id, guild_id, muted) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, guild_id) DO UPDATE SET muted = 1",
                (user_id, guild_id),
            )
        else:
            await conn.execute(
                "DELETE FROM ai_muted WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
        await conn.commit()


# ---------------------------------------------------------------------------
# Guild settings helpers (0.8) — per-guild JSON blob of moderation/behaviour
# toggles. guild_id=0 is the global default slot.
# ---------------------------------------------------------------------------

async def get_guild_settings_raw(guild_id: int, db_path: str = DEFAULT_DB_PATH) -> str | None:
    """Return the stored settings JSON string for guild_id, or None."""
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT settings FROM guild_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else None


async def set_guild_settings_raw(guild_id: int, settings_json: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Upsert the settings JSON string for guild_id."""
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "INSERT INTO guild_settings (guild_id, settings) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET settings = excluded.settings",
            (guild_id, settings_json),
        )
        await conn.commit()


async def claim_form_decision(
    message_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Atomically claim a persistent form decision; False means already handled."""
    if int(message_id) <= 0:
        return False
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "INSERT OR IGNORE INTO form_decisions (message_id, decided_at) VALUES (?, ?)",
            (int(message_id), int(time.time())),
        )
        await conn.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Persistent security state
# ---------------------------------------------------------------------------

async def set_raid_state(
    guild_id: int,
    raid_until: float,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    if guild_id <= 0:
        raise ValueError("guild_id must be positive")
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "INSERT INTO security_state (guild_id, raid_until, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "raid_until = excluded.raid_until, updated_at = excluded.updated_at",
            (guild_id, float(raid_until), int(time.time())),
        )
        await conn.commit()


async def clear_raid_state(guild_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute("DELETE FROM security_state WHERE guild_id = ?", (guild_id,))
        await conn.commit()


async def get_active_raid_states(
    *,
    now: float | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[int, float]:
    current_time = time.time() if now is None else float(now)
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute("DELETE FROM security_state WHERE raid_until <= ?", (current_time,))
        cursor = await conn.execute(
            "SELECT guild_id, raid_until FROM security_state WHERE raid_until > ?",
            (current_time,),
        )
        rows = await cursor.fetchall()
        await conn.commit()
        return {int(row[0]): float(row[1]) for row in rows}


async def get_security_posture(
    guild_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> dict | None:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT snapshot, snapshot_hash FROM security_posture WHERE guild_id = ?",
        (int(guild_id),),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    snapshot_text = str(row[0] or "")
    expected_hash = str(row[1] or "")
    actual_hash = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        logger.error("Security posture hash mismatch for guild %s", guild_id)
        return None
    try:
        snapshot = json.loads(snapshot_text)
    except json.JSONDecodeError:
        return None
    return snapshot if isinstance(snapshot, dict) else None


async def set_security_posture(
    guild_id: int,
    snapshot: dict,
    db_path: str = DEFAULT_DB_PATH,
) -> str:
    if guild_id <= 0:
        raise ValueError("guild_id must be positive")
    snapshot_text = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(snapshot_text) > 2_000_000:
        raise ValueError("security posture snapshot is too large")
    snapshot_hash = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "INSERT INTO security_posture (guild_id, snapshot, snapshot_hash, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "snapshot = excluded.snapshot, snapshot_hash = excluded.snapshot_hash, "
            "updated_at = excluded.updated_at",
            (int(guild_id), snapshot_text, snapshot_hash, int(time.time())),
        )
        await conn.commit()
    return snapshot_hash


# ---------------------------------------------------------------------------
# AI factual event log
# ---------------------------------------------------------------------------

async def add_ai_event(
    *,
    guild_id: int,
    event_type: str,
    actor_id: int | None = None,
    actor_name: str | None = None,
    target_user_id: int | None = None,
    target_role_id: int | None = None,
    channel_id: int | None = None,
    message_id: int | None = None,
    summary: str = "",
    details: str | dict | list | None = None,
    ts: int | None = None,
    deleted: bool = False,
    recipient_user_ids: list[int] | tuple[int, ...] | set[int] | None = None,
    recipient_role_id: int | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    if details is None:
        details_text = ""
    elif isinstance(details, str):
        details_text = details
    else:
        details_text = json.dumps(details, ensure_ascii=False)
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            """
            INSERT INTO ai_event_log (
                ts, guild_id, event_type, actor_id, actor_name, target_user_id,
                target_role_id, channel_id, message_id, summary, details, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(ts or time.time()),
                int(guild_id),
                str(event_type or "event")[:80],
                actor_id,
                (actor_name or "")[:200],
                target_user_id,
                target_role_id,
                channel_id,
                message_id,
                (summary or "")[:1200],
                details_text[:8000],
                int(bool(deleted)),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an ID for the inserted AI event")
        event_id = cursor.lastrowid
        if recipient_user_ids:
            recipient_rows = [
                (event_id, user_id, recipient_role_id)
                for user_id in dict.fromkeys(int(value) for value in recipient_user_ids)
                if user_id > 0
            ][:50_000]
            if recipient_rows:
                await conn.executemany(
                    "INSERT OR IGNORE INTO ai_event_recipients "
                    "(event_id, user_id, source_role_id) VALUES (?, ?, ?)",
                    recipient_rows,
                )
        await conn.commit()
        return event_id


async def mark_ai_message_deleted(guild_id: int, message_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE ai_event_log SET deleted = 1 WHERE guild_id = ? AND message_id = ?",
            (int(guild_id), int(message_id)),
        )
        await conn.commit()


async def mark_ai_messages_deleted(guild_id: int, message_ids, db_path: str = DEFAULT_DB_PATH) -> None:
    ids = [int(message_id) for message_id in dict.fromkeys(message_ids or [])]
    if not ids:
        return
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.executemany(
            "UPDATE ai_event_log SET deleted = 1 WHERE guild_id = ? AND message_id = ?",
            [(int(guild_id), message_id) for message_id in ids[:10_000]],
        )
        await conn.commit()


async def search_ai_events(
    *,
    guild_id: int | None = None,
    event_type: str | None = None,
    actor_id: int | None = None,
    target_user_id: int | None = None,
    target_role_id: int | None = None,
    recipient_user_id: int | None = None,
    channel_id: int | None = None,
    message_id: int | None = None,
    query: str | None = None,
    limit: int = 25,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    conn = await _get_conn(db_path)
    guild_filter = int(guild_id) if guild_id is not None else None
    event_filter = str(event_type) if event_type else None
    actor_filter = int(actor_id) if actor_id is not None else None
    user_filter = int(target_user_id) if target_user_id is not None else None
    role_filter = int(target_role_id) if target_role_id is not None else None
    recipient_filter = int(recipient_user_id) if recipient_user_id is not None else None
    channel_filter = int(channel_id) if channel_id is not None else None
    message_filter = int(message_id) if message_id is not None else None
    query_text = query.strip() if query else ""
    query_filter = f"%{query_text}%" if query_text else None
    capped_limit = max(1, min(int(limit or 25), 100))
    cursor = await conn.execute(
        """
        SELECT id, ts, guild_id, event_type, actor_id, actor_name, target_user_id,
               target_role_id, channel_id, message_id, summary, details, deleted
        FROM ai_event_log
        WHERE (? IS NULL OR guild_id = ?)
          AND (? IS NULL OR event_type = ?)
          AND (? IS NULL OR actor_id = ?)
          AND (? IS NULL OR target_user_id = ?)
          AND (? IS NULL OR target_role_id = ?)
          AND (
              ? IS NULL OR EXISTS (
                  SELECT 1
                  FROM ai_event_recipients AS recipient
                  WHERE recipient.event_id = ai_event_log.id
                    AND recipient.user_id = ?
              )
          )
          AND (? IS NULL OR channel_id = ?)
          AND (? IS NULL OR message_id = ?)
          AND (? IS NULL OR summary LIKE ? OR details LIKE ? OR actor_name LIKE ?)
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (
            guild_filter,
            guild_filter,
            event_filter,
            event_filter,
            actor_filter,
            actor_filter,
            user_filter,
            user_filter,
            role_filter,
            role_filter,
            recipient_filter,
            recipient_filter,
            channel_filter,
            channel_filter,
            message_filter,
            message_filter,
            query_filter,
            query_filter,
            query_filter,
            query_filter,
            capped_limit,
        ),
    )
    rows = await cursor.fetchall()
    keys = [
        "id", "ts", "guild_id", "event_type", "actor_id", "actor_name",
        "target_user_id", "target_role_id", "channel_id", "message_id",
        "summary", "details", "deleted",
    ]
    return [dict(zip(keys, row)) for row in rows]


# ---------------------------------------------------------------------------
# Structured P.OS tool action journal
# ---------------------------------------------------------------------------

_ACTION_JSON_LIMIT = 20_000
_ACTION_RESULT_LIMIT = 8_000
_ACTION_UNDO_STATUSES = {
    "ready",
    "not_reversible",
    "not_applicable",
    "in_progress",
    "undone",
    "failed",
    "acknowledged",
}


def _bounded_json(value: dict | list | None, *, limit: int = _ACTION_JSON_LIMIT) -> str:
    text = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) > limit:
        raise ValueError("structured action payload is too large")
    return text


def _decode_json_object(value: object) -> dict:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


async def record_pos_tool_action(
    *,
    source_guild_id: int,
    source_channel_id: int | None,
    source_message_id: int,
    actor_id: int,
    target_guild_id: int,
    operation: str,
    args: dict,
    result: str,
    success: bool,
    inverse_operation: str | None = None,
    inverse_args: dict | None = None,
    ts: int | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    if min(source_guild_id, source_message_id, actor_id, target_guild_id) <= 0:
        raise ValueError("action journal IDs must be positive")
    operation_text = str(operation or "").strip()[:100]
    if not operation_text:
        raise ValueError("operation is required")
    inverse_name = str(inverse_operation or "").strip()[:100] or None
    undo_status = (
        "not_applicable"
        if not success
        else "ready" if inverse_name else "not_reversible"
    )
    args_text = _bounded_json(args)
    inverse_text = _bounded_json(inverse_args) if inverse_name else None
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            """
            INSERT INTO pos_tool_actions (
                ts, source_guild_id, source_channel_id, source_message_id,
                actor_id, target_guild_id, operation, args_json, result,
                success, inverse_operation, inverse_args_json, undo_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(ts or time.time()),
                int(source_guild_id),
                int(source_channel_id) if source_channel_id else None,
                int(source_message_id),
                int(actor_id),
                int(target_guild_id),
                operation_text,
                args_text,
                str(result or "")[:_ACTION_RESULT_LIMIT],
                int(bool(success)),
                inverse_name,
                inverse_text,
                undo_status,
            ),
        )
        await conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an action journal ID")
        return int(cursor.lastrowid)


def _action_row_to_dict(row: Any) -> dict:
    keys = [
        "id",
        "ts",
        "source_guild_id",
        "source_channel_id",
        "source_message_id",
        "actor_id",
        "target_guild_id",
        "operation",
        "args_json",
        "result",
        "success",
        "inverse_operation",
        "inverse_args_json",
        "undo_status",
        "undo_started_at",
        "undone_at",
        "undo_result",
    ]
    item = dict(zip(keys, row))
    item["args"] = _decode_json_object(item.pop("args_json"))
    item["inverse_args"] = _decode_json_object(item.pop("inverse_args_json"))
    item["success"] = bool(item["success"])
    return item


async def list_recent_pos_tool_actions(
    *,
    actor_id: int,
    source_guild_id: int,
    source_channel_id: int | None = None,
    limit: int = 12,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    conn = await _get_conn(db_path)
    channel_filter = int(source_channel_id) if source_channel_id else None
    cursor = await conn.execute(
        """
        SELECT id, ts, source_guild_id, source_channel_id, source_message_id,
               actor_id, target_guild_id, operation, args_json, result, success,
               inverse_operation, inverse_args_json, undo_status,
               undo_started_at, undone_at, undo_result
        FROM pos_tool_actions
        WHERE actor_id = ? AND source_guild_id = ?
          AND (? IS NULL OR source_channel_id = ?)
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """,
        (
            int(actor_id),
            int(source_guild_id),
            channel_filter,
            channel_filter,
            max(1, min(int(limit), 50)),
        ),
    )
    return [_action_row_to_dict(row) for row in await cursor.fetchall()]


async def claim_recent_pos_action_group(
    *,
    actor_id: int,
    source_guild_id: int,
    source_channel_id: int | None,
    within_seconds: int = 30 * 60,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Atomically claim the latest not-yet-handled action group for undo."""
    now = int(time.time())
    cutoff = now - max(60, min(int(within_seconds), 24 * 60 * 60))
    stale_cutoff = now - 5 * 60
    channel_filter = int(source_channel_id) if source_channel_id else None
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        # A crashed worker may leave a claim behind. Re-open it after five
        # minutes; normal duplicate requests still fail closed.
        await conn.execute(
            "UPDATE pos_tool_actions SET undo_status = CASE "
            "WHEN inverse_operation IS NULL THEN 'not_reversible' ELSE 'ready' END, "
            "undo_started_at = NULL "
            "WHERE undo_status = 'in_progress' AND undo_started_at < ?",
            (stale_cutoff,),
        )
        cursor = await conn.execute(
            """
            SELECT source_message_id
            FROM pos_tool_actions
            WHERE actor_id = ? AND source_guild_id = ? AND ts >= ?
              AND (? IS NULL OR source_channel_id = ?)
              AND success = 1
              AND undo_status IN ('ready', 'failed', 'not_reversible', 'in_progress')
            ORDER BY ts DESC, id DESC
            LIMIT 1
            """,
            (
                int(actor_id),
                int(source_guild_id),
                cutoff,
                channel_filter,
                channel_filter,
            ),
        )
        group_row = await cursor.fetchone()
        if not group_row:
            await conn.commit()
            return []
        source_message_id = int(group_row[0])
        active_cursor = await conn.execute(
            "SELECT 1 FROM pos_tool_actions WHERE source_message_id = ? "
            "AND actor_id = ? AND undo_status = 'in_progress' LIMIT 1",
            (source_message_id, int(actor_id)),
        )
        if await active_cursor.fetchone():
            await conn.commit()
            return []
        await conn.execute(
            "UPDATE pos_tool_actions SET undo_status = 'in_progress', undo_started_at = ? "
            "WHERE source_message_id = ? AND actor_id = ? "
            "AND success = 1 AND undo_status IN ('ready', 'failed', 'not_reversible')",
            (now, source_message_id, int(actor_id)),
        )
        rows_cursor = await conn.execute(
            """
            SELECT id, ts, source_guild_id, source_channel_id, source_message_id,
                   actor_id, target_guild_id, operation, args_json, result, success,
                   inverse_operation, inverse_args_json, undo_status,
                   undo_started_at, undone_at, undo_result
            FROM pos_tool_actions
            WHERE source_message_id = ? AND actor_id = ? AND success = 1
              AND undo_status = 'in_progress'
            ORDER BY id DESC
            """,
            (source_message_id, int(actor_id)),
        )
        rows = await rows_cursor.fetchall()
        await conn.commit()
    return [_action_row_to_dict(row) for row in rows]


async def finish_pos_action_undo(
    action_id: int,
    *,
    status: str,
    result: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    normalized = str(status or "").strip().lower()
    if normalized not in {"undone", "failed", "acknowledged"}:
        raise ValueError("invalid final undo status")
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE pos_tool_actions SET undo_status = ?, undone_at = ?, "
            "undo_result = ? WHERE id = ? AND undo_status IN "
            "('in_progress', 'not_reversible')",
            (
                normalized,
                int(time.time()),
                str(result or "")[:_ACTION_RESULT_LIMIT],
                int(action_id),
            ),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Telegram owner bridge persistence and abuse controls
# ---------------------------------------------------------------------------

# A reservation normally covers one classification and one HTTP request. After
# an interrupted worker, release its pending slot without re-sending a message
# whose remote delivery may already have succeeded.
_TELEGRAM_RESERVATION_TTL_SECONDS = 10 * 60

async def reserve_telegram_contact_request(
    *,
    guild_id: int,
    channel_id: int,
    discord_message_id: int,
    discord_user_id: int,
    discord_username: str,
    message_text: str,
    urgency: str,
    urgency_reason: str,
    min_interval_seconds: int,
    daily_limit: int,
    global_hourly_limit: int,
    max_pending_per_user: int,
    now: int | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> tuple[dict | None, str | None]:
    timestamp = int(now or time.time())
    clean_text = str(message_text or "").strip()
    if not clean_text:
        return None, "empty"
    payload_hash = hashlib.sha256(clean_text.casefold().encode("utf-8", "replace")).hexdigest()
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE telegram_contact_requests SET status = 'failed', "
            "delivery_error = 'reservation_expired_delivery_unknown' "
            "WHERE status = 'reserved' AND created_at < ?",
            (timestamp - _TELEGRAM_RESERVATION_TTL_SECONDS,),
        )
        await conn.commit()
        duplicate_cursor = await conn.execute(
            "SELECT id, status FROM telegram_contact_requests "
            "WHERE discord_message_id = ?",
            (int(discord_message_id),),
        )
        duplicate = await duplicate_cursor.fetchone()
        if duplicate:
            return None, "already_processed"

        cursor = await conn.execute(
            "SELECT MAX(created_at) FROM telegram_contact_requests "
            "WHERE discord_user_id = ? AND status != 'failed'",
            (int(discord_user_id),),
        )
        row = await cursor.fetchone()
        last_created = int(row[0]) if row and row[0] is not None else 0
        if last_created and timestamp - last_created < max(1, min_interval_seconds):
            return None, f"cooldown:{max(1, min_interval_seconds - (timestamp - last_created))}"

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM telegram_contact_requests "
            "WHERE discord_user_id = ? AND created_at >= ? AND status != 'failed'",
            (int(discord_user_id), timestamp - 24 * 60 * 60),
        )
        count_row = await cursor.fetchone()
        if int(count_row[0] if count_row else 0) >= max(1, daily_limit):
            return None, "daily_limit"

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM telegram_contact_requests "
            "WHERE created_at >= ? AND status != 'failed'",
            (timestamp - 60 * 60,),
        )
        count_row = await cursor.fetchone()
        if int(count_row[0] if count_row else 0) >= max(1, global_hourly_limit):
            return None, "global_limit"

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM telegram_contact_requests "
            "WHERE discord_user_id = ? AND status IN ('reserved', 'sent')",
            (int(discord_user_id),),
        )
        count_row = await cursor.fetchone()
        if int(count_row[0] if count_row else 0) >= max(1, max_pending_per_user):
            return None, "pending_limit"

        cursor = await conn.execute(
            "SELECT id FROM telegram_contact_requests "
            "WHERE discord_user_id = ? AND payload_hash = ? "
            "AND created_at >= ? AND status != 'failed' LIMIT 1",
            (int(discord_user_id), payload_hash, timestamp - 24 * 60 * 60),
        )
        if await cursor.fetchone():
            return None, "duplicate_content"

        insert_cursor = await conn.execute(
            """
            INSERT INTO telegram_contact_requests (
                created_at, guild_id, channel_id, discord_message_id,
                discord_user_id, discord_username, payload_hash, message_text,
                urgency, urgency_reason, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved')
            """,
            (
                timestamp,
                int(guild_id),
                int(channel_id),
                int(discord_message_id),
                int(discord_user_id),
                str(discord_username or discord_user_id)[:200],
                payload_hash,
                clean_text[:1600],
                str(urgency or "normal")[:20],
                str(urgency_reason or "")[:300],
            ),
        )
        await conn.commit()
        if insert_cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a Telegram request ID")
        return {
            "id": int(insert_cursor.lastrowid),
            "created_at": timestamp,
            "guild_id": int(guild_id),
            "channel_id": int(channel_id),
            "discord_message_id": int(discord_message_id),
            "discord_user_id": int(discord_user_id),
            "discord_username": str(discord_username or discord_user_id)[:200],
            "message_text": clean_text[:1600],
            "urgency": str(urgency or "normal")[:20],
            "urgency_reason": str(urgency_reason or "")[:300],
        }, None


async def mark_telegram_contact_sent(
    request_id: int,
    telegram_message_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE telegram_contact_requests SET status = 'sent', "
            "telegram_message_id = ?, delivery_error = NULL "
            "WHERE id = ? AND status = 'reserved'",
            (int(telegram_message_id), int(request_id)),
        )
        await conn.commit()


async def update_telegram_contact_urgency(
    request_id: int,
    *,
    urgency: str,
    urgency_reason: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE telegram_contact_requests SET urgency = ?, urgency_reason = ? "
            "WHERE id = ? AND status = 'reserved'",
            (
                str(urgency or "normal")[:20],
                str(urgency_reason or "")[:300],
                int(request_id),
            ),
        )
        await conn.commit()


async def mark_telegram_contact_failed(
    request_id: int,
    error: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "UPDATE telegram_contact_requests SET status = 'failed', "
            "delivery_error = ? WHERE id = ? AND status = 'reserved'",
            (str(error or "delivery failed")[:500], int(request_id)),
        )
        await conn.commit()


async def get_telegram_contact_by_message(
    telegram_message_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> dict | None:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        """
        SELECT id, created_at, guild_id, channel_id, discord_message_id,
               discord_user_id, discord_username, message_text, urgency,
               urgency_reason, status, telegram_message_id, response_text,
               responded_at, delivery_target, delivery_error
        FROM telegram_contact_requests
        WHERE telegram_message_id = ?
        """,
        (int(telegram_message_id),),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    keys = [
        "id", "created_at", "guild_id", "channel_id", "discord_message_id",
        "discord_user_id", "discord_username", "message_text", "urgency",
        "urgency_reason", "status", "telegram_message_id", "response_text",
        "responded_at", "delivery_target", "delivery_error",
    ]
    return dict(zip(keys, row))


async def claim_telegram_contact_response(
    request_id: int,
    *,
    response_text: str,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Claim one owner reply before Discord delivery to suppress duplicates."""
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "UPDATE telegram_contact_requests SET status = 'responding', "
            "response_text = ?, responded_at = ?, delivery_error = NULL "
            "WHERE id = ? AND status = 'sent'",
            (
                str(response_text or "")[:2000],
                int(time.time()),
                int(request_id),
            ),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def complete_telegram_contact_response(
    request_id: int,
    *,
    response_text: str,
    delivery_target: str,
    delivery_error: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        cursor = await conn.execute(
            "UPDATE telegram_contact_requests SET status = ?, response_text = ?, "
            "responded_at = ?, delivery_target = ?, delivery_error = ? "
            "WHERE id = ? AND status = 'responding'",
            (
                "delivered" if not delivery_error else "delivery_failed",
                str(response_text or "")[:2000],
                int(time.time()),
                str(delivery_target or "")[:40],
                str(delivery_error or "")[:500] or None,
                int(request_id),
            ),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_integration_state(
    key: str,
    db_path: str = DEFAULT_DB_PATH,
) -> str | None:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "SELECT value FROM integration_state WHERE key = ?",
        (str(key)[:100],),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row else None


async def set_integration_state(
    key: str,
    value: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    async with _loop_lock(_write_locks):
        conn = await _get_conn(db_path)
        await conn.execute(
            "INSERT INTO integration_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (str(key)[:100], str(value)[:2000], int(time.time())),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Backup / Restore database using a Discord channel as persistent storage
# ---------------------------------------------------------------------------
# #13: Бэкап базы идёт в отдельный канал, НЕ совпадающий с логами модерации.
# Если переменная среды не задана — бэкап отключен и данные хранятся только локально.
def _safe_env_int(name: str, default: int = 0) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid integer in %s; using %s.", name, default)
        return default


BACKUP_CHANNEL_ID = _safe_env_int("DB_BACKUP_CHANNEL_ID") or 0
_SQLITE_MAGIC = b"SQLite format 3"  # первые 15 байт любого валидного файла SQLite
_BACKUP_MARKER = "[DATABASE_BACKUP]"
_BACKUP_PART_MARKER = "[DATABASE_BACKUP_PART]"
_BACKUP_MANIFEST_FILENAME = "bot_data.db.manifest.json"
_BACKUP_HASH_RE = re.compile(r"\bsha256=([0-9a-f]{64})\b", re.IGNORECASE)
_MAX_BACKUP_BYTES = 100 * 1024 * 1024
_MAX_BACKUP_PARTS = 32
_MAX_BACKUP_MANIFEST_BYTES = 32 * 1024
_DEFAULT_BACKUP_UPLOAD_BYTES = 10 * 1024 * 1024
_BACKUP_HISTORY_LIMIT = 1000
# Сколько последних бэкапов держать в канале (бэкап каждые 10 минут копится вечно
# и захламляет канал — старые сообщения подчищаем после успешной загрузки).
_BACKUP_KEEP_LAST = 50
_backup_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()
# Если канал бэкапов настроен, upload запрещён до безопасного restore/no-backup
# результата. Это защищает не только loop, но и shutdown/P.OS shutdown пути.
_backup_uploads_allowed = not bool(BACKUP_CHANNEL_ID)
_backup_disabled_warning_emitted = False


def _warn_backup_disabled_once() -> None:
    global _backup_disabled_warning_emitted
    if _backup_disabled_warning_emitted:
        return
    _backup_disabled_warning_emitted = True
    logger.warning(
        "Резервное копирование базы отключено: DB_BACKUP_CHANNEL_ID не настроен. "
        "SQLite остаётся только на локальном диске и может быть потеряна при перезапуске "
        "хостинга с временным диском."
    )


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compress_file(source_path: str, destination_path: str) -> None:
    with open(source_path, "rb") as source, open(destination_path, "wb") as output:
        with gzip.GzipFile(
            filename="bot_data.db",
            mode="wb",
            fileobj=output,
            compresslevel=6,
            mtime=0,
        ) as compressed:
            shutil.copyfileobj(source, compressed, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _write_restore_payload(path: str, raw: bytes, *, compressed: bool) -> int:
    total = 0
    with open(path, "wb") as output:
        if compressed:
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_BACKUP_BYTES:
                        raise ValueError("decompressed database exceeds the size limit")
                    output.write(chunk)
        else:
            total = len(raw)
            if total > _MAX_BACKUP_BYTES:
                raise ValueError("database exceeds the size limit")
            output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return total


def _sqlite_quick_check(path: str) -> tuple[bool, str]:
    try:
        uri = f"file:{os.path.abspath(path)}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rows = connection.execute("PRAGMA quick_check").fetchall()
            if rows == [("ok",)]:
                return True, "ok"
            details = "; ".join(str(row[0]) for row in rows[:5]) or "empty quick_check result"
            return False, details
        finally:
            connection.close()
    except Exception as exc:
        return False, str(exc)


async def _create_consistent_snapshot(db_path: str = DEFAULT_DB_PATH) -> str | None:
    """Сделать целостный снапшот БД через VACUUM INTO.

    Копирование живого файла при активном WAL может дать несогласованную базу.
    VACUUM INTO создаёт согласованную копию даже во время записи.
    Возвращает путь к временному файлу-снапшоту (его нужно удалить после загрузки).
    """
    snapshot_path = f"{db_path}.backup"
    async with _loop_lock(_write_locks):
        try:
            if os.path.exists(snapshot_path):
                os.remove(snapshot_path)
        except OSError:
            pass
        try:
            conn = await _get_conn(db_path)
            # Параметризовать имя файла в VACUUM INTO нельзя — экранируем кавычки вручную.
            safe_path = snapshot_path.replace("'", "''")
            await conn.execute(f"VACUUM INTO '{safe_path}'")
            return snapshot_path
        except Exception:
            logger.exception("Не удалось создать согласованный снапшот БД.")
            return None


async def _resolve_backup_channel(bot: discord.Client) -> discord.TextChannel | None:
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
        except Exception:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


def _parse_backup_manifest(raw: bytes) -> dict[str, Any]:
    """Validate bounded metadata before fetching any referenced Discord messages."""
    if not raw or len(raw) > _MAX_BACKUP_MANIFEST_BYTES:
        raise ValueError("invalid backup manifest size")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("encoding") != "gzip":
        raise ValueError("unsupported backup manifest")
    for key in ("size", "original_size"):
        value = manifest.get(key)
        if type(value) is not int or not 0 < value <= _MAX_BACKUP_BYTES:
            raise ValueError(f"invalid manifest {key}")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid manifest SHA-256")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= _MAX_BACKUP_PARTS:
        raise ValueError("invalid backup part count")
    message_ids: set[int] = set()
    total_size = 0
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            raise ValueError("invalid backup part metadata")
        message_id = part.get("message_id")
        if type(message_id) is not int or message_id <= 0 or message_id in message_ids:
            raise ValueError("invalid or duplicate backup part message ID")
        message_ids.add(message_id)
        if part.get("filename") != f"bot_data.db.gz.part-{index:04d}":
            raise ValueError("invalid backup part order or filename")
        part_size = part.get("size")
        if type(part_size) is not int or not 0 < part_size <= _MAX_BACKUP_BYTES:
            raise ValueError("invalid backup part size")
        part_hash = part.get("sha256")
        if not isinstance(part_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", part_hash):
            raise ValueError("invalid backup part SHA-256")
        total_size += part_size
    if total_size != manifest["size"]:
        raise ValueError("backup parts do not match manifest size")
    return manifest


async def _assemble_backup_parts(
    channel: discord.TextChannel, manifest: dict[str, Any], bot_user_id: int,
) -> bytes:
    payload = bytearray()
    for part in manifest["parts"]:
        message = await channel.fetch_message(part["message_id"])
        if message.author.id != bot_user_id or not str(message.content or "").startswith(_BACKUP_PART_MARKER):
            raise ValueError("backup part is not an authenticated bot upload")
        if len(message.attachments) != 1:
            raise ValueError("backup part attachment missing or ambiguous")
        attachment = message.attachments[0]
        if attachment.filename != part["filename"] or attachment.size != part["size"]:
            raise ValueError("backup part attachment metadata mismatch")
        raw = await attachment.read()
        if len(raw) != part["size"] or hashlib.sha256(raw).hexdigest() != part["sha256"]:
            raise ValueError("backup part size or SHA-256 mismatch")
        payload.extend(raw)
    if len(payload) != manifest["size"] or hashlib.sha256(payload).hexdigest() != manifest["sha256"]:
        raise ValueError("assembled backup size or SHA-256 mismatch")
    return bytes(payload)


async def _upload_multipart_backup(
    channel: discord.TextChannel, compressed_path: str, *, upload_limit: int,
    compressed_size: int, snapshot_size: int, snapshot_hash: str,
) -> None:
    """Publish the manifest last: interrupted part uploads are never restore points."""
    part_count = (compressed_size + upload_limit - 1) // upload_limit
    if part_count > _MAX_BACKUP_PARTS:
        raise ValueError("Discord upload limit requires too many backup parts")
    manifest: dict[str, Any] = {
        "version": 1, "encoding": "gzip", "size": compressed_size,
        "original_size": snapshot_size, "sha256": snapshot_hash, "parts": [],
    }
    uploaded: list[discord.Message] = []
    committed = False
    try:
        with open(compressed_path, "rb") as source:
            for index in range(1, part_count + 1):
                raw = source.read(upload_limit)
                filename = f"bot_data.db.gz.part-{index:04d}"
                file = discord.File(io.BytesIO(raw), filename=filename)
                try:
                    message = await channel.send(
                        content=f"{_BACKUP_PART_MARKER} sha256={snapshot_hash} part={index}/{part_count}",
                        file=file,
                    )
                finally:
                    file.close()
                uploaded.append(message)
                manifest["parts"].append({
                    "message_id": message.id, "filename": filename, "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                })
        manifest_raw = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        _parse_backup_manifest(manifest_raw)
        if len(manifest_raw) > upload_limit:
            raise ValueError("backup manifest exceeds Discord upload limit")
        file = discord.File(io.BytesIO(manifest_raw), filename=_BACKUP_MANIFEST_FILENAME)
        try:
            await channel.send(
                content=(f"{_BACKUP_MARKER} encoding=manifest-v1 "
                         f"sha256={hashlib.sha256(manifest_raw).hexdigest()} parts={part_count}"),
                file=file,
            )
            committed = True
        finally:
            file.close()
    finally:
        if not committed:
            # Only messages created by this attempt are disposable. Prior backups
            # remain intact even when a part/manifest upload fails or is cancelled.
            for message in uploaded:
                try:
                    await message.delete()
                except Exception:
                    logger.warning("Could not remove incomplete database backup part %s.", message.id)


async def backup_db_to_discord(bot: discord.Client, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Upload a consistent snapshot of the DB to the dedicated backup channel."""
    async with _loop_lock(_backup_locks):
        # #13: Не делаем бэкап, если канал не настроен
        if not BACKUP_CHANNEL_ID:
            _warn_backup_disabled_once()
            return False
        if not _backup_uploads_allowed:
            logger.warning("Database backup blocked: restore has not completed safely.")
            return False
        channel = await _resolve_backup_channel(bot)
        if not channel:
            logger.warning(f"Database backup failed: channel {BACKUP_CHANNEL_ID} not found.")
            return False

        if not os.path.exists(db_path):
            logger.warning(f"Database backup failed: {db_path} does not exist.")
            return False

        snapshot_path = await _create_consistent_snapshot(db_path)
        if not snapshot_path or not os.path.exists(snapshot_path):
            logger.warning("Database backup failed: snapshot was not created.")
            return False

        compressed_path = f"{snapshot_path}.gz"
        try:
            snapshot_size = os.path.getsize(snapshot_path)
            if snapshot_size <= 0 or snapshot_size > _MAX_BACKUP_BYTES:
                logger.error("Database backup rejected: invalid snapshot size %s bytes.", snapshot_size)
                return False
            await asyncio.to_thread(_compress_file, snapshot_path, compressed_path)
            compressed_size = os.path.getsize(compressed_path)
            if compressed_size <= 0 or compressed_size > _MAX_BACKUP_BYTES:
                logger.error("Database backup rejected: invalid compressed size %s bytes.", compressed_size)
                return False
            upload_limit = int(getattr(getattr(channel, "guild", None), "filesize_limit", 0) or 0)
            upload_limit = min(upload_limit or _DEFAULT_BACKUP_UPLOAD_BYTES, _DEFAULT_BACKUP_UPLOAD_BYTES)
            snapshot_hash = await asyncio.to_thread(_sha256_file, compressed_path)
            if compressed_size > upload_limit:
                await _upload_multipart_backup(
                    channel, compressed_path, upload_limit=upload_limit,
                    compressed_size=compressed_size, snapshot_size=snapshot_size,
                    snapshot_hash=snapshot_hash,
                )
            else:
                file = discord.File(compressed_path, filename="bot_data.db.gz")
                try:
                    await channel.send(
                        content=(
                            f"{_BACKUP_MARKER} Automatic database backup encoding=gzip "
                            f"sha256={snapshot_hash} size={compressed_size} "
                            f"original_size={snapshot_size}"
                        ),
                        file=file,
                    )
                finally:
                    file.close()
            logger.info("Database backup uploaded to Discord successfully.")
            try:
                await _prune_old_backups(channel, bot)
            except Exception as e:
                logger.warning(f"Не удалось подчистить старые бэкапы: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload database backup to Discord: {e}")
            return False
        finally:
            for temporary_path in (snapshot_path, compressed_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass


async def _prune_old_backups(channel: discord.TextChannel, bot: discord.Client) -> None:
    """Удалить старые бэкап-сообщения бота, оставив последние _BACKUP_KEEP_LAST."""
    bot_user_id = bot.user.id if bot.user else None
    if bot_user_id is None:
        return
    backups: list[discord.Message] = []
    async for msg in channel.history(limit=_BACKUP_HISTORY_LIMIT):
        if msg.author.id != bot_user_id:
            continue
        if not msg.content.startswith(_BACKUP_MARKER):
            continue
        backups.append(msg)
    # history отдаёт от новых к старым — всё после первых _BACKUP_KEEP_LAST удаляем.
    for msg in backups[_BACKUP_KEEP_LAST:]:
        try:
            part_ids: list[int] = []
            if msg.attachments and msg.attachments[0].filename == _BACKUP_MANIFEST_FILENAME:
                attachment = msg.attachments[0]
                if attachment.size > _MAX_BACKUP_MANIFEST_BYTES:
                    continue
                manifest = _parse_backup_manifest(await attachment.read())
                part_ids = [part["message_id"] for part in manifest["parts"]]
            await msg.delete()
            for message_id in part_ids:
                part_message = await channel.fetch_message(message_id)
                if part_message.author.id == bot_user_id and str(part_message.content or "").startswith(_BACKUP_PART_MARKER):
                    await part_message.delete()
        except Exception:
            pass


async def restore_db_from_discord(bot: discord.Client, db_path: str = DEFAULT_DB_PATH) -> bool | None:
    """Scan the backup channel for the latest backup uploaded BY THE BOT and restore it.

    Return values:
    - True: backup restored.
    - False: restore is intentionally skipped or no backup exists.
    - None: restore was configured but failed/was unsafe; callers must not upload
      a fresh backup over the last known good copy yet.
    """
    global _backup_uploads_allowed
    async with _loop_lock(_backup_locks):
        if not BACKUP_CHANNEL_ID:
            _backup_uploads_allowed = True
            _warn_backup_disabled_once()
            return False
        channel = await _resolve_backup_channel(bot)
        if not channel:
            _backup_uploads_allowed = False
            logger.warning("Database restore failed: channel %s not found.", BACKUP_CHANNEL_ID)
            return None

        bot_user_id = bot.user.id if bot.user else None
        if bot_user_id is None:
            _backup_uploads_allowed = False
            logger.error("Database restore attempted before Discord login completed.")
            return None

        saw_candidate = False
        rejected_candidates: list[str] = []
        try:
            async for msg in channel.history(limit=_BACKUP_HISTORY_LIMIT):
                if msg.author.id != bot_user_id:
                    continue
                content = str(msg.content or "")
                if content.startswith(_BACKUP_PART_MARKER):
                    # A process may stop before publishing its manifest. With no
                    # older complete backup, this is unsafe rather than an empty channel.
                    saw_candidate = True
                    continue
                if not content.startswith(_BACKUP_MARKER) or not msg.attachments:
                    continue
                att = msg.attachments[0]
                if att.filename not in {"bot_data.db", "bot_data.db.gz", _BACKUP_MANIFEST_FILENAME}:
                    continue
                saw_candidate = True
                candidate_label = f"message={msg.id}"

                try:
                    is_manifest = att.filename == _BACKUP_MANIFEST_FILENAME
                    size_limit = _MAX_BACKUP_MANIFEST_BYTES if is_manifest else _MAX_BACKUP_BYTES
                    if att.size and att.size > size_limit:
                        raise ValueError(f"file too large: {att.size} bytes")
                    raw = await att.read()
                    if not raw or len(raw) > size_limit:
                        raise ValueError(f"invalid downloaded size: {len(raw)} bytes")
                    expected_match = _BACKUP_HASH_RE.search(content)
                    if "sha256=" in content.lower() and not expected_match:
                        raise ValueError("malformed SHA-256 metadata")
                    actual_hash = hashlib.sha256(raw).hexdigest()
                    if expected_match and actual_hash.lower() != expected_match.group(1).lower():
                        raise ValueError("SHA-256 mismatch")

                    manifest = _parse_backup_manifest(raw) if is_manifest else None
                    if manifest is not None:
                        if not expected_match:
                            raise ValueError("backup manifest SHA-256 metadata missing")
                        raw = await _assemble_backup_parts(channel, manifest, bot_user_id)
                    compressed = is_manifest or att.filename == "bot_data.db.gz"
                    if not compressed and not raw.startswith(_SQLITE_MAGIC):
                        raise ValueError("SQLite magic bytes missing")

                    db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
                    fd, temp_path = tempfile.mkstemp(prefix=".pos-restore-", suffix=".db", dir=db_dir)
                    os.close(fd)
                    try:
                        restored_size = await asyncio.to_thread(
                            _write_restore_payload,
                            temp_path,
                            raw,
                            compressed=compressed,
                        )
                        if restored_size <= 0:
                            raise ValueError("restored database is empty")
                        if manifest is not None and restored_size != manifest["original_size"]:
                            raise ValueError("restored database size does not match manifest")
                        with open(temp_path, "rb") as restored_file:
                            if not restored_file.read(len(_SQLITE_MAGIC)).startswith(_SQLITE_MAGIC):
                                raise ValueError("SQLite magic bytes missing")
                        valid, check_details = await asyncio.to_thread(_sqlite_quick_check, temp_path)
                        if not valid:
                            raise ValueError(f"SQLite quick_check failed: {check_details}")

                        await close_all_connections()
                        for suffix in ("-wal", "-shm"):
                            side = f"{db_path}{suffix}"
                            if os.path.exists(side):
                                try:
                                    os.remove(side)
                                except OSError:
                                    pass

                        rollback_path = f"{db_path}.restore-rollback-{os.getpid()}-{time.time_ns()}"
                        had_original = os.path.exists(db_path)
                        if had_original:
                            os.replace(db_path, rollback_path)
                        try:
                            os.replace(temp_path, db_path)
                            temp_path = ""
                            await init_db(db_path)
                        except Exception:
                            await close_all_connections()
                            for suffix in ("-wal", "-shm"):
                                side = f"{db_path}{suffix}"
                                if os.path.exists(side):
                                    try:
                                        os.remove(side)
                                    except OSError:
                                        pass
                            if had_original and os.path.exists(rollback_path):
                                os.replace(rollback_path, db_path)
                                await init_db(db_path)
                            elif not had_original and os.path.exists(db_path):
                                try:
                                    os.remove(db_path)
                                except OSError:
                                    pass
                            raise
                        else:
                            if had_original and os.path.exists(rollback_path):
                                try:
                                    os.remove(rollback_path)
                                except OSError:
                                    pass

                        _backup_uploads_allowed = True
                        if not expected_match:
                            logger.warning("Restored legacy database backup without SHA-256 metadata (%s).", candidate_label)
                        logger.info("Database successfully restored from Discord backup (%s).", candidate_label)
                        return True
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except OSError:
                                pass
                except Exception as exc:
                    reason = f"{candidate_label}: {exc}"
                    rejected_candidates.append(reason)
                    logger.warning("Skipping invalid database backup %s", reason)
                    continue

            if saw_candidate:
                _backup_uploads_allowed = False
                logger.error(
                    "No valid database backup found; rejected %s candidate(s).",
                    len(rejected_candidates),
                )
                return None

            logger.info("No database backup found in history.")
            _backup_uploads_allowed = True
            return False
        except Exception as exc:
            _backup_uploads_allowed = False
            logger.error("Failed to restore database from Discord: %s", exc, exc_info=True)
            return None
