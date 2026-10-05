"""Настройки бота. Значения берутся из файла .env рядом с bot.py или из переменных окружения."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_env_file(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Главный администратор (может выдавать оценки) + дополнительные через запятую
OWNER_ID = 7185631660
ADMIN_IDS = {OWNER_ID} | {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()
}

PRICE_STARS = _int("PRICE_STARS", 10)  # цена одной оценки в звёздах Telegram
PACKS = [1, 3, 5, 10]  # пакеты оценок в магазине
WELCOME_BONUS = _int("WELCOME_BONUS", 0)  # бесплатные оценки новым пользователям
ADMIN_FREE = _bool("ADMIN_FREE", True)  # админы оценивают себя бесплатно
MINI_APP_URL = os.environ.get("MINI_APP_URL", "").strip()  # пусто — кнопка-заглушка
BRAND = os.environ.get("BOT_BRAND", "I WANNA MOG YOU").strip() or "I WANNA MOG YOU"
ANALYSIS_WORKERS = max(1, _int("ANALYSIS_WORKERS", 1))  # одновременных анализов (телефон — 1)

# Картинка приветствия после /start. Если файла нет — бот нарисует баннер сам
BANNER_PATH = BASE_DIR / (os.environ.get("BANNER_PATH", "").strip() or "assets/banner.jpg")

DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "bot.db"))
LOG_PATH = DATA_DIR / "bot.log"
