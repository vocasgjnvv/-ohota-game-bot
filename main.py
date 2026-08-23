import os
import sqlite3
import asyncio
import logging
import re
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================
# НАСТРОЙКИ
# =========================

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

DB_PATH = os.getenv("DB_PATH", "ohota.db")

if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавь токен бота в переменную окружения BOT_TOKEN."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(TOKEN)
dp = Dispatcher()


# =========================
# БАЗА ДАННЫХ
# =========================

def conn():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with closing(conn()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            game_number INTEGER UNIQUE NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            xp INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            investigations INTEGER DEFAULT 0,
            best_place INTEGER,
            interactions INTEGER DEFAULT 0,
            accusations INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS registrations (
            telegram_id INTEGER,
            hunt_code TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_id, hunt_code)
        );
        """)
        db.commit()


def get_user(telegram_id):
    with closing(conn()) as db:
        return db.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()


def create_user(telegram_id, nickname):
    with closing(conn()) as db:
        last_number = db.execute(
            "SELECT COALESCE(MAX(game_number), 1000) FROM users"
        ).fetchone()[0]

        game_number = last_number + 1

        db.execute(
            """
            INSERT INTO users
            (telegram_id, game_number, nickname)
            VALUES (?, ?, ?)
            """,
            (telegram_id, game_number, nickname)
        )

        db.commit()

        return game_number


# =========================
# КЛАВИАТУРЫ
# =========================

def main_menu():
    kb = InlineKeyboardBuilder()

    buttons = [
        ("🎯 Начать охоту", "hunt"),
        ("👤 Мой профиль", "profile"),
        ("🏆 Рейтинг", "rating"),
        ("📜 Правила", "rules"),
        ("💬 Чат", "chat"),
    ]

    for text, callback in buttons:
        kb.button(text=text, callback_data=callback)

    kb.adjust(1)

    return kb.as_markup()


def back_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="menu")
    return kb.as_markup()


# =========================
# /START
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    user = get_user(message.from_user.id)

    if user:
        await message.answer(
            f"🕵️ <b>ОХОТА</b>\n\n"
            f"С возвращением, <b>{user['nickname']}</b>!\n"
            f"Твой игровой номер: <b>#{user['game_number']}</b>.",
            reply_markup=main_menu()
        )
        return

    await message.answer(
        "🕵️ <b>Добро пожаловать в «ОХОТУ»!</b>\n\n"
        "Это онлайн-игра, где игроки расследуют дела, "
        "получают зацепки и соревнуются друг с другом.\n\n"
        "Для начала придумай игровой псевдоним.\n\n"
        "📌 От 3 до 20 символов.\n"
        "📌 Буквы, цифры, пробел, дефис или _.\n"
        "📌 Без оскорблений."
    )


# =========================
# РЕГИСТРАЦИЯ
# =========================

@dp.message(F.text)
async def registration(message: Message):
    if message.text.startswith("/"):
        return

    user = get_user(message.from_user.id)

    if user:
        await message.answer(
            "Выбери действие:",
            reply_markup=main_menu()
        )
        return

    nickname = message.text.strip()

    if not re.fullmatch(
        r"[A-Za-zА-Яа-яЁё0-9 _-]{3,20}",
        nickname
    ):
        await message.answer(
            "❌ Псевдоним должен содержать от 3 до 20 символов.\n"
            "Разрешены буквы, цифры, пробел, дефис и _."
        )
        return

    bad_words = (
        "хуй",
        "пизд",
        "еб",
        "бляд",
        "сука",
        "дебил",
    )

    normalized = nickname.lower().replace("ё", "е")

    if any(word in normalized for word in bad_words):
        await message.answer(
            "❌ Такой псевдоним нельзя использовать."
        )
        return

    try:
        game_number = create_user(
            message.from_user.id,
            nickname
        )

    except sqlite3.IntegrityError:
        await message.answer(
            "❌ Этот псевдоним уже занят.\n"
            "Придумай другой."
        )
        return

    await message.answer(
        f"✅ <b>Регистрация завершена!</b>\n\n"
        f"🎫 Игровой номер: <b>#{game_number}</b>\n"
        f"👤 Псевдоним: <b>{nickname}</b>\n\n"
        f"Добро пожаловать в «ОХОТУ».",
        reply_markup=main_menu()
    )


# =========================
# ГЛАВНОЕ МЕНЮ
# =========================

@dp.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🕵️ <b>Главное меню «ОХОТЫ»</b>",
        reply_markup=main_menu()
    )

    await callback.answer()


# =========================
# ПРОФИЛЬ
# =========================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)

    if not user:
        await callback.message.edit_text(
            "❌ Сначала отправь /start.",
            reply_markup=back_button()
        )

        await callback.answer()
        return

    best_place = user["best_place"] or "—"

    text = (
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"🎫 Номер: <b>#{user['game_number']}</b>\n"
        f"🕵️ Псевдоним: <b>{user['nickname']}</b>\n\n"
        f"⭐ XP: <b>{user['xp']}</b>\n"
        f"🏆 Победы: <b>{user['wins']}</b>\n"
        f"🔎 Расследования: <b>{user['investigations']}</b>\n"
        f"🤝 Взаимодействия: <b>{user['interactions']}</b>\n"
        f"⚠️ Обвинения: <b>{user['accusations']}</b>\n"
        f"🥇 Лучшее место: <b>{best_place}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# =========================
# РЕЙТИНГ
# =========================

@dp.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery):
    with closing(conn()) as db:
        rows = db.execute(
            """
            SELECT nickname, xp, wins
            FROM users
            ORDER BY xp DESC, wins DESC, game_number ASC
            LIMIT 10
            """
        ).fetchall()

    if not rows:
        text = "🏆 <b>РЕЙТИНГ</b>\n\nПока игроков нет."
    else:
        lines = []

        for index, row in enumerate(rows, 1):
            lines.append(
                f"<b>{index}.</b> {row['nickname']} — "
                f"⭐ {row['xp']} XP"
            )

        text = (
            "🏆 <b>РЕЙТИНГ «ОХОТЫ»</b>\n\n"
            + "\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# =========================
# ПРАВИЛА
# =========================

@dp.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery):
    text = (
        "📜 <b>ПРАВИЛА «ОХОТЫ»</b>\n\n"
        "🎯 Каждая охота — отдельное расследование.\n\n"
        "⏱ Игроки получают ограниченное время "
        "на расследование дела.\n\n"
        "🔎 Можно искать зацепки.\n"
        "🤝 Можно взаимодействовать с другими игроками.\n"
        "⚠️ В конце расследования можно сделать обвинение.\n\n"
        "🏆 За результаты игрок получает XP и "
        "поднимается в рейтинге.\n\n"
        "Новые игровые механики будут добавляться "
        "по мере развития проекта."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# =========================
# ОХОТА
# =========================

@dp.callback_query(F.data == "hunt")
async def hunt(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🎯 УЧАСТВОВАТЬ",
        callback_data="join"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="menu"
    )

    kb.adjust(1)

    text = (
        "🕵️ <b>ОХОТА №001</b>\n\n"
        "📖 <b>«Последний рейс»</b>\n\n"
        "⏱ Продолжительность: <b>60 минут</b>\n"
        "💰 Участие: <b>бесплатно</b>\n"
        "🏆 Награда: <b>XP + рейтинг</b>\n\n"
        "Охота находится в подготовке.\n"
        "Нажми «УЧАСТВОВАТЬ», чтобы записаться."
    )

    await callback.message.edit_text(
        text,
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# =========================
# УЧАСТИЕ В ОХОТЕ
# =========================

@dp.callback_query(F.data == "join")
async def join_hunt(callback: CallbackQuery):
    user = get_user(callback.from_user.id)

    if not user:
        await callback.message.edit_text(
            "❌ Сначала зарегистрируйся через /start.",
            reply_markup=back_button()
        )

        await callback.answer()
        return

    with closing(conn()) as db:
        try:
            db.execute(
                """
                INSERT INTO registrations
                (telegram_id, hunt_code)
                VALUES (?, ?)
                """,
                (callback.from_user.id, "001")
            )

            db.commit()

            text = (
                "✅ <b>Ты зарегистрирован!</b>\n\n"
                "🎯 ОХОТА №001\n"
                "📖 «Последний рейс»\n\n"
                "Когда охота будет запущена, "
                "ты сможешь принять участие."
            )

        except sqlite3.IntegrityError:
            text = (
                "ℹ️ <b>Ты уже зарегистрирован</b>\n\n"
                "Ты уже записан на ОХОТУ №001."
            )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# =========================
# ЧАТ
# =========================

@dp.callback_query(F.data == "chat")
async def chat(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()

    kb.button(
        text="💬 Открыть чат",
        url="https://t.me/ohota_online_chat"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="menu"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        "💬 <b>Официальный чат «ОХОТЫ»</b>\n\n"
        "Общайся с другими игроками.",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# =========================
# ЗАПУСК
# =========================

async def main():
    init_db()

    logging.info("Бот «ОХОТА» запускается...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
