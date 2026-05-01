import sqlite3


DEFAULT_DB_PATH = "bot_data.db"


def init_db(db_path: str = "bot_data.db") -> None:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS private_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER,
                user2_id INTEGER,
                password TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                sender_id INTEGER,
                message TEXT,
                file BLOB,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def add_entry(title: str, description: str, db_path: str = DEFAULT_DB_PATH) -> int:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO entries (title, description) VALUES (?, ?)",
            ((title or "").strip(), (description or "").strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def delete_entry(entry_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.commit()
        return cursor.rowcount > 0


def list_entries(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> list[tuple[int, str, str]]:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, description FROM entries ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        )
        return [(int(row[0]), str(row[1] or ""), str(row[2] or "")) for row in cursor.fetchall()]
