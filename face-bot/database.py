"""SQLite: пользователи, баланс оценок, платежи звёздами, история оценок, кланы и топы.

Всё хранится локально в файле data/bot.db (на телефоне с Termux), фото оценок — в data/photos/.
"""
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

CREATE TABLE IF NOT EXISTS clans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
"""

# Колонки, добавленные после первой версии: старая база на телефоне дополняется ими при запуске
MIGRATIONS = {
    "users": {
        "clan_id": "INTEGER",
        "clan_joined_at": "TEXT",
        "best_rating_id": "INTEGER",
        "photo_hidden": "INTEGER NOT NULL DEFAULT 0",
    },
    "ratings": {
        "gender": "TEXT",
        "details": "TEXT",
        "front_path": "TEXT",
        "side_path": "TEXT",
        "card_path": "TEXT",
        "front_file_id": "TEXT",
        "side_file_id": "TEXT",
        "card_file_id": "TEXT",
        "excluded": "INTEGER NOT NULL DEFAULT 0",
    },
}

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_users_clan ON users(clan_id);
CREATE INDEX IF NOT EXISTS idx_users_best ON users(best_score);
CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
"""

# Порядок мест в топах. Одинаковые значения: выше тот, кто добился результата раньше
RATING_ORDER = "best_score DESC, best_rating_id ASC, id ASC"
BALANCE_ORDER = "balance DESC, id ASC"
CLAN_ORDER = "rating DESC, members DESC, id ASC"

CLANS_WITH_RATING = """
    SELECT c.id, c.name, c.owner_id, c.created_at,
           COUNT(u.id) AS members, COALESCE(SUM(u.best_score), 0) AS rating
    FROM clans c LEFT JOIN users u ON u.clan_id = c.id
    GROUP BY c.id
"""


