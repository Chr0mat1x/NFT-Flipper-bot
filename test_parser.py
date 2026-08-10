"""
Отдельный скрипт для тестирования парсера без запуска бота.
Пример: python test_parser.py "Ton Punks"
"""
import asyncio
import logging
import sys

from parsers import ParserManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main(collection_name: str) -> None:
    """Тестирует парсинг коллекции на Getgems и Tonnel."""
    manager = ParserManager()
    await manager.start()
    try:
        logger.info("Парсинг коллекции '%s'...", collection_name)
        lots = await manager.parse_collection(collection_name)
        print(f"\nНайдено лотов: {len(lots)}\n")
        for lot in lots[:10]:
            print(
                f"  [{lot.marketplace}] {lot.name} | "
                f"Цена: {lot.listing_price} TON | Floor: {lot.floor_price} TON | "
                f"Объём 24ч: {lot.volume_24h} | Холдеры: {lot.holders}"
            )
        if lots:
            print("\nОбогащаю первые 3 лота атрибутами...")
            enriched = await manager.enrich_lots(lots, max_enrich=3)
            for lot in enriched:
                print(f"\n  {lot.name} ({lot.marketplace})")
                print(f"    Атрибуты: {lot.attributes}")
                print(f"    Возраст листинга: {lot.listing_age_minutes} мин")
                print(f"    Ставки: {lot.has_bids}")
    finally:
        await manager.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python test_parser.py 'Название коллекции'")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))