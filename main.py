import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from database import save_request
from config import BOT_TOKEN, ADMIN_IDS, WHITELIST
from utils import (
    validate_fio,
    validate_snils,
    validate_passport,
    get_nomerogram,
    get_olx,
    get_getcontact,
)
import asyncio

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Состояния бота
class Form(StatesGroup):
    waiting_for_phone = State()
    waiting_for_fio = State()
    waiting_for_snils_or_passport = State()

# Клавиатура для запроса номера телефона
phone_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Отправить номер телефона", request_contact=True)]
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

# Приветствие и запрос номера
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in WHITELIST:
        await message.answer("Извините, у вас нет доступа к этому боту.")
        return
    await message.answer(
        "Вас приветствует онлайн помощник.\nПожалуйста, отправьте свой номер телефона, используя кнопку ниже.",
        reply_markup=phone_kb,
    )
    await state.set_state(Form.waiting_for_phone)

# Получение номера телефона
@dp.message(F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    if message.from_user.id not in WHITELIST:
        await message.answer("Извините, у вас нет доступа к этому боту.")
        return
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await message.answer("Спасибо! Теперь введите ваше ФИО (только кириллица, минимум имя и фамилия):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Form.waiting_for_fio)

# Альтернативный вариант: номер в тексте
@dp.message(Form.waiting_for_phone)
async def process_phone_text(message: types.Message, state: FSMContext):
    if message.from_user.id not in WHITELIST:
        await message.answer("Извините, у вас нет доступа к этому боту.")
        return
    phone = message.text.strip()
    # Можно добавить проверку формата номера, например через regex
    await state.update_data(phone=phone)
    await message.answer("Спасибо! Теперь введите ваше ФИО (только кириллица, минимум имя и фамилия):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Form.waiting_for_fio)

# Получение ФИО и проверка
@dp.message(Form.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    if not validate_fio(fio):
        await message.answer("Пожалуйста, введите корректное ФИО (только кириллица, минимум имя и фамилия). Попробуйте ещё раз.")
        return
    await state.update_data(fio=fio)
    await message.answer("ФИО принято. Идёт поиск информации по номеру телефона... Пожалуйста, подождите...")
    data = await fetch_info(state)
    await message.answer(data)
    await message.answer("Теперь введите ваш СНИЛС или серию и номер паспорта (без пробелов).")
    await state.set_state(Form.waiting_for_snils_or_passport)

# Функция для сбора и формирования информации по номеру
async def fetch_info(state: FSMContext) -> str:
    data = await state.get_data()
    phone = data.get("phone", "")
    results = []
    # Парсим все источники
    results.append("Результаты поиска по номеру телефона:\n")
    results.append(get_nomerogram(phone))
    results.append(get_olx(phone))
    results.append(get_getcontact(phone))
    return "\n".join(results)

# Обработка СНИЛС или паспорта
@dp.message(Form.waiting_for_snils_or_passport)
async def process_snils_passport(message: types.Message, state: FSMContext):
    text = message.text.strip()
    snils = ""
    passport = ""
    if len(text) == 11 and text.replace(" ", "").isdigit():
        # Возможно СНИЛС
        if validate_snils(text):
            snils = text
        else:
            await message.answer("Неверный формат СНИЛС. Попробуйте ещё раз.")
            return
    elif len(text) in (9,10,12,14):  # например серия + номер паспорта может быть 10 цифр
        if validate_passport(text):
            passport = text
        else:
            await message.answer("Неверный формат паспорта. Попробуйте ещё раз.")
            return
    else:
        await message.answer("Пожалуйста, введите корректный СНИЛС (11 цифр) или паспорт (серия и номер без пробелов).")
        return

    await state.update_data(snils=snils, passport=passport)
    await message.answer("Идёт проверка данных в государственных базах и кредитной истории... Пожалуйста, подождите...")

    info = await fetch_documents_info(state)

    # Сохраняем заявку в базу
    user = message.from_user
    data = await state.get_data()
    save_request(user.id, user.username or "", data["phone"], data["fio"], snils, passport, info)

    # Отправляем администратору всю информацию в тексте
    admin_message = (
        f"Новая заявка:\n"
        f"Пользователь: @{user.username} (ID: {user.id})\n"
        f"Телефон: {data['phone']}\n"
        f"ФИО: {data['fio']}\n"
        f"СНИЛС: {snils if snils else '-'}\n"
        f"Паспорт: {passport if passport else '-'}\n\n"
        f"Результаты проверки:\n{info}"
    )
    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, admin_message)

    await message.answer("Спасибо! Ваша заявка принята и передана на проверку.")
    await state.clear()

async def fetch_documents_info(state: FSMContext) -> str:
    data = await state.get_data()
    snils = data.get("snils", "")
    passport = data.get("passport", "")
    results = []
    # Тут добавь парсинг и проверку госбаз, МВД, кредитных историй
    # Поскольку прямой доступ к ним сложен, можно реализовать заглушки или интеграцию с API, если будут.
    # Пока заглушка:
    if snils:
        results.append(f"Проверка СНИЛС: {snils} - (данные из госбаз не реализованы)")
    if passport:
        results.append(f"Проверка паспорта: {passport} - (данные из госбаз не реализованы)")
    results.append("Кредитная история: (данные не реализованы)")

    return "\n".join(results)

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