def clan_key(name: str) -> str:
    """Ключ уникальности названия клана: без учёта регистра, лишних пробелов и разницы «ё»/«е»."""
    return " ".join(name.split()).casefold().replace("ё", "е")


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
            self._migrate()

    def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.conn.executescript(INDEXES)
        # оценки, сделанные до появления топов: запоминаем, какая из них лучшая
        self.conn.execute(
            "UPDATE users SET best_rating_id = (SELECT r.id FROM ratings r WHERE r.user_id = users.id "
            "AND r.excluded = 0 ORDER BY r.score DESC, r.id ASC LIMIT 1) "
            "WHERE best_rating_id IS NULL AND best_score IS NOT NULL"
        )

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

    def set_photo_hidden(self, user_id: int, hidden: bool) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE users SET photo_hidden=?, updated_at=? WHERE id=?", (int(hidden), _now(), user_id))

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
    def record_rating(self, user_id: int, score: float, parts: dict, paid: bool, gender: str | None = None,
                      details: dict | None = None) -> tuple[int, bool]:
        """Сохраняет оценку. Возвращает (id оценки, стала ли она новым личным рекордом)."""
        now = _now()
        with self.lock, self.conn:
            prev = self.conn.execute("SELECT best_score FROM users WHERE id=?", (user_id,)).fetchone()
            rid = self.conn.execute(
                "INSERT INTO ratings(user_id, score, parts, paid, gender, details, created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, score, json.dumps(parts, ensure_ascii=False), int(paid), gender,
                 json.dumps(details, ensure_ascii=False) if details is not None else None, now),
            ).lastrowid
            # личный рекорд меняется только при строго большем балле: при равенстве в топе остаётся более ранний
            record = prev is None or prev["best_score"] is None or score > prev["best_score"]
            self.conn.execute(
                "UPDATE users SET ratings_count = ratings_count + 1, last_score=?, "
                "best_score = CASE WHEN ? THEN ? ELSE best_score END, "
                "best_rating_id = CASE WHEN ? THEN ? ELSE best_rating_id END, updated_at=? WHERE id=?",
                (score, int(record), score, int(record), rid, now, user_id),
            )
            return int(rid), record

    def set_rating_files(self, rating_id: int, **fields: str | None) -> None:
        """Пути к сохранённым фото (front_path, side_path, card_path) и их file_id в Telegram."""
        allowed = {"front_path", "side_path", "card_path", "front_file_id", "side_file_id", "card_file_id"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE ratings SET {sets} WHERE id=?", (*fields.values(), rating_id))

    def get_rating(self, rating_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM ratings WHERE id=?", (rating_id,)).fetchone()

    def best_rating(self, user_id: int) -> sqlite3.Row | None:
        """Оценка, которая сейчас считается рейтингом пользователя (его лучшая)."""
        with self.lock:
            return self.conn.execute(
                "SELECT r.* FROM users u JOIN ratings r ON r.id = u.best_rating_id WHERE u.id=?", (user_id,)
            ).fetchone()

    def exclude_best_rating(self, user_id: int) -> sqlite3.Row | None:
        """Админ убирает лучшую оценку пользователя из топа; рейтингом становится следующая по величине."""
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT r.* FROM users u JOIN ratings r ON r.id = u.best_rating_id WHERE u.id=?", (user_id,)
            ).fetchone()
            if row is None:
                return None
            self.conn.execute("UPDATE ratings SET excluded=1 WHERE id=?", (row["id"],))
            nxt = self.conn.execute(
                "SELECT id, score FROM ratings WHERE user_id=? AND excluded=0 ORDER BY score DESC, id ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            self.conn.execute(
                "UPDATE users SET best_score=?, best_rating_id=?, updated_at=? WHERE id=?",
                (nxt["score"] if nxt else None, nxt["id"] if nxt else None, _now(), user_id),
            )
            return row

    def record_grant(self, admin_id: int, user_id: int, amount: int) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO grants(admin_id, user_id, amount, created_at) VALUES(?,?,?,?)",
                (admin_id, user_id, amount, _now()),
            )

    # ---------- кланы ----------
    def create_clan(self, owner_id: int, name: str, name_key: str) -> tuple[sqlite3.Row | None, str | None]:
        """Создаёт клан и переводит в него создателя. Ошибки: 'has_clan' (уже есть свой клан), 'name_taken'."""
        now = _now()
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM clans WHERE owner_id=?", (owner_id,)).fetchone():
                return None, "has_clan"
            if self.conn.execute("SELECT 1 FROM clans WHERE name_key=?", (name_key,)).fetchone():
                return None, "name_taken"
            try:
                cid = self.conn.execute(
                    "INSERT INTO clans(name, name_key, owner_id, created_at) VALUES(?,?,?,?)",
                    (name, name_key, owner_id, now),
                ).lastrowid
            except sqlite3.IntegrityError:
                return None, "name_taken"
            self.conn.execute("UPDATE users SET clan_id=?, clan_joined_at=?, updated_at=? WHERE id=?",
                              (cid, now, now, owner_id))
            return self.conn.execute("SELECT * FROM clans WHERE id=?", (cid,)).fetchone(), None

    def get_clan(self, clan_id: int) -> sqlite3.Row | None:
        """Клан вместе с рейтингом (сумма лучших оценок участников), числом участников и местом в топе."""
        with self.lock:
            return self.conn.execute(
                f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY {CLAN_ORDER}) AS place "
                f"FROM ({CLANS_WITH_RATING})) WHERE id=?", (clan_id,)
            ).fetchone()

    def owned_clan(self, user_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM clans WHERE owner_id=?", (user_id,)).fetchone()

    def user_clan_id(self, user_id: int) -> int | None:
        row = self.get_user(user_id)
        return int(row["clan_id"]) if row and row["clan_id"] is not None else None

    def find_clan(self, ref: str) -> sqlite3.Row | None:
        """Поиск клана по номеру или точному названию (для админа)."""
        ref = ref.strip()
        with self.lock:
            if ref.isdigit():
                row = self.conn.execute("SELECT * FROM clans WHERE id=?", (int(ref),)).fetchone()
                if row:
                    return row
            return self.conn.execute("SELECT * FROM clans WHERE name_key=?", (clan_key(ref),)).fetchone()

    def join_clan(self, user_id: int, clan_id: int) -> str:
        """Вступление в клан. Результат: 'ok', 'no_clan', 'already', 'owner' (глава другого клана)."""
        now = _now()
        with self.lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM clans WHERE id=?", (clan_id,)).fetchone():
                return "no_clan"
            row = self.conn.execute("SELECT clan_id FROM users WHERE id=?", (user_id,)).fetchone()
            if row is not None and row["clan_id"] == clan_id:
                return "already"
            if self.conn.execute("SELECT 1 FROM clans WHERE owner_id=?", (user_id,)).fetchone():
                return "owner"
            self.conn.execute("UPDATE users SET clan_id=?, clan_joined_at=?, updated_at=? WHERE id=?",
                              (clan_id, now, now, user_id))
            return "ok"

    def leave_clan(self, user_id: int) -> str:
        """Выход из клана. Результат: 'ok', 'none' (не в клане), 'owner' (главе нужно распустить клан)."""
        with self.lock, self.conn:
            row = self.conn.execute("SELECT clan_id FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None or row["clan_id"] is None:
                return "none"
            if self.conn.execute("SELECT 1 FROM clans WHERE owner_id=? AND id=?", (user_id, row["clan_id"])).fetchone():
                return "owner"
            self.conn.execute("UPDATE users SET clan_id=NULL, clan_joined_at=NULL, updated_at=? WHERE id=?",
                              (_now(), user_id))
            return "ok"

    def delete_clan(self, clan_id: int) -> tuple[sqlite3.Row | None, list[int]]:
        """Удаляет клан (роспуск главой или админом). Возвращает (клан, id бывших участников)."""
        with self.lock, self.conn:
            clan = self.conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
            if clan is None:
                return None, []
            members = [int(r["id"]) for r in self.conn.execute("SELECT id FROM users WHERE clan_id=?", (clan_id,))]
            self.conn.execute("UPDATE users SET clan_id=NULL, clan_joined_at=NULL, updated_at=? WHERE clan_id=?",
                              (_now(), clan_id))
            self.conn.execute("DELETE FROM clans WHERE id=?", (clan_id,))
            return clan, members

    def clan_members(self, clan_id: int, limit: int = 10) -> list[sqlite3.Row]:
        """Участники клана, лучшие оценки сверху (без оценки — в конце, по времени вступления)."""
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE clan_id=? ORDER BY best_score IS NULL, best_score DESC, clan_joined_at, id "
                "LIMIT ?", (clan_id, limit)
            ).fetchall()

    def clans_page(self, offset: int, limit: int) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                f"SELECT * FROM ({CLANS_WITH_RATING}) ORDER BY {CLAN_ORDER} LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()

    def clans_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM clans").fetchone()[0])

    # ---------- топы ----------
    def top_rating(self, limit: int = 10) -> list[sqlite3.Row]:
        """Топ по рейтингу: рейтинг человека — его лучшая оценка."""
        with self.lock:
            return self.conn.execute(
                f"SELECT u.*, c.name AS clan_name FROM users u LEFT JOIN clans c ON c.id = u.clan_id "
                f"WHERE u.best_score IS NOT NULL ORDER BY {', '.join('u.' + p for p in RATING_ORDER.split(', '))} "
                f"LIMIT ?", (limit,)
            ).fetchall()

    def top_balance(self, limit: int = 10) -> list[sqlite3.Row]:
        """Топ по количеству оценок на балансе."""
        with self.lock:
            return self.conn.execute(
                f"SELECT * FROM users WHERE balance > 0 ORDER BY {BALANCE_ORDER} LIMIT ?", (limit,)
            ).fetchall()

    def top_clans(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.clans_page(0, limit)

    def rating_place(self, user_id: int) -> int | None:
        with self.lock:
            row = self.conn.execute(
                f"SELECT place FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY {RATING_ORDER}) AS place "
                f"FROM users WHERE best_score IS NOT NULL) WHERE id=?", (user_id,)
            ).fetchone()
            return int(row["place"]) if row else None

    def balance_place(self, user_id: int) -> int | None:
        with self.lock:
            row = self.conn.execute(
                f"SELECT place FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY {BALANCE_ORDER}) AS place "
                f"FROM users WHERE balance > 0) WHERE id=?", (user_id,)
            ).fetchone()
            return int(row["place"]) if row else None

    def rated_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM users WHERE best_score IS NOT NULL").fetchone()[0])

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
                "clans": q("SELECT COUNT(*) FROM clans"),
                "in_clans": q("SELECT COUNT(*) FROM users WHERE clan_id IS NOT NULL"),
            }
