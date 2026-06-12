"""
storage.py — async SQLite через aiosqlite.
Синхронный sqlite3 блокировал event loop при каждом запросе.
"""
from __future__ import annotations

import os
import logging
import discord
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


# ---------------------------------------------------------------------------
# Backup / Restore database using a Discord channel as persistent storage
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
# #13: Бэкап базы идёт в отдельный канал, НЕ совпадающий с логами модерации.
# Если переменная среды не задана — бэкап отключен и данные хранятся только локально.
BACKUP_CHANNEL_ID = int(os.getenv("DB_BACKUP_CHANNEL_ID", "0")) or 0
_SQLITE_MAGIC = b"SQLite format 3"  # первые 15 байт любого валидного файла SQLite

async def backup_db_to_discord(bot: discord.Client) -> bool:
    """Upload bot_data.db to the dedicated DB backup channel (DB_BACKUP_CHANNEL_ID env var)."""
    # #13: Не делаем бэкап, если канал не настроен
    if not BACKUP_CHANNEL_ID:
        logger.info("Database backup skipped: DB_BACKUP_CHANNEL_ID not configured.")
        return False
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
        except Exception:
            pass
    if not isinstance(channel, discord.TextChannel):
        logger.warning(f"Database backup failed: channel {BACKUP_CHANNEL_ID} not found.")
        return False

    if not os.path.exists(DEFAULT_DB_PATH):
        logger.warning(f"Database backup failed: {DEFAULT_DB_PATH} does not exist.")
        return False

    try:
        file = discord.File(DEFAULT_DB_PATH, filename="bot_data.db")
        await channel.send(content="[DATABASE_BACKUP] Automatic database backup", file=file)
        logger.info("Database backup uploaded to Discord successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to upload database backup to Discord: {e}")
        return False

async def restore_db_from_discord(bot: discord.Client) -> bool:
    """Scan the backup channel for the latest database backup and restore it."""
    # #13: Не выполняем восстановление, если канал не настроен
    if not BACKUP_CHANNEL_ID:
        logger.info("Database restore skipped: DB_BACKUP_CHANNEL_ID not configured.")
        return False
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
        except Exception:
            pass
    if not isinstance(channel, discord.TextChannel):
        logger.warning(f"Database restore failed: channel {BACKUP_CHANNEL_ID} not found.")
        return False

    try:
        async for msg in channel.history(limit=50):
            if msg.content.startswith("[DATABASE_BACKUP]") and msg.attachments:
                att = msg.attachments[0]
                if att.filename == "bot_data.db":
                    # #3: Проверяем максимальный размер (100 МБ) и magic bytes SQLite
                    if att.size and att.size > 100 * 1024 * 1024:
                        logger.warning(f"Database restore aborted: backup file too large ({att.size} bytes).")
                        return False
                    raw = await att.read()
                    if not raw.startswith(_SQLITE_MAGIC):
                        logger.warning("Database restore aborted: backup file does not look like a valid SQLite database.")
                        return False
                    # Всё проверено — записываем
                    with open(DEFAULT_DB_PATH, "wb") as f:
                        f.write(raw)
                    logger.info("Database successfully restored from Discord backup.")
                    return True
        logger.info("No database backup found in history.")
        return False
    except Exception as e:
        logger.error(f"Failed to restore database from Discord: {e}")
        return False
