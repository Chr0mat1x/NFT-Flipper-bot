"""
Анализ выгодности NFT-лотов для перепродажи (флипов).
Реализует все критерии из ТЗ: ценовая аномалия, редкость атрибутов,
ликвидность, возраст листинга, чистая прибыль, фильтр скамов.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

from parsers import NftLot

logger = logging.getLogger(__name__)

# Пороговые значения по умолчанию (можно переопределить через настройки)
DEFAULT_DISCOUNT_THRESHOLD = 0.80      # listing_price < floor * 0.80
DEFAULT_COMMISSION_RATE = 0.02         # комиссия маркетплейса 2%
DEFAULT_SELL_MARKUP = 1.05             # продаём по floor * 1.05
DEFAULT_MIN_VOLUME_24H = 100.0         # TON
DEFAULT_MIN_HOLDERS = 200
DEFAULT_MIN_COLLECTION_AGE_DAYS = 3    # коллекция старше 3 дней
SCAM_KEYWORDS = ("mystery", "airdrop", "giveaway")


@dataclass
class AnalysisResult:
    """Результат анализа лота."""
    is_profitable: bool
    reasons: List[str] = None          # причины, почему выгодно
    reject_reasons: List[str] = None   # причины, почему не выгодно
    profit: float = 0.0                # чистая прибыль в TON
    rarity_score: float = 0.0          # редкостный вес
    discount: float = 0.0              # скидка относительно floor (0..1)


class NftAnalyzer:
    """
    Анализирует лоты на предмет выгодности перепродажи.
    Все критерии из ТЗ реализованы в методе is_profitable().
    """

    def __init__(
        self,
        min_profit: float = 5.0,
        max_listing_age_minutes: int = 120,
        discount_threshold: float = DEFAULT_DISCOUNT_THRESHOLD,
        commission_rate: float = DEFAULT_COMMISSION_RATE,
        sell_markup: float = DEFAULT_SELL_MARKUP,
        min_volume_24h: float = DEFAULT_MIN_VOLUME_24H,
        min_holders: int = DEFAULT_MIN_HOLDERS,
        min_collection_age_days: float = DEFAULT_MIN_COLLECTION_AGE_DAYS,
    ):
        self.min_profit = min_profit
        self.max_listing_age_minutes = max_listing_age_minutes
        self.discount_threshold = discount_threshold
        self.commission_rate = commission_rate
        self.sell_markup = sell_markup
        self.min_volume_24h = min_volume_24h
        self.min_holders = min_holders
        self.min_collection_age_days = min_collection_age_days

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def is_profitable(self, lot: NftLot) -> AnalysisResult:
        """
        Проверяет, является ли лот выгодным для перепродажи.
        Возвращает AnalysisResult с причинами.
        """
        result = AnalysisResult(is_profitable=False, reasons=[], reject_reasons=[])

        # 1. Фильтр скамов (самый ранний — дешёвая проверка)
        scam_reason = self._check_scam(lot)
        if scam_reason:
            result.reject_reasons.append(scam_reason)
            return result

        # 2. Ценовая аномалия
        if lot.floor_price <= 0 or lot.listing_price <= 0:
            result.reject_reasons.append("Нет данных о цене (floor или листинг)")
            return result

        discount = 1.0 - (lot.listing_price / lot.floor_price)
        result.discount = discount
        if lot.listing_price >= lot.floor_price * self.discount_threshold:
            result.reject_reasons.append(
                f"Скидка {discount:.1%} меньше порога {1 - self.discount_threshold:.0%}"
            )
            return result
        result.reasons.append(f"Ценовая аномалия: скидка {discount:.1%} от floor")

        # 3. Ликвидность коллекции
        liquidity_ok, liquidity_reason = self._check_liquidity(lot)
        if not liquidity_ok:
            result.reject_reasons.append(liquidity_reason)
            return result
        result.reasons.append(liquidity_reason)

        # 4. Возраст листинга
        if lot.listing_age_minutes is not None:
            if lot.listing_age_minutes > self.max_listing_age_minutes:
                result.reject_reasons.append(
                    f"Листинг старше {self.max_listing_age_minutes} мин "
                    f"({lot.listing_age_minutes:.0f} мин)"
                )
                return result
            result.reasons.append(f"Возраст листинга {lot.listing_age_minutes:.0f} мин")
        else:
            # Если возраст неизвестен — пропускаем проверку (не блокируем)
            result.reasons.append("Возраст листинга неизвестен (пропущена проверка)")

        # 5. Редкость атрибутов
        rarity_score, rarity_reason = self._check_rarity(lot)
        result.rarity_score = rarity_score
        if rarity_reason:
            result.reject_reasons.append(rarity_reason)
            return result
        if rarity_score > 0:
            result.reasons.append(f"Редкость: вес {rarity_score:.2f}")

        # 6. Чистая прибыль
        profit = self._calculate_profit(lot)
        result.profit = profit
        if profit < self.min_profit:
            result.reject_reasons.append(
                f"Прибыль {profit:.2f} TON < минимума {self.min_profit} TON"
            )
            return result
        result.reasons.append(f"Чистая прибыль {profit:.2f} TON")

        # Все проверки пройдены
        result.is_profitable = True
        return result

    # ------------------------------------------------------------------
    # Внутренние проверки
    # ------------------------------------------------------------------

    def _check_scam(self, lot: NftLot) -> Optional[str]:
        """Фильтр скамов: коллекция старше 3 дней, название без скам-слов."""
        # Проверка названия коллекции и лота на скам-слова
        combined = f"{lot.collection} {lot.name}".lower()
        for keyword in SCAM_KEYWORDS:
            if keyword in combined:
                return f"Название содержит скам-слово '{keyword}'"

        # Проверка возраста коллекции (если известен)
        if lot.collection_age_days is not None:
            if lot.collection_age_days < self.min_collection_age_days:
                return (
                    f"Коллекция слишком молодая "
                    f"({lot.collection_age_days:.0f} дн < {self.min_collection_age_days} дн)"
                )
        return None

    def _check_liquidity(self, lot: NftLot) -> tuple[bool, str]:
        """
        Ликвидность: объём 24ч > 100 TON, холдеров > 200, наличие ставок.
        Если данные неизвестны — считаем проверку пройденной (не блокируем).
        """
        reasons = []

        if lot.volume_24h is not None:
            if lot.volume_24h <= self.min_volume_24h:
                return False, f"Объём 24ч {lot.volume_24h:.1f} TON ≤ {self.min_volume_24h} TON"
            reasons.append(f"Объём 24ч {lot.volume_24h:.1f} TON")
        else:
            reasons.append("Объём 24ч неизвестен")

        if lot.holders is not None:
            if lot.holders <= self.min_holders:
                return False, f"Холдеров {lot.holders} ≤ {self.min_holders}"
            reasons.append(f"Холдеров {lot.holders}")
        else:
            reasons.append("Холдеры неизвестны")

        if lot.has_bids:
            reasons.append("Есть активные ставки")
        else:
            reasons.append("Ставок нет (не критично)")

        return True, ", ".join(reasons)

    def _check_rarity(self, lot: NftLot) -> tuple[float, Optional[str]]:
        """
        Редкость атрибутов:
        - вес = сумма(редкость_атрибута * 0.1)
        - если вес > 5 — бонус
        - если есть атрибут с редкостью < 3% — дополнительный плюс
        - если более 3 атрибутов с редкостью > 20% — игнорируем лот
        """
        if not lot.attributes:
            return 0.0, None  # нет данных — не блокируем

        # Считаем вес
        rarity_weight = sum(attr["rarity"] * 0.1 for attr in lot.attributes)

        # Проверка: более 3 атрибутов с редкостью > 20% — игнорируем
        common_attrs = [a for a in lot.attributes if a["rarity"] > 20.0]
        if len(common_attrs) > 3:
            return rarity_weight, (
                f"Более 3 атрибутов с редкостью > 20% "
                f"({len(common_attrs)} шт) — вероятно, обычный NFT"
            )

        # Бонус за вес > 5
        if rarity_weight > 5:
            rarity_weight += 2.0  # бонус

        # Дополнительный плюс за атрибут с редкостью < 3%
        rare_attrs = [a for a in lot.attributes if a["rarity"] < 3.0]
        if rare_attrs:
            rarity_weight += 1.5  # бонус за редкий атрибут

        return rarity_weight, None

    def _calculate_profit(self, lot: NftLot) -> float:
        """
        Чистая прибыль = (floor * sell_markup) - listing_price - комиссия(2% от продажи).
        """
        sell_price = lot.floor_price * self.sell_markup
        commission = sell_price * self.commission_rate
        profit = sell_price - lot.listing_price - commission
        return profit

    # ------------------------------------------------------------------
    # Вспомогательные методы для отчётов
    # ------------------------------------------------------------------

    def format_lot_message(self, lot: NftLot, result: AnalysisResult) -> str:
        """Форматирует сообщение для отправки в Telegram."""
        lines = [
            f"🤑 <b>Выгодный флип найден!</b>",
            f"",
            f"🏷 <b>{lot.name}</b>",
            f"📦 Коллекция: <b>{lot.collection}</b>",
            f"🌐 Маркетплейс: <b>{lot.marketplace}</b>",
            f"",
            f"💰 Цена листинга: <b>{lot.listing_price:.2f} TON</b>",
            f"📊 Floor: <b>{lot.floor_price:.2f} TON</b>",
            f"📉 Скидка: <b>{result.discount:.1%}</b>",
            f"💎 Чистая прибыль: <b>{result.profit:.2f} TON</b>",
        ]

        if result.rarity_score > 0:
            lines.append(f"⭐ Редкость: <b>{result.rarity_score:.2f}</b>")

        if lot.volume_24h is not None:
            lines.append(f"📈 Объём 24ч: <b>{lot.volume_24h:.1f} TON</b>")
        if lot.holders is not None:
            lines.append(f"👥 Холдеров: <b>{lot.holders}</b>")
        if lot.has_bids:
            lines.append(f"🔨 Есть активные ставки")

        lines.append(f"")
        lines.append(f"🔗 <a href='{lot.url}'>Открыть лот</a>")

        return "\n".join(lines)