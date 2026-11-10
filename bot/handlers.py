"""Telegram bot handlers for Magnit promo monitoring."""

import logging
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from .config import DEFAULT_MIN_DISCOUNT, MAX_STORES
from .database import Database
from .magnit_api import MagnitClient
from .store_search import StoreSearcher

logger = logging.getLogger(__name__)

router = Router()


class StoreStates(StatesGroup):
    waiting_for_store_query = State()


class DiscountStates(StatesGroup):
    waiting_for_discount = State()


# ----------------------------------------------------------------------
# Keyboards
# ----------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Мои магазины", callback_data="menu_stores")],
            [InlineKeyboardButton(text="💰 Порог скидки", callback_data="menu_discount")],
            [InlineKeyboardButton(text="⚙️ Мониторинг", callback_data="menu_monitor")],
            [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu_help")],
        ]
    )


def stores_menu_keyboard(stores: list, has_space: bool) -> InlineKeyboardMarkup:
    buttons = []
    for store in stores:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 {store['store_address']} ({store['store_code']})",
                    callback_data=f"remove_store:{store['store_code']}",
                )
            ]
        )
    if has_space:
        buttons.append(
            [InlineKeyboardButton(text="➕ Добавить магазин", callback_data="add_store")]
        )
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def discount_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить порог", callback_data="change_discount")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
        ]
    )


# ----------------------------------------------------------------------
# Start / Help
# ----------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, magnit_client: MagnitClient) -> None:
    user_id = message.from_user.id
    await db.upsert_user(user_id)
    await state.clear()

    text = (
        "🛒 <b>Бот мониторинга акций Магнит</b>\n\n"
        "Я отслеживаю новые акции в ваших любимых магазинах «Магнит» "
        "и присылаю уведомления с фото товара, названием, скидкой и адресом магазина.\n\n"
        "Выберите действие:"
    )
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("stores"))
async def cmd_stores(message: Message, state: FSMContext, db: Database) -> None:
    user_id = message.from_user.id
    await state.clear()
    await db.upsert_user(user_id)

    stores = await db.get_stores(user_id)
    if not stores:
        text = (
            "🏪 <b>Ваши магазины</b>\n\n"
            "У вас пока нет добавленных магазинов.\n"
            "Можно добавить до 10 магазинов."
        )
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить магазин", callback_data="add_store")],
                    [InlineKeyboardButton(text="◀️ В меню", callback_data="back_main")],
                ]
            ),
        )
        return

    text = f"🏪 <b>Ваши магазины</b> ({len(stores)}/{MAX_STORES}):\n\n"
    for store in stores:
        text += f"• {store['store_address']}\n"
    await message.answer(
        text, reply_markup=stores_menu_keyboard(stores, len(stores) < MAX_STORES)
    )


@router.message(Command("discount"))
async def cmd_discount(message: Message, state: FSMContext, db: Database) -> None:
    user_id = message.from_user.id
    await state.clear()
    await db.upsert_user(user_id)

    min_disc = await db.get_min_discount(user_id)
    text = (
        f"💰 <b>Порог скидки</b>\n\n"
        f"Минимальная скидка для уведомлений: <b>{min_disc}%</b>"
    )
    await message.answer(text, reply_markup=discount_menu_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/stores — управление магазинами\n"
        "/discount — порог скидки\n"
        "/help — эта справка\n\n"
        "<b>Как это работает:</b>\n"
        "1. Добавьте до 10 магазинов по адресу.\n"
        "2. Установите минимальный процент скидки.\n"
        "3. Бот будет проверять акции и присылать новые.\n\n"
        "Магазин можно добавить, набрав часть адреса (например, «Омск» или «Омск, улица Ленина»)."
    )
    await message.answer(text)


