import aiosqlite


DEFAULT_DB_PATH = "bot_data.db"


async def init_db(db_path: str = "bot_data.db") -> None:
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
            CREATE TABLE IF NOT EXISTS ai_contexts (
                user_id INTEGER,
                guild_id INTEGER,
                json_data TEXT,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_muted_users (
                user_id INTEGER,
                guild_id INTEGER,
                PRIMARY KEY (user_id, guild_id)
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


async def get_ai_context(user_id: int, guild_id: int, db_path: str = DEFAULT_DB_PATH) -> str | None:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT json_data FROM ai_contexts WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def update_ai_context(user_id: int, guild_id: int, json_data: str, db_path: str = DEFAULT_DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO ai_contexts (user_id, guild_id, json_data) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, guild_id) DO UPDATE SET json_data=excluded.json_data",
            (user_id, guild_id, json_data)
        )
        await conn.commit()


async def is_ai_muted(user_id: int, guild_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM ai_muted_users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        row = await cursor.fetchone()
        return row is not None


async def set_ai_muted_user(user_id: int, guild_id: int, is_muted: bool, db_path: str = DEFAULT_DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as conn:
        if is_muted:
            await conn.execute(
                "INSERT OR IGNORE INTO ai_muted_users (user_id, guild_id) VALUES (?, ?)",
                (user_id, guild_id)
            )
        else:
            await conn.execute(
                "DELETE FROM ai_muted_users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id)
            )
        await conn.commit()
