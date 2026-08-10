"""
Фоновый асинхронный мониторинг NFT-маркетплейсов.
Каждый цикл проверяет коллекции пользователей с включённым мониторингом,
применяет критерии выгодности и отправляет уведомления.
"""
import asyncio
import logging
from typing import Dict, List

import database as db
from analyzer import NftAnalyzer
from config import config
from parsers import NftLot, ParserManager

logger = logging.getLogger(__name__)


class MonitoringService:
    """Фоновый мониторинг. Запускается как asyncio-задача при старте."""

    def __init__(self, bot):
        self.bot = bot
        self.parser_manager = ParserManager(proxy_url=config.PROXY_URL)
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Запускает мониторинг: стартует браузер и фоновую задачу."""
        if self._running:
            return
        try:
            await self.parser_manager.start()
            logger.info("Playwright браузер запущен")
        except Exception as exc:
            logger.error("Не удалось запустить Playwright: %s", exc)
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Мониторинг запущен")

    async def stop(self) -> None:
        """Останавливает мониторинг и закрывает браузер."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await self.parser_manager.stop()
            logger.info("Playwright браузер остановлен")
        except Exception as exc:
            logger.error("Ошибка при остановке Playwright: %s", exc)
        logger.info("Мониторинг остановлен")

    async def _monitor_loop(self) -> None:
        """Бесконечный цикл проверки коллекций."""
        logger.info(
            "Цикл мониторинга запущен, интервал %d мин",
            config.MONITOR_INTERVAL_MINUTES,
        )
        while self._running:
            try:
                await self._run_check_cycle()
            except Exception as exc:
                logger.error("Ошибка цикла мониторинга: %s", exc)
            for _ in range(int(config.MONITOR_INTERVAL_MINUTES * 60)):
                if not self._running:
                    return
                await asyncio.sleep(1)

    async def _run_check_cycle(self) -> None:
        """Один полный цикл проверки всех коллекций всех пользователей."""
        logger.info("Начинаю цикл проверки коллекций")

        if self.parser_manager.getgems is None:
            try:
                await self.parser_manager.start()
            except Exception as exc:
                logger.error("Не удалось перезапустить Playwright: %s", exc)
                return

        users = db.get_all_users_with_monitoring()
        if not users:
            logger.info("Нет пользователей с включённым мониторингом")
            return

        logger.info("Проверяю коллекции для %d пользователей", len(users))

        collection_tasks: Dict[tuple, dict] = {}
        for user_id in users:
            settings = db.get_user_settings(user_id)
            for collection in settings["collections"]:
                key = (user_id, collection)
                collection_tasks[key] = {
                    "user_id": user_id,
                    "collection": collection,
                    "settings": settings,
                }

        for key, task in list(collection_tasks.items()):
            if not self._running:
                return

            user_id = task["user_id"]
            collection = task["collection"]
            settings = task["settings"]

            if not db.should_check_collection(
                collection, config.MIN_COLLECTION_CHECK_INTERVAL_MINUTES
            ):
                continue

            try:
                await self._check_collection_for_user(user_id, collection, settings)
            except Exception as exc:
                logger.error(
                    "Ошибка проверки коллекции '%s' для пользователя %d: %s",
                    collection, user_id, exc,
                )
                continue

            db.set_collection_check(collection)
            await asyncio.sleep(2)

        db.cleanup_old_lots()
        logger.info("Цикл проверки коллекций завершён")

    async def _check_collection_for_user(
        self, user_id: int, collection: str, settings: dict
    ) -> None:
        """Парсит коллекцию, анализирует лоты и отправляет уведомления."""
        logger.info("Проверка коллекции '%s' для пользователя %d", collection, user_id)

        lots = await self.parser_manager.parse_collection(collection)
        if not lots:
            logger.info(
                "Коллекция '%s': не найдено лотов (или маркетплейсы недоступны)",
                collection,
            )
            return

        lots.sort(key=lambda l: l.listing_price)
        enriched = await self.parser_manager.enrich_lots(lots)

        analyzer = NftAnalyzer(
            min_profit=settings["min_profit"],
            max_listing_age_minutes=settings["max_listing_age"],
        )

        sent_count = 0
        for lot in lots:
            if db.is_lot_sent(lot.lot_id, user_id):
                continue
            if lot.listing_price < settings["min_price"]:
                continue
            if lot.listing_price > settings["max_price"]:
                continue

            analyzed_lot = lot
            for enriched_lot in enriched:
                if enriched_lot.lot_id == lot.lot_id:
                    analyzed_lot = enriched_lot
                    break

            result = analyzer.is_profitable(analyzed_lot)
            if not result.is_profitable:
                continue

            try:
                text = analyzer.format_lot_message(analyzed_lot, result)
                await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                db.mark_lot_sent(lot.lot_id, user_id)
                sent_count += 1
                logger.info(
                    "Отправлен выгодный лот %s пользователю %d (прибыль %.2f TON)",
                    lot.lot_id, user_id, result.profit,
                )
            except Exception as exc:
                logger.error(
                    "Ошибка отправки уведомления пользователю %d: %s", user_id, exc
                )

            if sent_count >= config.MAX_NOTIFICATIONS_PER_CYCLE:
                logger.info(
                    "Достигнут лимит уведомлений (%d) для пользователя %d",
                    config.MAX_NOTIFICATIONS_PER_CYCLE, user_id,
                )
                break

        logger.info(
            "Коллекция '%s': отправлено %d уведомлений для пользователя %d",
            collection, sent_count, user_id,
        )