@router.callback_query(F.data == "menu_help")
async def cb_help(callback: CallbackQuery) -> None:
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/stores — управление магазинами\n"
        "/discount — порог скидки\n"
        "/help — эта справка\n\n"
        "<b>Как это работает:</b>\n"
        "1. Добавьте до 10 магазинов по адресу.\n"
        "2. Установите минимальный процент скидки.\n"
        "3. Бот будет проверять акции и присылать новые.\n\n"
        "Магазин можно добавить, набрав часть адреса (например, «Омск» или «Омск, улица Ленина»)."
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]]
    ))
    await callback.answer()


# ----------------------------------------------------------------------
# Stores
# ----------------------------------------------------------------------

@router.callback_query(F.data == "menu_stores")
async def cb_stores(callback: CallbackQuery, db: Database) -> None:
    user_id = callback.from_user.id
    stores = await db.get_stores(user_id)
    has_space = len(stores) < MAX_STORES

    if not stores:
        text = (
            "🏪 <b>Ваши магазины</b>\n\n"
            "У вас пока нет добавленных магазинов.\n"
            "Можно добавить до 10 магазинов.\n\n"
            "Нажмите «Добавить магазин» и введите часть адреса."
        )
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить магазин", callback_data="add_store")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
                ]
            ),
        )
    else:
        text = f"🏪 <b>Ваши магазины</b> ({len(stores)}/{MAX_STORES}):\n\n"
        for store in stores:
            text += f"• {store['store_address']} (код: {store['store_code']})\n"
        await callback.message.edit_text(
            text,
            reply_markup=stores_menu_keyboard(stores, has_space),
        )
    await callback.answer()


@router.callback_query(F.data == "add_store")
async def cb_add_store(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StoreStates.waiting_for_store_query)
    await callback.message.edit_text(
        "➕ <b>Добавление магазина</b>\n\n"
        "Введите город или часть адреса магазина «Магнит» "
        "(например, «Омск» или «Омск, улица Ленина»):\n\n"
        "Я найду ближайшие магазины — выберите нужный.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_stores")]]
        ),
    )
    await callback.answer()


@router.message(StoreStates.waiting_for_store_query)
async def process_store_query(
    message: Message, state: FSMContext, db: Database, store_searcher: StoreSearcher
) -> None:
    query = message.text.strip()
    user_id = message.from_user.id

    if len(query) < 2:
        await message.answer("⚠️ Введите минимум 2 символа для поиска.")
        return

    stores = await store_searcher.search_stores(query)
    if not stores:
        await message.answer(
            "😕 Магазины не найдены. Попробуйте другой адрес или город "
            "(например, «Омск» или «Омск, улица Ленина»)."
        )
        return

    # Save search results in FSM storage and reference them by index:
    # Telegram callback data is limited to 64 bytes, which is too small
    # for full Cyrillic addresses.
    await state.update_data(found_stores=stores)
    await state.set_state(None)

    # Build inline keyboard with found stores
    buttons = []
    for idx, store in enumerate(stores[:10]):
        address = store.get("address") or ""
        code = store.get("code") or ""
        distance = store.get("distance_km")
        store_type = store.get("store_type") or ""
        label = f"🏪 {address}"
        if distance is not None:
            label += f" — {distance} км"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"select_store:{idx}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="menu_stores")])

    await message.answer(
        f"🔍 Найдено магазинов: {len(stores[:10])}\nВыберите магазин:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("select_store:"))
async def cb_select_store(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("❌ Ошибка выбора магазина.")
        return

    idx = int(parts[1])
    state_data = await state.get_data()
    stores = state_data.get("found_stores") or []
    if idx >= len(stores):
        await callback.answer("❌ Результаты поиска устарели. Выполните поиск заново.")
        return

    store = stores[idx]
    store_code = str(store.get("code") or "")
    store_address = store.get("address") or ""
    if not store_code:
        await callback.answer("❌ Ошибка выбора магазина.")
        return

    user_id = callback.from_user.id

    count = await db.count_stores(user_id)
    if count >= MAX_STORES:
        await callback.message.edit_text(
            f"⚠️ Достигнут лимит в {MAX_STORES} магазинов. Удалите один, чтобы добавить новый.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="menu_stores")]]
            ),
        )
        await callback.answer()
        return

    success = await db.add_store(user_id, store_code, store_address)
    if success:
        text = (
            f"✅ Магазин добавлен: {store_address}\n"
            f"Код: {store_code}"
        )
    else:
        text = f"⚠️ Магазин уже добавлен: {store_address}"

    # Clear search results after successful selection
    await state.update_data(found_stores=None)

    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("remove_store:"))
