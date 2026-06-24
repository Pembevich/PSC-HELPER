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
import logging
import discord
import aiosqlite

DEFAULT_DB_PATH = "bot_data.db"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Единое соединение на путь к БД
# ---------------------------------------------------------------------------
_connections: dict[str, aiosqlite.Connection] = {}
_conn_lock = asyncio.Lock()


async def _get_conn(db_path: str = DEFAULT_DB_PATH) -> aiosqlite.Connection:
    """Вернуть (создав при необходимости) общее соединение для db_path."""
    conn = _connections.get(db_path)
    if conn is not None:
        return conn
    async with _conn_lock:
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
    async with _conn_lock:
        for path, conn in list(_connections.items()):
            try:
                await conn.close()
            except Exception as exc:
                logger.warning(f"Не удалось закрыть соединение {path}: {exc}")
            _connections.pop(path, None)


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
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
    # Чистим устаревшие таблицы со старых версий (plaintext-пароли и т.п.).
    await conn.execute("DROP TABLE IF EXISTS private_chats")
    await conn.execute("DROP TABLE IF EXISTS chat_messages")
    await conn.commit()


# ---------------------------------------------------------------------------
# entries helpers
# ---------------------------------------------------------------------------

async def add_entry(title: str, description: str, db_path: str = DEFAULT_DB_PATH) -> int:
    conn = await _get_conn(db_path)
    cursor = await conn.execute(
        "INSERT INTO entries (title, description) VALUES (?, ?)",
        ((title or "").strip(), (description or "").strip()),
    )
    await conn.commit()
    return int(cursor.lastrowid)


async def delete_entry(entry_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
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
    """Upsert the muted status of a user for P.OS on a guild."""
    conn = await _get_conn(db_path)
    await conn.execute(
        "INSERT INTO ai_muted (user_id, guild_id, muted) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, guild_id) DO UPDATE SET muted = excluded.muted",
        (user_id, guild_id, int(muted)),
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
    conn = await _get_conn(db_path)
    await conn.execute(
        "INSERT INTO guild_settings (guild_id, settings) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET settings = excluded.settings",
        (guild_id, settings_json),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Backup / Restore database using a Discord channel as persistent storage
# ---------------------------------------------------------------------------
# #13: Бэкап базы идёт в отдельный канал, НЕ совпадающий с логами модерации.
# Если переменная среды не задана — бэкап отключен и данные хранятся только локально.
BACKUP_CHANNEL_ID = int(os.getenv("DB_BACKUP_CHANNEL_ID", "0")) or 0
_SQLITE_MAGIC = b"SQLite format 3"  # первые 15 байт любого валидного файла SQLite
_BACKUP_MARKER = "[DATABASE_BACKUP]"
_MAX_BACKUP_BYTES = 100 * 1024 * 1024


async def _create_consistent_snapshot(db_path: str = DEFAULT_DB_PATH) -> str | None:
    """Сделать целостный снапшот БД через VACUUM INTO.

    Копирование живого файла при активном WAL может дать несогласованную базу.
    VACUUM INTO создаёт согласованную копию даже во время записи.
    Возвращает путь к временному файлу-снапшоту (его нужно удалить после загрузки).
    """
    snapshot_path = f"{db_path}.backup"
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
    except Exception as exc:
        logger.error(f"Не удалось создать снапшот БД: {exc}")
        return None


async def _resolve_backup_channel(bot: discord.Client) -> discord.TextChannel | None:
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    if not channel:
        try:
            channel = await bot.fetch_channel(BACKUP_CHANNEL_ID)
        except Exception:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


async def backup_db_to_discord(bot: discord.Client, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Upload a consistent snapshot of the DB to the dedicated backup channel."""
    # #13: Не делаем бэкап, если канал не настроен
    if not BACKUP_CHANNEL_ID:
        logger.info("Database backup skipped: DB_BACKUP_CHANNEL_ID not configured.")
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

    try:
        file = discord.File(snapshot_path, filename="bot_data.db")
        await channel.send(content=f"{_BACKUP_MARKER} Automatic database backup", file=file)
        logger.info("Database backup uploaded to Discord successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to upload database backup to Discord: {e}")
        return False
    finally:
        try:
            os.remove(snapshot_path)
        except OSError:
            pass


async def restore_db_from_discord(bot: discord.Client, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Scan the backup channel for the latest backup uploaded BY THE BOT and restore it."""
    # #13: Не выполняем восстановление, если канал не настроен
    if not BACKUP_CHANNEL_ID:
        logger.info("Database restore skipped: DB_BACKUP_CHANNEL_ID not configured.")
        return False
    channel = await _resolve_backup_channel(bot)
    if not channel:
        logger.warning(f"Database restore failed: channel {BACKUP_CHANNEL_ID} not found.")
        return False

    bot_user_id = bot.user.id if bot.user else None

    try:
        async for msg in channel.history(limit=50):
            # #1: доверяем ТОЛЬКО бэкапам, которые отправил сам бот.
            if bot_user_id is None or msg.author.id != bot_user_id:
                continue
            if not msg.content.startswith(_BACKUP_MARKER) or not msg.attachments:
                continue
            att = msg.attachments[0]
            if att.filename != "bot_data.db":
                continue
            # #3: Проверяем максимальный размер (100 МБ) и magic bytes SQLite
            if att.size and att.size > _MAX_BACKUP_BYTES:
                logger.warning(f"Database restore aborted: backup file too large ({att.size} bytes).")
                return False
            raw = await att.read()
            if not raw.startswith(_SQLITE_MAGIC):
                logger.warning("Database restore aborted: backup file is not a valid SQLite database.")
                return False
            # Закрываем активные соединения перед перезаписью файла на диске.
            await close_all_connections()
            # Сносим WAL/SHM, иначе старый журнал может перекрыть восстановленные данные.
            for suffix in ("-wal", "-shm"):
                side = f"{db_path}{suffix}"
                if os.path.exists(side):
                    try:
                        os.remove(side)
                    except OSError:
                        pass
            with open(db_path, "wb") as f:
                f.write(raw)
            logger.info("Database successfully restored from Discord backup.")
            return True
        logger.info("No database backup found in history.")
        return False
    except Exception as e:
        logger.error(f"Failed to restore database from Discord: {e}")
        return False
