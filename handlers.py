"""
Обработчики команд Telegram и FSM-состояний для настройки фильтров.
Использует aiogram 3.x.
"""
import logging
from typing import Any, Dict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import database as db
from config import config

logger = logging.getLogger(__name__)

router = Router()


# ---------------------------------------------------------------------------
# FSM-состояния для настройки фильтров
# ---------------------------------------------------------------------------

class FilterStates(StatesGroup):
    """Состояния мастера настройки фильтров."""
    min_price = State()          # минимальная цена
    max_price = State()          # максимальная цена
    collections = State()        # список коллекций
    min_profit = State()         # минимальная прибыль
    max_listing_age = State()    # максимальное время листинга


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура с кнопками."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Настроить фильтры")],
            [KeyboardButton(text="▶️ Запустить мониторинг")],
            [KeyboardButton(text="⏹️ Остановить")],
            [KeyboardButton(text="📋 Мои настройки")],
        ],
        resize_keyboard=True,
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_filters")]
        ]
    )


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start — приветствие и главное меню."""
    user_id = message.from_user.id
    # Создаём настройки по умолчанию, если их нет
    db.get_user_settings(user_id)

    await message.answer(
        "👋 <b>Привет! Я бот для поиска выгодных NFT-флипов на TON.</b>\n\n"
        "Я мониторю маркетплейсы <b>Getgems</b> и <b>Tonnel</b> и ищу лоты, "
        "которые можно купить дешевле и перепродать с прибылью.\n\n"
        "Используй кнопки ниже, чтобы настроить фильтры и запустить мониторинг.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам."""
    await message.answer(
        "📖 <b>Справка</b>\n\n"
        "• <b>/start</b> — главное меню\n"
        "• <b>📊 Настроить фильтры</b> — изменить параметры поиска\n"
        "• <b>▶️ Запустить мониторинг</b> — начать фоновые проверки\n"
        "• <b>⏹️ Остановить</b> — остановить мониторинг\n"
        "• <b>📋 Мои настройки</b> — посмотреть текущие фильтры\n\n"
        "Критерии выгодности:\n"
        "• Цена листинга < 80% от floor\n"
        "• Ликвидная коллекция (объём 24ч > 100 TON, холдеров > 200)\n"
        "• Свежий листинг (не старше заданного времени)\n"
        "• Чистая прибыль ≥ заданного минимума\n"
        "• Фильтр скам-коллекций",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "📊 Настроить фильтры")
async def start_filters(message: Message, state: FSMContext):
    """Начинает мастер настройки фильтров."""
    await state.set_state(FilterStates.min_price)
    await message.answer(
        "⚙️ <b>Настройка фильтров</b>\n\n"
        f"Текущая минимальная цена: <b>{db.get_user_settings(message.from_user.id)['min_price']} TON</b>\n"
        "Введите <b>минимальную цену</b> лота в TON (например, 1):",
        reply_markup=cancel_keyboard(),
    )


@router.message(FilterStates.min_price)
async def process_min_price(message: Message, state: FSMContext):
    """Обрабатывает минимальную цену."""
    try:
        value = float(message.text.replace(",", "."))
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например, 1 или 0.5):")
        return

    db.update_user_setting(message.from_user.id, "min_price", value)
    await state.set_state(FilterStates.max_price)
    await message.answer(
        f"✅ Минимальная цена: <b>{value} TON</b>\n\n"
        f"Текущая максимальная цена: <b>{db.get_user_settings(message.from_user.id)['max_price']} TON</b>\n"
        "Введите <b>максимальную цену</b> лота в TON (например, 500):",
        reply_markup=cancel_keyboard(),
    )


@router.message(FilterStates.max_price)
async def process_max_price(message: Message, state: FSMContext):
    """Обрабатывает максимальную цену."""
    try:
        value = float(message.text.replace(",", "."))
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например, 500):")
        return

    settings = db.update_user_setting(message.from_user.id, "max_price", value)
    if settings["min_price"] > settings["max_price"]:
        await message.answer(
            "⚠️ Максимальная цена меньше минимальной. "
            "Поменяйте их местами или введите корректное значение:"
        )
        return

    await state.set_state(FilterStates.collections)
    current = ", ".join(settings["collections"]) if settings["collections"] else "не заданы"
    await message.answer(
        f"✅ Максимальная цена: <b>{value} TON</b>\n\n"
        f"Текущие коллекции: <b>{current}</b>\n"
        "Введите <b>названия коллекций</b> через запятую (например: "
        "Ton Punks, TON Diamonds, Space Pigs):",
        reply_markup=cancel_keyboard(),
    )


