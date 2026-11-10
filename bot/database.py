"""Database module for Magnit promo bot using SQLite with aiosqlite."""

import os
from typing import List, Optional

import aiosqlite

from .config import DATABASE_PATH, MAX_STORES


class Database:
    """Handles all database operations for users, stores, discounts, and sent promotions."""

    def __init__(self, db_path: str = DATABASE_PATH) -> None:
        self.db_path = db_path
        # The data directory may not exist yet in a fresh container
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    async def init(self) -> None:
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    min_discount INTEGER NOT NULL DEFAULT 10,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    store_code TEXT NOT NULL,
                    store_address TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    UNIQUE (user_id, store_code)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sent_promotions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    store_code TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    UNIQUE (user_id, product_id, store_code)
                )
                """
            )
            await db.commit()

    # ---- Users ----

    async def upsert_user(self, user_id: int) -> None:
        """Insert or ignore a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, min_discount) VALUES (?, ?)",
                (user_id, 10),
            )
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def set_min_discount(self, user_id: int, min_discount: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET min_discount = ? WHERE user_id = ?",
                (min_discount, user_id),
            )
            await db.commit()

    async def get_min_discount(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT min_discount FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row["min_discount"] if row else 10

    # ---- Stores ----

    async def add_store(self, user_id: int, store_code: str, store_address: str) -> bool:
        """Add a store for a user. Returns False if already exists or limit reached."""
        count = await self.count_stores(user_id)
        if count >= MAX_STORES:
            return False  # max stores per user reached

        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO stores (user_id, store_code, store_address) VALUES (?, ?, ?)",
                    (user_id, store_code, store_address),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def remove_store(self, user_id: int, store_code: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM stores WHERE user_id = ? AND store_code = ?",
                (user_id, store_code),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def count_stores(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT COUNT(*) AS c FROM stores WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row["c"]

    async def get_stores(self, user_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM stores WHERE user_id = ? ORDER BY created_at", (user_id,)
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_all_stores(self) -> List[dict]:
        """Get all stores across all users (used by the polling loop)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM stores") as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_users_for_store(self, store_code: str) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT u.user_id, u.min_discount, s.store_address
                FROM stores s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.store_code = ?
                """,
                (store_code,),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    # ---- Sent promotions ----

    async def is_sent(self, user_id: int, product_id: str, store_code: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT 1 FROM sent_promotions WHERE user_id = ? AND product_id = ? AND store_code = ?",
                (user_id, product_id, store_code),
            ) as cur:
                return await cur.fetchone() is not None

    async def mark_sent(self, user_id: int, product_id: str, store_code: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO sent_promotions (user_id, product_id, store_code) VALUES (?, ?, ?)",
                (user_id, product_id, store_code),
            )
            await db.commit()