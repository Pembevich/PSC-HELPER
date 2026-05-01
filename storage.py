"""
storage.py — async SQLite через aiosqlite.
Синхронный sqlite3 блокировал event loop при каждом запросе.
"""
from __future__ import annotations

import aiosqlite

DEFAULT_DB_PATH = "bot_data.db"


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS private_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER,
                user2_id INTEGER,
                password TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                sender_id INTEGER,
                message TEXT,
                file BLOB,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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
        await conn.commit()


# ---------------------------------------------------------------------------
# entries helpers
# ---------------------------------------------------------------------------

async def add_entry(title: str, description: str, db_path: str = DEFAULT_DB_PATH) -> int:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "INSERT INTO entries (title, description) VALUES (?, ?)",
            ((title or "").strip(), (description or "").strip()),
        )
        await conn.commit()
        return int(cursor.lastrowid)


async def delete_entry(entry_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def list_entries(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> list[tuple[int, str, str]]:
    async with aiosqlite.connect(db_path) as conn:
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
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT context FROM ai_context WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        return str(row[0]) if row and row[0] is not None else None


async def update_ai_context(user_id: int, guild_id: int, context: str, db_path: str = DEFAULT_DB_PATH) -> None:
    """Upsert AI context JSON string for (user_id, guild_id)."""
    async with aiosqlite.connect(db_path) as conn:
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
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT muted FROM ai_muted WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        return bool(row and row[0])


async def set_ai_muted_user(user_id: int, guild_id: int, muted: bool, db_path: str = DEFAULT_DB_PATH) -> None:
    """Upsert the muted status of a user for P.OS on a guild."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO ai_muted (user_id, guild_id, muted) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET muted = excluded.muted",
            (user_id, guild_id, int(muted)),
        )
        await conn.commit()