@router.message(FilterStates.collections)
async def process_collections(message: Message, state: FSMContext):
    """Обрабатывает список коллекций."""
    raw = message.text.strip()
    if not raw:
        await message.answer("❌ Введите хотя бы одно название коллекции:")
        return

    # Разбиваем по запятой, убираем лишние пробелы
    collections = [c.strip() for c in raw.split(",") if c.strip()]
    if not collections:
        await message.answer("❌ Не удалось распознать коллекции. Попробуйте ещё раз:")
        return

    db.update_user_setting(message.from_user.id, "collections", collections)
    await state.set_state(FilterStates.min_profit)
    await message.answer(
        f"✅ Коллекции: <b>{', '.join(collections)}</b>\n\n"
        f"Текущая минимальная прибыль: <b>{db.get_user_settings(message.from_user.id)['min_profit']} TON</b>\n"
        "Введите <b>минимальную прибыль</b> в TON (например, 5):",
        reply_markup=cancel_keyboard(),
    )


@router.message(FilterStates.min_profit)
async def process_min_profit(message: Message, state: FSMContext):
    """Обрабатывает минимальную прибыль."""
    try:
        value = float(message.text.replace(",", "."))
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число (например, 5):")
        return

    db.update_user_setting(message.from_user.id, "min_profit", value)
    await state.set_state(FilterStates.max_listing_age)
    await message.answer(
        f"✅ Минимальная прибыль: <b>{value} TON</b>\n\n"
        f"Текущее максимальное время листинга: "
        f"<b>{db.get_user_settings(message.from_user.id)['max_listing_age']} мин</b>\n"
        "Введите <b>максимальное время листинга</b> в минутах (например, 120):",
        reply_markup=cancel_keyboard(),
    )


@router.message(FilterStates.max_listing_age)
async def process_max_listing_age(message: Message, state: FSMContext):
    """Обрабатывает максимальное время листинга и завершает настройку."""
    try:
        value = int(message.text)
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое положительное число минут (например, 120):")
        return

    db.update_user_setting(message.from_user.id, "max_listing_age", value)
    await state.clear()

    settings = db.get_user_settings(message.from_user.id)
    await message.answer(
        "✅ <b>Настройки сохранены!</b>\n\n"
        f"{format_settings(settings)}\n\n"
        "Теперь нажми <b>▶️ Запустить мониторинг</b>, чтобы начать поиск.",
        reply_markup=main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Кнопки главного меню
# ---------------------------------------------------------------------------

@router.message(F.text == "▶️ Запустить мониторинг")
async def start_monitoring(message: Message):
    """Включает мониторинг для пользователя."""
    user_id = message.from_user.id
    settings = db.get_user_settings(user_id)

    if not settings["collections"]:
        await message.answer(
            "⚠️ Сначала настройте коллекции через <b>📊 Настроить фильтры</b>.",
            reply_markup=main_keyboard(),
        )
        return

    db.update_user_setting(user_id, "monitoring_enabled", True)
    await message.answer(
        "✅ <b>Мониторинг запущен!</b>\n\n"
        f"Буду проверять коллекции каждые {config.MONITOR_INTERVAL_MINUTES} минут.\n"
        "Как только найду выгодный флип — сразу пришлю уведомление.",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "⏹️ Остановить")
async def stop_monitoring(message: Message):
    """Останавливает мониторинг для пользователя."""
    db.update_user_setting(message.from_user.id, "monitoring_enabled", False)
    await message.answer(
        "⏹️ <b>Мониторинг остановлен.</b>\n\n"
        "Можно изменить фильтры или запустить снова.",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == "📋 Мои настройки")
async def show_settings(message: Message):
    """Показывает текущие настройки пользователя."""
    settings = db.get_user_settings(message.from_user.id)
    status = "🟢 Активен" if settings["monitoring_enabled"] else "🔴 Остановлен"
    await message.answer(
        f"📋 <b>Мои настройки</b>\n\n"
        f"{format_settings(settings)}\n\n"
        f"Статус мониторинга: <b>{status}</b>",
        reply_markup=main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Отмена FSM
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "cancel_filters")
async def cancel_filters(callback: CallbackQuery, state: FSMContext):
    """Отменяет настройку фильтров."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Настройка отменена. Текущие фильтры сохранены."
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def format_settings(settings: Dict[str, Any]) -> str:
    """Форматирует настройки для отображения."""
    collections = ", ".join(settings["collections"]) if settings["collections"] else "не заданы"
    return (
        f"💰 Мин. цена: <b>{settings['min_price']} TON</b>\n"
        f"💰 Макс. цена: <b>{settings['max_price']} TON</b>\n"
        f"📦 Коллекции: <b>{collections}</b>\n"
        f"💎 Мин. прибыль: <b>{settings['min_profit']} TON</b>\n"
        f"⏱ Макс. время листинга: <b>{settings['max_listing_age']} мин</b>"
    )