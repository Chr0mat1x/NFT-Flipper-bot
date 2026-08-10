"""
Точка входа: FastAPI-приложение, вебхук Telegram, запуск мониторинга.
FastAPI (healthcheck /health) работает ВСЕГДА — Railway не гасит контейнер.
Если задан WEBHOOK_URL — регистрируется вебхук.
Если нет — поллинг запускается фоновой задачей.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

import database as db
from config import config
from handlers import router as handlers_router
from monitor import MonitoringService

# --- Логирование: в консоль и файл ---
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# --- Telegram Bot и Dispatcher ---
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
dp.include_router(handlers_router)

# --- Сервис мониторинга ---
monitor_service = MonitoringService(bot)

# --- Фоновая задача поллинга ---
_polling_task: asyncio.Task | None = None


async def set_webhook() -> None:
    """Регистрирует вебхук, если задан WEBHOOK_URL."""
    if not config.WEBHOOK_URL:
        logger.info("WEBHOOK_URL не задан — работаю в режиме поллинга")
        return
    webhook_url = f"{config.WEBHOOK_URL}{config.WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logger.info("Вебхук зарегистрирован: %s", webhook_url)


async def delete_webhook() -> None:
    """Удаляет вебхук при остановке."""
    try:
        await bot.delete_webhook()
    except Exception as exc:
        logger.error("Ошибка удаления вебхука: %s", exc)


async def start_polling() -> None:
    """Запускает поллинг как фоновую задачу."""
    global _polling_task
    if _polling_task and not _polling_task.done():
        return
    # Важно: удаляем вебхук, иначе Telegram продолжит слать обновления
    # на старый вебхук и поллинг не будет получать сообщения
    try:
        await bot.delete_webhook()
        logger.info("Вебхук удалён перед запуском поллинга")
    except Exception as exc:
        logger.error("Ошибка удаления вебхука перед поллингом: %s", exc)

    async def _run_polling() -> None:
        try:
            await dp.start_polling(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Поллинг упал с ошибкой: %s", exc)

    _polling_task = asyncio.create_task(_run_polling())
    logger.info("Поллинг запущен как фоновая задача")


async def stop_polling() -> None:
    """Останавливает фоновый поллинг."""
    global _polling_task
    if _polling_task:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None
        logger.info("Поллинг остановлен")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: старт и остановка."""
    # Инициализация БД
    db.init_db()
    logger.info("База данных инициализирована")

    # Регистрация вебхука ИЛИ запуск поллинга
    if config.WEBHOOK_URL:
        await set_webhook()
    else:
        await start_polling()

    # Запуск мониторинга
    await monitor_service.start()

    yield

    # Остановка
    await monitor_service.stop()
    if config.WEBHOOK_URL:
        await delete_webhook()
    else:
        await stop_polling()
    await bot.session.close()
    logger.info("Приложение остановлено")


# --- FastAPI приложение (работает всегда, healthcheck доступен) ---
app = FastAPI(title="NFT Flip Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    """Healthcheck для Railway и других хостингов."""
    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/")
async def root():
    """Корневая страница."""
    return {"status": "ok", "service": "NFT Flip Bot"}


@app.post(config.WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Принимает обновления от Telegram через вебхук."""
    update = Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return JSONResponse(content={"ok": True})


if __name__ == "__main__":
    if not config.BOT_TOKEN:
        logger.error(
            "BOT_TOKEN не задан! Добавьте переменную BOT_TOKEN в Railway "
            "(Settings -> Variables) или в .env файл."
        )
        raise SystemExit(1)

    # Проверяем формат токена заранее, чтобы дать понятную ошибку
    try:
        from aiogram.utils.token import validate_token
        validate_token(config.BOT_TOKEN)
    except Exception as exc:
        logger.error(
            "BOT_TOKEN невалиден: %s. Проверьте, что в переменной BOT_TOKEN "
            "нет пробелов, кавычек и переносов строк. Токен должен выглядеть так: "
            "123456789:AAExampleTokenFromBotFather",
            exc,
        )
        raise SystemExit(1)

    # FastAPI всегда запускается через Uvicorn — healthcheck работает всегда,
    # а поллинг/вебхук управляются внутри lifespan
    uvicorn.run(app, host=config.HOST, port=config.PORT)
