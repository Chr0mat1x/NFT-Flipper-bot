"""
Централизованная загрузка конфигурации из .env и переменных окружения.
Все настройки бота доступны через конфиг без прямого чтения os.environ.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта: каталог, где лежит этот файл
BASE_DIR = Path(__file__).resolve().parent

# Загружаем переменные из .env (если файл есть)
load_dotenv(BASE_DIR / ".env")


class Config:
    """Настройки приложения. Значения берутся из .env / окружения, есть разумные дефолты."""

    # --- Telegram ---
    #: Токен бота от @BotFather (обязательный параметр)
    #: Убираем пробелы, переносы строк и кавычки (вдруг вставили с кавычками)
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip().strip("\"'")

    #: Публичный URL приложения (например https://myapp.up.railway.app).
    #: Если не задан — бот работает в режиме поллинга (локально).
    WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
    WEBHOOK_PATH: str = os.getenv("WEBHOOK_PATH", "/webhook/telegram")

    # --- Сервер ---
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))  # Railway автоматически подставляет PORT

    # --- Прокси (опционально) ---
    # Пример: http://user:pass@host:port или socks5://host:port. Пусто = без прокси.
    PROXY_URL: str = os.getenv("PROXY_URL", "").strip()

    # --- База данных ---
    DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "data.db"))

    # --- Мониторинг ---
    #: Как часто запускается полный цикл проверки всех коллекций, минут
    MONITOR_INTERVAL_MINUTES: int = int(os.getenv("MONITOR_INTERVAL_MINUTES", "15"))
    #: Минимальная пауза между проверками одной и той же коллекции, минут
    MIN_COLLECTION_CHECK_INTERVAL_MINUTES: int = int(os.getenv("MIN_COLLECTION_CHECK_INTERVAL_MINUTES", "10"))

    # --- Значения фильтров по умолчанию ---
    DEFAULT_MIN_PRICE: float = float(os.getenv("DEFAULT_MIN_PRICE", "1"))
    DEFAULT_MAX_PRICE: float = float(os.getenv("DEFAULT_MAX_PRICE", "500"))
    DEFAULT_MIN_PROFIT: float = float(os.getenv("DEFAULT_MIN_PROFIT", "5"))
    DEFAULT_MAX_LISTING_AGE: int = int(os.getenv("DEFAULT_MAX_LISTING_AGE", "120"))

    # --- Лимиты парсинга ---
    #: Таймаут загрузки страницы в миллисекундах
    PARSING_TIMEOUT_MS: int = int(os.getenv("PARSING_TIMEOUT_MS", "45000"))
    #: Сколько лотов максимум брать из коллекции
    MAX_LOTS_PER_COLLECTION: int = int(os.getenv("MAX_LOTS_PER_COLLECTION", "30"))
    #: Сколько лотов обогащать атрибутами (редкостью) через открытие страницы лота
    MAX_LOTS_ENRICHED: int = int(os.getenv("MAX_LOTS_ENRICHED", "10"))
    #: Сколько прокруток страницы для подгрузки ленивого контента
    MAX_SCROLLS: int = int(os.getenv("MAX_SCROLLS", "5"))
    #: Максимум уведомлений одному пользователю за один цикл (анти-спам)
    MAX_NOTIFICATIONS_PER_CYCLE: int = int(os.getenv("MAX_NOTIFICATIONS_PER_CYCLE", "10"))

    # --- Логирование ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str = os.getenv("LOG_FILE", str(BASE_DIR / "bot.log"))


# Единственный экземпляр конфига для всего приложения
config = Config()