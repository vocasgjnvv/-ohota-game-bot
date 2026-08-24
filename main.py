import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton


logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")


DB_FILE = "ohota.db"

dp = Dispatcher()
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🕵️ Начать"),
            KeyboardButton(text="🎯 Миссии"),
        ],
        [
            KeyboardButton(text="👤 Мой профиль"),
            KeyboardButton(text="🏆 Рейтинг"),
        ],
        [
            KeyboardButton(text="📜 Правила"),
            KeyboardButton(text="💬 Чат"),
        ],
    ],
    resize_keyboard=True,
)


def init_db():
    connection = sqlite3.connect(DB_FILE)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            xp INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0
        )
    """)

    connection.commit()
    connection.close()


def get_or_create_player(message: Message):
    user = message.from_user

    connection = sqlite3.connect(DB_FILE)

    cursor = connection.cursor()

    cursor.execute(
        "SELECT id, xp, score FROM players WHERE telegram_id = ?",
        (user.id,),
    )

    player = cursor.fetchone()

    if player is None:
        cursor.execute(
            """
            INSERT INTO players
            (telegram_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            """,
            (
                user.id,
                user.username,
                user.first_name,
                user.last_name,
            ),
        )

        connection.commit()

        player = (
            cursor.lastrowid,
            0,
            0,
        )

    else:
        cursor.execute(
            """
            UPDATE players
            SET username = ?,
                first_name = ?,
                last_name = ?
            WHERE telegram_id = ?
            """,
            (
                user.username,
                user.first_name,
                user.last_name,
                user.id,
            ),
        )

        connection.commit()

    connection.close()

    return player


@dp.message(CommandStart())
async def start_handler(message: Message):
    player = get_or_create_player(message)

    await message.answer(
        f"🕵️ <b>ОХОТА</b>\n\n"
        f"Привет, <b>{message.from_user.first_name or 'охотник'}</b>!\n\n"
        f"👤 Твой профиль создан.\n\n"
        f"⭐ Очки: <b>{player[2]}</b>\n"
        f"⚡ Опыт: <b>{player[1]}</b>\n\n"
        f"🎯 Скоро здесь появятся первые миссии.",
        reply_markup=main_menu, 
        )
@dp.message(F.text == "🎯 Миссии")
async def missions_handler(message: Message):
    await message.answer(
        "🎯 <b>МИССИИ</b>\n\n"
        "Пока активных миссий нет."
    )
@dp.message(F.text == "👤 Мой профиль")
async def profile_handler(message: Message):
    player = get_or_create_player(message)

    await message.answer(
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Имя: <b>{message.from_user.first_name or 'Не указано'}</b>\n"
        f"⭐ Очки: <b>{player[2]}</b>\n"
        f"⚡ Опыт: <b>{player[1]}</b>"
    )
@dp.message(F.text == "🏆 Рейтинг")
async def rating_handler(message: Message):
    connection = sqlite3.connect(DB_FILE)

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT first_name, username, score
        FROM players
        ORDER BY score DESC, xp DESC
        LIMIT 10
        """
    )

    players = cursor.fetchall()

    connection.close()

    if not players:
        await message.answer(
            "🏆 <b>РЕЙТИНГ</b>\n\n"
            "Пока игроков нет."
        )
        return

    text = "🏆 <b>РЕЙТИНГ</b>\n\n"

    for position, player in enumerate(players, start=1):
        first_name, username, score = player

        name = first_name or username or "Охотник"

        text += (
            f"{position}. {name} — "
            f"⭐ <b>{score}</b>\n"
        )

    await message.answer(text)

@dp.message(F.text == "📜 Правила")
async def rules_handler(message: Message):
    await message.answer(
        "📜 <b>ПРАВИЛА ОХОТЫ</b>\n\n"
        "1️⃣ Выполняй доступные миссии.\n"
        "2️⃣ За выполнение миссий получай очки и опыт.\n"
        "3️⃣ Не используй запрещённые способы выполнения заданий.\n"
        "4️⃣ Не пытайся обмануть систему.\n"
        "5️⃣ Следи за новыми миссиями и участвуй в них.\n\n"
        "🎯 Главная цель — выполнять миссии и подниматься в рейтинге."
    )
@dp.message(F.text == "💬 Чат")
async def chat_handler(message: Message):
    await message.answer(
        "💬 <b>ЧАТ</b>\n\n"
        "Чат пока не подключён.\n"
        "Скоро здесь появится ссылка на чат ОХОТЫ."
    )
    

@dp.message()
async def message_handler(message: Message):
    await message.answer(
        "🕵️ <b>ОХОТА</b>\n\n"
        "Используй команду /start."
    )


async def main():
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    logging.info("ОХОТА запускается...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())