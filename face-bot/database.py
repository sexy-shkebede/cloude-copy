"""SQLite: пользователи, баланс оценок, платежи звёздами, история оценок."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER NOT NULL DEFAULT 0,
    gender TEXT,
    ratings_count INTEGER NOT NULL DEFAULT 0,
    best_score REAL,
    last_score REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS payments (
    charge_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    credits INTEGER NOT NULL,
    payload TEXT,
    refunded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    score REAL NOT NULL,
    parts TEXT,
    paid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    # ---------- пользователи ----------
    def upsert_user(self, user_id: int, username: str | None, first_name: str | None, bonus: int = 0) -> tuple[sqlite3.Row, bool]:
        """Создаёт или обновляет пользователя. Возвращает (строка, создан_ли_сейчас)."""
        now = _now()
        with self.lock, self.conn:
            row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO users(id, username, first_name, balance, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                    (user_id, username, first_name, max(0, bonus), now, now),
                )
                created = True
            else:
                self.conn.execute(
                    "UPDATE users SET username=?, first_name=?, updated_at=? WHERE id=?",
                    (username, first_name, now, user_id),
                )
                created = False
            return self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(), created

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def find_user(self, ref: str) -> sqlite3.Row | None:
        """Поиск по id или @username."""
        ref = ref.strip()
        with self.lock:
            if ref.lstrip("-").isdigit():
                return self.conn.execute("SELECT * FROM users WHERE id=?", (int(ref),)).fetchone()
            return self.conn.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (ref.lstrip("@"),)
            ).fetchone()

    def ensure_user(self, user_id: int) -> None:
        now = _now()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO users(id, balance, created_at, updated_at) VALUES(?,0,?,?)", (user_id, now, now)
            )

    def set_gender(self, user_id: int, gender: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET gender=?, updated_at=? WHERE id=?", (gender, _now(), user_id))

    # ---------- баланс ----------
    def get_balance(self, user_id: int) -> int:
        row = self.get_user(user_id)
        return int(row["balance"]) if row else 0

    def add_balance(self, user_id: int, amount: int) -> int:
        """Изменяет баланс (amount может быть отрицательным, баланс не уходит ниже 0)."""
        self.ensure_user(user_id)
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE users SET balance=MAX(0, balance + ?), updated_at=? WHERE id=?", (amount, _now(), user_id)
            )
            return int(self.conn.execute("SELECT balance FROM users WHERE id=?", (user_id,)).fetchone()[0])

    def try_spend(self, user_id: int, amount: int = 1) -> bool:
        """Атомарно списывает оценки, если их хватает."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE users SET balance = balance - ?, updated_at=? WHERE id=? AND balance >= ?",
                (amount, _now(), user_id, amount),
            )
            return cur.rowcount == 1

    # ---------- платежи ----------
    def record_payment(self, charge_id: str, user_id: int, stars: int, credits: int, payload: str) -> bool:
        """Сохраняет платёж и начисляет оценки. False — если такой платёж уже был (повтор)."""
        self.ensure_user(user_id)
        with self.lock, self.conn:
            try:
                self.conn.execute(
                    "INSERT INTO payments(charge_id, user_id, stars, credits, payload, created_at) VALUES(?,?,?,?,?,?)",
                    (charge_id, user_id, stars, credits, payload, _now()),
                )
            except sqlite3.IntegrityError:
                return False
            self.conn.execute(
                "UPDATE users SET balance = balance + ?, updated_at=? WHERE id=?", (credits, _now(), user_id)
            )
            return True

    def get_payment(self, charge_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM payments WHERE charge_id=?", (charge_id,)).fetchone()

    def mark_refunded(self, charge_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE payments SET refunded=1 WHERE charge_id=?", (charge_id,))

    def last_payments(self, user_id: int, limit: int = 5) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
            ).fetchall()

    # ---------- оценки ----------
    def record_rating(self, user_id: int, score: float, parts: dict, paid: bool) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO ratings(user_id, score, parts, paid, created_at) VALUES(?,?,?,?,?)",
                (user_id, score, json.dumps(parts, ensure_ascii=False), int(paid), _now()),
            )
            self.conn.execute(
                "UPDATE users SET ratings_count = ratings_count + 1, last_score=?, "
                "best_score = MAX(COALESCE(best_score, 0), ?), updated_at=? WHERE id=?",
                (score, score, _now(), user_id),
            )

    def record_grant(self, admin_id: int, user_id: int, amount: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO grants(admin_id, user_id, amount, created_at) VALUES(?,?,?,?)",
                (admin_id, user_id, amount, _now()),
            )

    def stats(self) -> dict:
        with self.lock:
            q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
            return {
                "users": q("SELECT COUNT(*) FROM users"),
                "ratings": q("SELECT COUNT(*) FROM ratings"),
                "ratings_today": q("SELECT COUNT(*) FROM ratings WHERE created_at >= date('now','localtime')"),
                "payments": q("SELECT COUNT(*) FROM payments WHERE refunded=0"),
                "stars": q("SELECT COALESCE(SUM(stars),0) FROM payments WHERE refunded=0"),
                "granted": q("SELECT COALESCE(SUM(amount),0) FROM grants"),
                "avg_score": q("SELECT AVG(score) FROM ratings"),
            }
