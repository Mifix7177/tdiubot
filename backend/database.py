import aiosqlite
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from backend.config import DATABASE_PATH, ADMIN_IDS

async def get_db():
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        
        # Buttons and Menus table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS buttons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_key TEXT NOT NULL,
            parent_menu TEXT NOT NULL,
            label_uz TEXT NOT NULL,
            label_ru TEXT NOT NULL,
            label_en TEXT NOT NULL,
            emoji TEXT DEFAULT '',
            position INTEGER DEFAULT 0,
            row_index INTEGER DEFAULT 0,
            button_type TEXT NOT NULL,
            required_role TEXT DEFAULT 'all',
            is_enabled INTEGER DEFAULT 1,
            guest_access INTEGER DEFAULT 1,
            student_access INTEGER DEFAULT 1,
            guest_limit INTEGER DEFAULT 0,
            student_limit INTEGER DEFAULT 0,
            action_payload TEXT DEFAULT ''
        )
        """)

        # Users table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'student',
            language TEXT DEFAULT 'ru',
            language_selected INTEGER DEFAULT 0,
            student_id TEXT,
            faculty TEXT,
            group_name TEXT,
            course INTEGER DEFAULT 1,
            account_status TEXT DEFAULT 'Active',
            ai_queries_today INTEGER DEFAULT 0,
            last_active TEXT,
            created_at TEXT
        )
        """)

        # Migration check for language_selected
        try:
            await db.execute("ALTER TABLE users ADD COLUMN language_selected INTEGER DEFAULT 0")
        except Exception:
            pass

        # Messages (Help, Confessions, Normal)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            msg_type TEXT NOT NULL,
            content TEXT NOT NULL,
            media_url TEXT,
            media_type TEXT,
            status TEXT DEFAULT 'new',
            admin_reply TEXT,
            channel_message_id INTEGER DEFAULT NULL,
            created_at TEXT,
            replied_at TEXT
        )
        """)

        # Migration check for channel_message_id
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN channel_message_id INTEGER DEFAULT NULL")
        except Exception:
            pass

        # Migration check for user_notify_message_id
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN user_notify_message_id INTEGER DEFAULT NULL")
        except Exception:
            pass


        # Schedules
        await db.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT NOT NULL,
            group_name TEXT NOT NULL,
            day_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            subject TEXT NOT NULL,
            room TEXT NOT NULL,
            teacher TEXT NOT NULL
        )
        """)

        # Tests
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT,
            duration_minutes INTEGER DEFAULT 30,
            attempts_allowed INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1
        )
        """)

        # Test Questions
        await db.execute("""
        CREATE TABLE IF NOT EXISTS test_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option TEXT NOT NULL,
            FOREIGN KEY (test_id) REFERENCES tests (id) ON DELETE CASCADE
        )
        """)

        # Test Attempts
        await db.execute("""
        CREATE TABLE IF NOT EXISTS test_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            percentage REAL NOT NULL,
            completed_at TEXT NOT NULL
        )
        """)

        # Announcements & Events
        await db.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'announcement',
            event_date TEXT,
            created_at TEXT NOT NULL
        )
        """)

        # Documents
        await db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            file_url TEXT NOT NULL,
            file_size TEXT DEFAULT '1.2 MB',
            is_public INTEGER DEFAULT 1
        )
        """)

        # Settings
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        # Default auto_post_channel setting (0 = disabled / requires admin approve, 1 = direct auto-post)
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_post_channel', '0')")

        await db.commit()

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        await db.commit()

async def is_user_blocked(user_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT account_status FROM users WHERE telegram_id = ?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0] == "Blocked")

async def block_user(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET account_status = 'Blocked' WHERE telegram_id = ?", (user_id,))
        await db.commit()

async def unblock_user(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET account_status = 'Active' WHERE telegram_id = ?", (user_id,))
        await db.commit()

async def get_blocked_users():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT telegram_id, full_name, username, created_at FROM users WHERE account_status = 'Blocked'")
        return await cur.fetchall()

