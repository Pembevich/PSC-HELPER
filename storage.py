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
        await conn.commit()


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