async def cb_remove_store(callback: CallbackQuery, db: Database) -> None:
    store_code = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    removed = await db.remove_store(user_id, store_code)
    if removed:
        text = f"🗑 Магазин (код: {store_code}) удалён."
    else:
        text = "⚠️ Магазин не найден."

    # Re-show stores menu
    stores = await db.get_stores(user_id)
    has_space = len(stores) < MAX_STORES
    if not stores:
        await callback.message.edit_text(
            "🏪 <b>Ваши магазины</b>\n\nУ вас пока нет добавленных магазинов.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Добавить магазин", callback_data="add_store")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
                ]
            ),
        )
    else:
        text = f"🏪 <b>Ваши магазины</b> ({len(stores)}/{MAX_STORES}):\n\n"
        for store in stores:
            text += f"• {store['store_address']} (код: {store['store_code']})\n"
        await callback.message.edit_text(
            text,
            reply_markup=stores_menu_keyboard(stores, has_space),
        )
    await callback.answer()


# ----------------------------------------------------------------------
# Discount threshold
# ----------------------------------------------------------------------

@router.callback_query(F.data == "menu_discount")
async def cb_discount(callback: CallbackQuery, db: Database) -> None:
    user_id = callback.from_user.id
    min_disc = await db.get_min_discount(user_id)
    text = f"💰 <b>Порог скидки</b>\n\nМинимальная скидка для уведомлений: <b>{min_disc}%</b>\n\nНажмите «Изменить порог», чтобы задать новое значение (0–100)."
    await callback.message.edit_text(text, reply_markup=discount_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "change_discount")
async def cb_change_discount(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DiscountStates.waiting_for_discount)
    await callback.message.edit_text(
        "✏️ <b>Изменение порога скидки</b>\n\n"
        "Введите минимальный процент скидки (целое число от 0 до 100):\n\n"
        "Например: 10 — только скидки 10% и выше.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu_discount")]]
        ),
    )
    await callback.answer()


@router.message(DiscountStates.waiting_for_discount)
async def process_discount(message: Message, state: FSMContext, db: Database) -> None:
    text = message.text.strip()
    try:
        value = int(text)
    except ValueError:
        await message.answer("⚠️ Введите целое число (например, 10).")
        return

    if value < 0 or value > 100:
        await message.answer("⚠️ Значение должно быть от 0 до 100.")
        return

    user_id = message.from_user.id
    await db.set_min_discount(user_id, value)
    await state.clear()

    await message.answer(
        f"✅ Порог скидки установлен: <b>{value}%</b>\n\n"
        "Теперь вы будете получать уведомления только о скидках этого процента и выше.",
        reply_markup=main_menu_keyboard(),
    )


# ----------------------------------------------------------------------
# Monitoring toggle / info
# ----------------------------------------------------------------------

@router.callback_query(F.data == "menu_monitor")
async def cb_monitor(callback: CallbackQuery, db: Database) -> None:
    user_id = callback.from_user.id
    stores = await db.get_stores(user_id)
    min_disc = await db.get_min_discount(user_id)

    text = (
        f"⚙️ <b>Мониторинг акций</b>\n\n"
        f"Магазинов отслеживается: {len(stores)}/{MAX_STORES}\n"
        f"Порог скидки: {min_disc}%\n\n"
        "Бот проверяет акции в фоновом режиме и присылает новые.\n"
        "Чтобы получать уведомления, добавьте хотя бы один магазин."
    )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


# ----------------------------------------------------------------------
# Back to main
# ----------------------------------------------------------------------

@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🛒 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()