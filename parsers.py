"""
Парсеры NFT-маркетплейсов TON (Getgems, Tonnel) на базе Playwright (async).
Не используют никаких API-ключей — только открытие страниц и извлечение данных.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Browser, Page

from config import config

logger = logging.getLogger(__name__)

# Регулярки для извлечения чисел из текста
_PERCENT_RE = re.compile(r"([\d.,]+)\s*%")
_NUMBER_RE = re.compile(r"([\d\s.,]+)")


def _parse_float(text: Optional[str]) -> Optional[float]:
    """Извлекает число из строки вида '1 234.56 TON' или '12,5%'."""
    if not text:
        return None
    # Убираем пробелы-разделители тысяч, оставляем точку как десятичный разделитель
    cleaned = text.replace("\u00a0", " ").replace(",", ".").replace(" ", "")
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group(1).replace(" ", ""))
    except ValueError:
        return None


def _parse_percent(text: Optional[str]) -> Optional[float]:
    """Извлекает процент из строки вида '3.5%'."""
    if not text:
        return None
    match = _PERCENT_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


@dataclass
class NftLot:
    """Один лот (NFT) на маркетплейсе."""
    lot_id: str                 # уникальный идентификатор (для анти-спама)
    marketplace: str            # "getgems" | "tonnel"
    collection: str             # название коллекции
    name: str                   # название NFT
    url: str                    # ссылка на лот
    listing_price: float        # цена листинга в TON
    floor_price: float          # текущий floor коллекции в TON
    attributes: List[Dict] = field(default_factory=list)  # [{"trait": str, "rarity": float}]
    listing_age_minutes: Optional[float] = None
    volume_24h: Optional[float] = None
    holders: Optional[int] = None
    has_bids: bool = False
    collection_age_days: Optional[float] = None
    image_url: Optional[str] = None


@dataclass
class CollectionInfo:
    """Информация о коллекции, извлекаемая со страницы коллекции."""
    name: str
    floor_price: Optional[float] = None
    volume_24h: Optional[float] = None
    holders: Optional[int] = None
    has_bids: bool = False
    collection_age_days: Optional[float] = None


class BaseParser:
    """Базовый класс парсера: управление браузером, прокси, таймауты."""

    def __init__(self, proxy_url: str = ""):
        self._proxy_url = proxy_url
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def start(self) -> None:
        """Запускает Playwright и браузер (Chromium headless)."""
        self._playwright = await async_playwright().start()
        launch_options = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if self._proxy_url:
            launch_options["proxy"] = {"server": self._proxy_url}
        self._browser = await self._playwright.chromium.launch(**launch_options)

    async def stop(self) -> None:
        """Останавливает браузер и Playwright."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_page(self) -> Page:
        """Создаёт новую страницу с настройками."""
        page = await self._browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        # Блокируем тяжёлые ресурсы для скорости
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )
        return page

    async def _goto(self, page: Page, url: str) -> None:
        """Переход на страницу с таймаутом и обработкой ошибок."""
        await page.goto(url, timeout=config.PARSING_TIMEOUT_MS, wait_until="domcontentloaded")

    async def _scroll(self, page: Page, times: int = config.MAX_SCROLLS) -> None:
        """Прокручивает страницу вниз для подгрузки ленивого контента."""
        for _ in range(times):
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(700)


