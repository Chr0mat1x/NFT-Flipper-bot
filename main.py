"""
Точка входа: FastAPI-приложение, вебхук Telegram, запуск мониторинга.
При старте автоматически регистрирует вебхук (если задан WEBHOOK_URL),
иначе работает в режиме поллинга.
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: старт и остановка."""
    # Инициализация БД
    db.init_db()
    logger.info("База данных инициализирована")

    # Регистрация вебхука
    await set_webhook()

    # Запуск мониторинга
    await monitor_service.start()

    yield

    # Остановка
    await monitor_service.stop()
    await delete_webhook()
    await bot.session.close()
    logger.info("Приложение остановлено")


# --- FastAPI приложение ---
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


async def polling_main() -> None:
    """Запуск в режиме поллинга (локально, без WEBHOOK_URL)."""
    db.init_db()
    await monitor_service.start()
    try:
        await dp.start_polling(bot)
    finally:
        await monitor_service.stop()
        await bot.session.close()


if __name__ == "__main__":
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан! Создайте .env файл с BOT_TOKEN.")
        raise SystemExit(1)

    if config.WEBHOOK_URL:
        # Режим вебхука: запускаем FastAPI через Uvicorn
        uvicorn.run(app, host=config.HOST, port=config.PORT)
    else:
        # Режим поллинга: запускаем напрямую
        asyncio.run(polling_main())