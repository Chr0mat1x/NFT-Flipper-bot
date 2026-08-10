"""
Работа с SQLite: хранение настроек пользователей, отправленных лотов
и времени последней проверки коллекций. Используется встроенный sqlite3.
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from config import config

# SQLite не любит конкурентную запись из разных потоков/корутин,
# поэтому используем локальный lock и WAL-режим.
_db_lock = threading.RLock()

# Дефолтные настройки пользователя (используются при первом /start)
DEFAULT_SETTINGS: Dict[str, Any] = {
    "min_price": config.DEFAULT_MIN_PRICE,
    "max_price": config.DEFAULT_MAX_PRICE,
    "collections": [],          # список названий коллекций
    "min_profit": config.DEFAULT_MIN_PROFIT,
    "max_listing_age": config.DEFAULT_MAX_LISTING_AGE,
    "monitoring_enabled": False,
}


def _connect() -> sqlite3.Connection:
    """Создаёт подключение к БД с нужными прагмами."""
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@contextmanager
def _get_conn():
    """Контекстный менеджер подключения с блокировкой и авто-коммитом."""
    with _db_lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывается при старте приложения."""
    with _get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                settings_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sent_lots (
                lot_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                sent_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sent_lots_user ON sent_lots(user_id);

            CREATE TABLE IF NOT EXISTS collection_checks (
                collection_name TEXT PRIMARY KEY,
                last_check_at REAL NOT NULL
            );
            """
        )


# ---------------------------------------------------------------------------
# Настройки пользователей
# ---------------------------------------------------------------------------

def get_user_settings(user_id: int) -> Dict[str, Any]:
    """Возвращает настройки пользователя (создаёт дефолтные, если их нет)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT settings_json FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

    if row is None:
        settings = dict(DEFAULT_SETTINGS)
        save_user_settings(user_id, settings)
        return settings

    settings = json.loads(row["settings_json"])
    # Подстраховка: если в БД не хватает ключей (старая версия) — дополняем
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings)
    return merged


def save_user_settings(user_id: int, settings: Dict[str, Any]) -> None:
    """Сохраняет (или обновляет) настройки пользователя."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, settings_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET settings_json = excluded.settings_json
            """,
            (user_id, json.dumps(settings, ensure_ascii=False), time.time()),
        )


def update_user_setting(user_id: int, key: str, value: Any) -> Dict[str, Any]:
    """Обновляет одно поле настроек и возвращает обновлённый словарь."""
    settings = get_user_settings(user_id)
    settings[key] = value
    save_user_settings(user_id, settings)
    return settings


def get_all_users_with_monitoring() -> List[int]:
    """Возвращает список user_id всех пользователей с включённым мониторингом."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT user_id, settings_json FROM users").fetchall()

    result = []
    for row in rows:
        settings = json.loads(row["settings_json"])
        if settings.get("monitoring_enabled") and settings.get("collections"):
            result.append(row["user_id"])
    return result


# ---------------------------------------------------------------------------
# Отправленные лоты (анти-спам)
# ---------------------------------------------------------------------------

def is_lot_sent(lot_id: str, user_id: int) -> bool:
    """Проверяет, отправляли ли уже этот лот данному пользователю."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_lots WHERE lot_id = ? AND user_id = ?",
            (lot_id, user_id),
        ).fetchone()
    return row is not None


def mark_lot_sent(lot_id: str, user_id: int) -> None:
    """Помечает лот как отправленный пользователю."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_lots (lot_id, user_id, sent_at) VALUES (?, ?, ?)",
            (lot_id, user_id, time.time()),
        )


def cleanup_old_lots(days: int = 14) -> None:
    """Периодическая чистка старых записей об отправленных лотах."""
    cutoff = time.time() - days * 86400
    with _get_conn() as conn:
        conn.execute("DELETE FROM sent_lots WHERE sent_at < ?", (cutoff,))


# ---------------------------------------------------------------------------
# Время последней проверки коллекции
# ---------------------------------------------------------------------------

def get_last_collection_check(collection: str) -> Optional[float]:
    """Возвращает timestamp последней проверки коллекции (или None)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT last_check_at FROM collection_checks WHERE collection = ?",
            (collection,),
        ).fetchone()
    return row["last_check_at"] if row else None


def set_collection_check(collection: str) -> None:
    """Записывает время последней проверки коллекции."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO collection_checks (collection, last_check_at)
            VALUES (?, ?)
            ON CONFLICT(collection) DO UPDATE SET last_check_at = excluded.last_check_at
            """,
            (collection, time.time()),
        )


def should_check_collection(collection: str, min_interval_minutes: int) -> bool:
    """
    Возвращает True, если коллекцию пора проверять
    (прошло не меньше min_interval_minutes с последней проверки).
    """
    last = get_last_collection_check(collection)
    if last is None:
        return True
    return (time.time() - last) >= min_interval_minutes * 60