class GetgemsParser(BaseParser):
    """Парсер маркетплейса Getgems (https://getgems.io)."""

    BASE_URL = "https://getgems.io"

    async def get_collection_info(self, collection_name: str) -> Optional[CollectionInfo]:
        """
        Ищет коллекцию по названию и возвращает её метрики.
        Возвращает None, если коллекция не найдена.
        """
        page = await self._new_page()
        try:
            # Поиск коллекции через поисковую строку
            search_url = f"{self.BASE_URL}/collection?search={collection_name.replace(' ', '%20')}"
            await self._goto(page, search_url)
            await page.wait_for_timeout(2500)

            # Пробуем найти ссылку на коллекцию в результатах
            link = await page.query_selector(
                f"a[href*='/collection/']:has-text('{collection_name}')"
            )
            if not link:
                # Fallback: берём первую ссылку на коллекцию
                link = await page.query_selector("a[href*='/collection/']")
            if not link:
                logger.warning("Коллекция '%s' не найдена на Getgems", collection_name)
                return None

            href = await link.get_attribute("href")
            collection_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
            await self._goto(page, collection_url)
            await page.wait_for_timeout(3000)
            await self._scroll(page, 2)

            return await self._extract_collection_info(page, collection_name, collection_url)
        except Exception as exc:
            logger.error("Ошибка парсинга коллекции %s на Getgems: %s", collection_name, exc)
            return None
        finally:
            await page.close()

    async def _extract_collection_info(
        self, page: Page, collection_name: str, collection_url: str
    ) -> CollectionInfo:
        """Извлекает метрики коллекции со страницы коллекции."""
        text = await page.inner_text("body")

        floor = _parse_float(self._find_floor(text))
        volume = _parse_float(self._find_volume(text))
        holders = self._find_holders(text)
        has_bids = "bid" in text.lower() or "offer" in text.lower()
        age_days = self._find_collection_age(text)

        return CollectionInfo(
            name=collection_name,
            floor_price=floor,
            volume_24h=volume,
            holders=holders,
            has_bids=has_bids,
            collection_age_days=age_days,
        )

    def _find_floor(self, text: str) -> Optional[str]:
        """Ищет floor price в тексте страницы."""
        # Типичные паттерны: "Floor 12.5 TON", "Floor price: 12.5"
        for pattern in [
            r"Floor\s*price[:\s]*([\d\s.,]+)\s*TON",
            r"Floor[:\s]*([\d\s.,]+)\s*TON",
            r"Floor[:\s]*([\d\s.,]+)\s*₮",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_volume(self, text: str) -> Optional[str]:
        """Ищет объём торгов за 24ч."""
        for pattern in [
            r"Volume\s*24h[:\s]*([\d\s.,]+)\s*TON",
            r"Volume[:\s]*([\d\s.,]+)\s*TON",
            r"24h\s*volume[:\s]*([\d\s.,]+)\s*TON",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_holders(self, text: str) -> Optional[int]:
        """Ищет количество холдеров."""
        match = re.search(r"Holders?[:\s]*([\d\s.,]+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1).replace(" ", "").replace(",", ""))
            except ValueError:
                return None
        return None

    def _find_collection_age(self, text: str) -> Optional[float]:
        """Пытается определить возраст коллекции (в днях) по дате создания."""
        # Ищем дату вида "Created: 12.05.2023" или "Deployed: 2023-05-12"
        match = re.search(
            r"(?:Created|Deployed|Launched)[:\s]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
            text,
            re.IGNORECASE,
        )
        if match:
            from datetime import datetime
            date_str = match.group(1)
            for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m/%d/%Y"):
                try:
                    created = datetime.strptime(date_str, fmt)
                    return (datetime.now() - created).days
                except ValueError:
                    continue
        return None

    async def get_lots(
        self, collection_name: str, collection_url: Optional[str] = None
    ) -> List[NftLot]:
        """
        Получает список лотов коллекции с ценами.
        Возвращает пустой список при ошибке.
        """
        page = await self._new_page()
        try:
            if not collection_url:
                info = await self.get_collection_info(collection_name)
                if not info:
                    return []
                # Повторно открываем страницу коллекции (get_collection_info закрыла свою)
                search_url = f"{self.BASE_URL}/collection?search={collection_name.replace(' ', '%20')}"
                await self._goto(page, search_url)
                await page.wait_for_timeout(2500)
                link = await page.query_selector("a[href*='/collection/']")
                if not link:
                    return []
                href = await link.get_attribute("href")
                collection_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

            await self._goto(page, collection_url)
            await page.wait_for_timeout(3000)
            await self._scroll(page, config.MAX_SCROLLS)

            # Извлекаем floor для коллекции
            text = await page.inner_text("body")
            floor = _parse_float(self._find_floor(text)) or 0.0

            # Ищем карточки лотов
            lots = []
            cards = await page.query_selector_all(
                "a[href*='/nft/'], div[class*='nft-card'], div[class*='NftCard']"
            )
            for card in cards[: config.MAX_LOTS_PER_COLLECTION]:
                try:
                    href = await card.get_attribute("href")
                    if not href:
                        continue
                    lot_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    card_text = await card.inner_text()
                    price = _parse_float(card_text)
                    if price is None or price <= 0:
                        continue

                    # Извлекаем имя NFT из текста карточки (первая строка)
                    name = card_text.strip().split("\n")[0][:80] if card_text.strip() else "NFT"

                    # Уникальный ID лота — берём из URL
                    lot_id = lot_url.split("/")[-1] or lot_url

                    lots.append(
                        NftLot(
                            lot_id=f"getgems:{lot_id}",
                            marketplace="getgems",
                            collection=collection_name,
                            name=name,
                            url=lot_url,
                            listing_price=price,
                            floor_price=floor,
                        )
                    )
                except Exception as exc:
                    logger.debug("Ошибка обработки карточки лота: %s", exc)
                    continue

            logger.info("Getgems: коллекция '%s' — найдено %d лотов", collection_name, len(lots))
            return lots
        except Exception as exc:
            logger.error("Ошибка парсинга лотов %s на Getgems: %s", collection_name, exc)
            return []
        finally:
            await page.close()

    async def enrich_lot(self, lot: NftLot) -> NftLot:
        """
        Открывает страницу лота и извлекает атрибуты с редкостью,
        возраст листинга, наличие ставок.
        """
        page = await self._new_page()
        try:
            await self._goto(page, lot.url)
            await page.wait_for_timeout(2500)
            text = await page.inner_text("body")

            # Атрибуты: ищем строки вида "Trait: Value (3.5%)"
            attr_pattern = re.compile(
                r"([A-Za-zА-Яа-яЁё0-9\s\-_]{2,40})\s*[:]\s*([A-Za-zА-Яа-яЁё0-9\s\-_]{1,40})\s*\(([\d.,]+)\s*%\)"
            )
            for match in attr_pattern.finditer(text):
                trait, value, rarity_str = match.groups()
                rarity = _parse_percent(f"{rarity_str}%")
                if rarity is not None:
                    lot.attributes.append(
                        {"trait": f"{trait.strip()}: {value.strip()}", "rarity": rarity}
                    )

            # Возраст листинга: ищем "Listed X minutes ago" / "Listed X hours ago"
            age_match = re.search(
                r"Listed\s+(\d+)\s*(minute|hour|day)s?\s+ago", text, re.IGNORECASE
            )
            if age_match:
                amount = int(age_match.group(1))
                unit = age_match.group(2).lower()
                if unit == "minute":
                    lot.listing_age_minutes = amount
                elif unit == "hour":
                    lot.listing_age_minutes = amount * 60
                elif unit == "day":
                    lot.listing_age_minutes = amount * 1440

            # Наличие ставок
            lot.has_bids = bool(re.search(r"\bBid\b|\bOffer\b", text, re.IGNORECASE))

            # Изображение
            img = await page.query_selector("img[src*='nft']")
            if img:
                lot.image_url = await img.get_attribute("src")

            return lot
        except Exception as exc:
            logger.debug("Ошибка обогащения лота %s: %s", lot.url, exc)
            return lot
        finally:
            await page.close()


class TonnelParser(BaseParser):
    """Парсер маркетплейса Tonnel (https://tonnel.app)."""

    BASE_URL = "https://tonnel.app"

    async def get_collection_info(self, collection_name: str) -> Optional[CollectionInfo]:
        """Ищет коллекцию на Tonnel и возвращает её метрики."""
        page = await self._new_page()
        try:
            search_url = f"{self.BASE_URL}/collections?search={collection_name.replace(' ', '%20')}"
            await self._goto(page, search_url)
            await page.wait_for_timeout(2500)

            link = await page.query_selector(
                f"a[href*='/collection/']:has-text('{collection_name}')"
            )
            if not link:
                link = await page.query_selector("a[href*='/collection/']")
            if not link:
                logger.warning("Коллекция '%s' не найдена на Tonnel", collection_name)
                return None

            href = await link.get_attribute("href")
            collection_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
            await self._goto(page, collection_url)
            await page.wait_for_timeout(3000)
            await self._scroll(page, 2)

            text = await page.inner_text("body")
            floor = _parse_float(self._find_floor(text))
            volume = _parse_float(self._find_volume(text))
            holders = self._find_holders(text)
            has_bids = "bid" in text.lower() or "offer" in text.lower()
            age_days = self._find_collection_age(text)

            return CollectionInfo(
                name=collection_name,
                floor_price=floor,
                volume_24h=volume,
                holders=holders,
                has_bids=has_bids,
                collection_age_days=age_days,
            )
        except Exception as exc:
            logger.error("Ошибка парсинга коллекции %s на Tonnel: %s", collection_name, exc)
            return None
        finally:
            await page.close()

    def _find_floor(self, text: str) -> Optional[str]:
        for pattern in [
            r"Floor\s*price[:\s]*([\d\s.,]+)\s*TON",
            r"Floor[:\s]*([\d\s.,]+)\s*TON",
            r"Floor[:\s]*([\d\s.,]+)\s*₮",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_volume(self, text: str) -> Optional[str]:
        for pattern in [
            r"Volume\s*24h[:\s]*([\d\s.,]+)\s*TON",
            r"Volume[:\s]*([\d\s.,]+)\s*TON",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_holders(self, text: str) -> Optional[int]:
        match = re.search(r"Holders?[:\s]*([\d\s.,]+)", text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1).replace(" ", "").replace(",", ""))
            except ValueError:
                return None
        return None

    def _find_collection_age(self, text: str) -> Optional[float]:
        match = re.search(
            r"(?:Created|Deployed|Launched)[:\s]*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
            text,
            re.IGNORECASE,
        )
        if match:
            from datetime import datetime
            date_str = match.group(1)
            for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%m/%d/%Y"):
                try:
                    created = datetime.strptime(date_str, fmt)
                    return (datetime.now() - created).days
                except ValueError:
                    continue
        return None

    async def get_lots(
        self, collection_name: str, collection_url: Optional[str] = None
    ) -> List[NftLot]:
        """Получает список лотов коллекции на Tonnel."""
        page = await self._new_page()
        try:
            if not collection_url:
                info = await self.get_collection_info(collection_name)
                if not info:
                    return []
                search_url = f"{self.BASE_URL}/collections?search={collection_name.replace(' ', '%20')}"
                await self._goto(page, search_url)
                await page.wait_for_timeout(2500)
                link = await page.query_selector("a[href*='/collection/']")
                if not link:
                    return []
                href = await link.get_attribute("href")
                collection_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

            await self._goto(page, collection_url)
            await page.wait_for_timeout(3000)
            await self._scroll(page, config.MAX_SCROLLS)

            text = await page.inner_text("body")
            floor = _parse_float(self._find_floor(text)) or 0.0

            lots = []
            cards = await page.query_selector_all(
                "a[href*='/nft/'], div[class*='nft-card'], div[class*='NftCard']"
            )
            for card in cards[: config.MAX_LOTS_PER_COLLECTION]:
                try:
                    href = await card.get_attribute("href")
                    if not href:
                        continue
                    lot_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    card_text = await card.inner_text()
                    price = _parse_float(card_text)
                    if price is None or price <= 0:
                        continue

                    name = card_text.strip().split("\n")[0][:80] if card_text.strip() else "NFT"
                    lot_id = lot_url.split("/")[-1] or lot_url

                    lots.append(
                        NftLot(
                            lot_id=f"tonnel:{lot_id}",
                            marketplace="tonnel",
                            collection=collection_name,
                            name=name,
                            url=lot_url,
                            listing_price=price,
                            floor_price=floor,
                        )
                    )
                except Exception as exc:
                    logger.debug("Ошибка обработки карточки лота Tonnel: %s", exc)
                    continue

            logger.info("Tonnel: коллекция '%s' — найдено %d лотов", collection_name, len(lots))
            return lots
        except Exception as exc:
            logger.error("Ошибка парсинга лотов %s на Tonnel: %s", collection_name, exc)
            return []
        finally:
            await page.close()

    async def enrich_lot(self, lot: NftLot) -> NftLot:
        """Обогащает лот атрибутами и возрастом листинга."""
        page = await self._new_page()
        try:
            await self._goto(page, lot.url)
            await page.wait_for_timeout(2500)
            text = await page.inner_text("body")

            attr_pattern = re.compile(
                r"([A-Za-zА-Яа-яЁё0-9\s\-_]{2,40})\s*[:]\s*([A-Za-zА-Яа-яЁё0-9\s\-_]{1,40})\s*\(([\d.,]+)\s*%\)"
            )
            for match in attr_pattern.finditer(text):
                trait, value, rarity_str = match.groups()
                rarity = _parse_percent(f"{rarity_str}%")
                if rarity is not None:
                    lot.attributes.append(
                        {"trait": f"{trait.strip()}: {value.strip()}", "rarity": rarity}
                    )

            age_match = re.search(
                r"Listed\s+(\d+)\s*(minute|hour|day)s?\s+ago", text, re.IGNORECASE
            )
            if age_match:
                amount = int(age_match.group(1))
                unit = age_match.group(2).lower()
                if unit == "minute":
                    lot.listing_age_minutes = amount
                elif unit == "hour":
                    lot.listing_age_minutes = amount * 60
                elif unit == "day":
                    lot.listing_age_minutes = amount * 1440

            lot.has_bids = bool(re.search(r"\bBid\b|\bOffer\b", text, re.IGNORECASE))

            img = await page.query_selector("img[src*='nft']")
            if img:
                lot.image_url = await img.get_attribute("src")

            return lot
        except Exception as exc:
            logger.debug("Ошибка обогащения лота Tonnel %s: %s", lot.url, exc)
            return lot
        finally:
            await page.close()


class ParserManager:
    """
    Управляет жизненным циклом парсеров.
    Создаёт единый браузер для обоих маркетплейсов (экономия ресурсов).
    """

    def __init__(self, proxy_url: str = ""):
        self._proxy_url = proxy_url
        self._playwright = None
        self._browser: Optional[Browser] = None
        self.getgems: Optional[GetgemsParser] = None
        self.tonnel: Optional[TonnelParser] = None

    async def start(self) -> None:
        """Запускает общий браузер и создаёт парсеры."""
        self._playwright = await async_playwright().start()
        launch_options = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if self._proxy_url:
            launch_options["proxy"] = {"server": self._proxy_url}
        self._browser = await self._playwright.chromium.launch(**launch_options)

        self.getgems = GetgemsParser(self._proxy_url)
        self.tonnel = TonnelParser(self._proxy_url)
        # Передаём общий браузер парсерам
        self.getgems._browser = self._browser
        self.tonnel._browser = self._browser

    async def stop(self) -> None:
        """Останавливает браузер."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def parse_collection(self, collection_name: str) -> List[NftLot]:
        """
        Парсит коллекцию на обоих маркетплейсах и возвращает объединённый список лотов.
        """
        results: List[NftLot] = []

        # Getgems
        try:
            info = await self.getgems.get_collection_info(collection_name)
            if info:
                lots = await self.getgems.get_lots(collection_name)
                # Проставляем метрики коллекции в лоты
                for lot in lots:
                    lot.floor_price = info.floor_price or lot.floor_price
                    lot.volume_24h = info.volume_24h
                    lot.holders = info.holders
                    lot.has_bids = info.has_bids or lot.has_bids
                    lot.collection_age_days = info.collection_age_days
                results.extend(lots)
        except Exception as exc:
            logger.error("Ошибка парсинга Getgems для %s: %s", collection_name, exc)

        # Tonnel
        try:
            info = await self.tonnel.get_collection_info(collection_name)
            if info:
                lots = await self.tonnel.get_lots(collection_name)
                for lot in lots:
                    lot.floor_price = info.floor_price or lot.floor_price
                    lot.volume_24h = info.volume_24h
                    lot.holders = info.holders
                    lot.has_bids = info.has_bids or lot.has_bids
                    lot.collection_age_days = info.collection_age_days
                results.extend(lots)
        except Exception as exc:
            logger.error("Ошибка парсинга Tonnel для %s: %s", collection_name, exc)

        return results

    async def enrich_lots(self, lots: List[NftLot], max_enrich: int = config.MAX_LOTS_ENRICHED) -> List[NftLot]:
        """
        Обогащает первые max_enrich лотов атрибутами (редкостью).
        """
        enriched = []
        for lot in lots[:max_enrich]:
            if lot.marketplace == "getgems":
                enriched.append(await self.getgems.enrich_lot(lot))
            else:
                enriched.append(await self.tonnel.enrich_lot(lot))
        return enriched