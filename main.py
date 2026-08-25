import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from quest_service import QuestService


logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

ADMIN_ID = 5169764277

DB_FILE = "ohota.db"
mission_creation = {}
quest_service = QuestService(DB_FILE)

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
admin_menu = ReplyKeyboardMarkup(
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
        [
            KeyboardButton(text="👑 Админ-панель"),
        ],
    ],
    resize_keyboard=True,
)
admin_panel_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Создать миссию"),
            KeyboardButton(text="📋 Все миссии"),
        ],
        [
            KeyboardButton(text="👥 Участники"),
            KeyboardButton(text="📊 Статистика"),
        ],
        [
            KeyboardButton(text="💬 Поддержка"),
        ],
        [
            KeyboardButton(text="🔙 Главное меню"),
        ],
    ],
    resize_keyboard=True,
)

admin_menu = ReplyKeyboardMarkup(
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
        [
            KeyboardButton(text="👑 Админ-панель"),
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
    
    connection.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            reward_xp INTEGER DEFAULT 0,
            reward_score INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )
    """)
    
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mission_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            status TEXT DEFAULT 'joined',
            reward_given INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(mission_id, telegram_id)
        )
    """)
    try:
        connection.execute(
            "ALTER TABLE mission_participants "
            "ADD COLUMN reward_given INTEGER DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass
    
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id FROM missions LIMIT 1"
    )

    mission = cursor.fetchone()

    if mission is None:
        cursor.execute(
            """
            INSERT INTO missions
            (title, description, reward_xp, reward_score, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Первая охота",
                "Найди первого участника ОХОТЫ и приведи его в игру.",
                100,
                50,
                1,
            ),
        )

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
    mission_creation.pop(message.from_user.id, None)
    
    if message.from_user.id == ADMIN_ID:
        current_menu = admin_menu
    else:
        current_menu = main_menu
        
    await message.answer(
        f"🕵️ <b>ОХОТА</b>\n\n"
        f"Привет, <b>{message.from_user.first_name or 'охотник'}</b>!\n\n"
        f"👤 Твой профиль создан.\n\n"
        f"⭐ Очки: <b>{player[2]}</b>\n"
        f"⚡ Опыт: <b>{player[1]}</b>\n\n"
        f"🎯 Скоро здесь появятся первые миссии.",
        reply_markup=current_menu,
        )
@dp.message(F.text == "👑 Админ-панель")
async def admin_panel_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У тебя нет доступа.")
        return

    await message.answer(
        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выбери нужный раздел:",
        reply_markup=admin_panel_menu,
    )
@dp.message(F.text == "➕ Создать миссию")
async def create_mission_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У тебя нет доступа.")
        return
        
    mission_creation[message.from_user.id] = {}

    await message.answer(
        "➕ <b>СОЗДАНИЕ МИССИИ</b>\n\n"
        "Напиши название новой миссии."
    )
        
@dp.message(F.text == "🎯 Миссии")
async def missions_handler(message: Message):
    connection = sqlite3.connect(DB_FILE)

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, title, description, reward_xp, reward_score
        FROM missions
        WHERE active = 1
        ORDER BY id ASC
        """
    )

    missions = cursor.fetchall()

    connection.close()

    if not missions:
        await message.answer(
            "🎯 <b>МИССИИ</b>\n\n"
            "Сейчас активных миссий нет."
        )
        return

    text = "🎯 <b>АКТИВНЫЕ МИССИИ</b>\n\n"

    for mission in missions:
        mission_id, title, description, reward_xp, reward_score = mission

        connection = sqlite3.connect(DB_FILE)
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM mission_participants
            WHERE mission_id = ? AND telegram_id = ?
            """,
            (mission_id, message.from_user.id),
        )

        participation = cursor.fetchone()

        connection.close()

        if participation:
            button_text = "🏁 Выполнить миссию"
            callback_action = f"complete_mission:{mission_id}"
        else:
            button_text = "▶️ Участвовать"
            callback_action = f"join_mission:{mission_id}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=callback_action
                    )
                ]
            ]
        )

        await message.answer(
            f"🎯 <b>Миссия №{mission_id}</b>\n"
            f"<b>{title}</b>\n\n"
            f"{description}\n\n"
            f"⚡ Опыт: <b>{reward_xp}</b>\n"
            f"⭐ Очки: <b>{reward_score}</b>",
            reply_markup=keyboard,
        )

    await message.answer(text)
@dp.callback_query(F.data.startswith("join_mission:"))
async def join_mission_handler(callback):
    mission_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    connection = sqlite3.connect(DB_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO mission_participants
        (mission_id, telegram_id)
        VALUES (?, ?)
        """,
        (mission_id, telegram_id),
    )

    connection.commit()

    cursor.execute(
        """
        SELECT changes()
        """
    )

    inserted = cursor.fetchone()[0]

    connection.close()

    if inserted:
        await callback.answer("✅ Ты участвуешь в миссии!")
        await callback.message.answer(
            "🎯 <b>Участие подтверждено!</b>\n\n"
            "Теперь выполни условия миссии."
        )
    else:
        await callback.answer("ℹ️ Ты уже участвуешь в этой миссии.")
@dp.callback_query(F.data.startswith("complete_mission:"))
async def complete_mission_handler(callback):
    mission_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id

    connection = sqlite3.connect(DB_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT status, reward_given
        FROM mission_participants
        WHERE mission_id = ? AND telegram_id = ?
        """,
        (mission_id, telegram_id),
    )

    participation = cursor.fetchone()

    if not participation:
        connection.close()
        await callback.answer("❌ Ты не участвуешь в этой миссии.")
        return

    status, reward_given = participation

    if reward_given:
        connection.close()
        await callback.answer("ℹ️ Награда уже была получена.")
        return

    if status != "completed":
        cursor.execute(
            """
            UPDATE mission_participants
            SET status = 'completed'
            WHERE mission_id = ? AND telegram_id = ?
            """,
            (mission_id, telegram_id),
        )

    cursor.execute(
        """
        SELECT reward_xp, reward_score
        FROM missions
        WHERE id = ?
        """,
        (mission_id,),
    )

    reward = cursor.fetchone()

    if not reward:
        connection.close()
        await callback.answer("❌ Миссия не найдена.")
        return

    reward_xp, reward_score = reward

    cursor.execute(
        """
        UPDATE players
        SET xp = xp + ?,
            score = score + ?
        WHERE telegram_id = ?
        """,
        (reward_xp, reward_score, telegram_id),
    )

    cursor.execute(
        """
        UPDATE mission_participants
        SET reward_given = 1,
            status = 'completed'
        WHERE mission_id = ? AND telegram_id = ?
        """,
        (mission_id, telegram_id),
    )

    connection.commit()
    connection.close()

    await callback.answer("🎁 Награда получена!")

    await callback.message.answer(
        "🏁 <b>МИССИЯ ВЫПОЛНЕНА!</b>\n\n"
        f"⚡ XP: <b>+{reward_xp}</b>\n"
        f"⭐ Очки: <b>+{reward_score}</b>"
    )
@dp.message(F.text == "👤 Мой профиль")
async def profile_handler(message: Message):
    player = get_or_create_player(message)

    connection = sqlite3.connect(DB_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM mission_participants
        WHERE telegram_id = ?
          AND status = 'completed'
        """,
        (message.from_user.id,),
    )

    completed_missions = cursor.fetchone()[0]

    connection.close()

    await message.answer(
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Имя: <b>{message.from_user.first_name or 'Не указано'}</b>\n"
        f"⭐ Очки: <b>{player[2]}</b>\n"
        f"⚡ Опыт: <b>{player[1]}</b>\n"
        f"🎯 Выполнено миссий: <b>{completed_missions}</b>"
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
    
@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "⛔ У тебя нет доступа к админ-панели."
        )
        return

    await message.answer(
        "👑 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Доступ разрешён."
    )
@dp.callback_query(F.data == "cancel_create_mission")
async def cancel_create_mission_handler(callback):
    user_id = callback.from_user.id

    mission_creation.pop(user_id, None)

    await callback.answer("❌ Создание миссии отменено.")

    await callback.message.answer(
        "❌ <b>СОЗДАНИЕ МИССИИ ОТМЕНЕНО</b>"
    )
@dp.callback_query(F.data == "confirm_create_mission")
async def confirm_create_mission_handler(callback):
    user_id = callback.from_user.id

    if user_id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    if user_id not in mission_creation:
        await callback.answer(
            "❌ Данные миссии не найдены.",
            show_alert=True
        )
        return

    data = mission_creation[user_id]

    connection = sqlite3.connect(DB_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO missions
        (title, description, reward_xp, reward_score, active)
        VALUES (?, ?, ?, ?, 1)
        """,
        (
            data["title"],
            data["description"],
            data["reward_xp"],
            data["reward_score"],
        ),
    )

    connection.commit()
    mission_id = cursor.lastrowid
    connection.close()

    mission_creation.pop(user_id, None)

    await callback.answer("✅ Миссия создана!")

    await callback.message.answer(
        "🎯 <b>МИССИЯ СОЗДАНА!</b>\n\n"
        f"🆔 №{mission_id}\n"
        f"🎯 <b>{data['title']}</b>\n"
        f"📄 {data['description']}\n\n"
        f"⚡ XP: <b>{data['reward_xp']}</b>\n"
        f"⭐ Очки: <b>{data['reward_score']}</b>"
    )
@dp.message()
async def mission_creation_handler(message: Message):
    user_id = message.from_user.id

    if user_id != ADMIN_ID:
        return

    if user_id not in mission_creation:
        return

    data = mission_creation[user_id]

    if "title" not in data:
        data["title"] = message.text

        await message.answer(
            "📄 <b>ОПИСАНИЕ МИССИИ</b>\n\n"
            "Теперь напиши описание новой миссии."
        )
        return

    if "description" not in data:
        data["description"] = message.text

        await message.answer(
            "⚡ <b>НАГРАДА XP</b>\n\n"
            "Напиши количество опыта за выполнение миссии.\n\n"
            "Например: <b>100</b>"
        )
        return

    if "reward_xp" not in data:
        try:
            reward_xp = int(message.text)
        except ValueError:
            await message.answer(
                "❌ Введи число.\n\n"
                "Например: <b>100</b>"
            )
            return

        if reward_xp < 0:
            await message.answer(
                "❌ XP не может быть отрицательным."
            )
            return

        data["reward_xp"] = reward_xp

        await message.answer(
            "⭐ <b>НАГРАДА ОЧКИ</b>\n\n"
            "Напиши количество очков за выполнение миссии.\n\n"
            "Например: <b>50</b>"
        )
        return
    if "reward_score" not in data:
        try:
            reward_score = int(message.text)
        except ValueError:
            await message.answer(
                "❌ Введи число.\n\n"
                "Например: <b>50</b>"
            )
            return

        if reward_score < 0:
            await message.answer(
                "❌ Очки не могут быть отрицательными."
            )
            return

        data["reward_score"] = reward_score

        keyboard = InlineKeyboardMarkup(

            inline_keyboard=[

                [

                    InlineKeyboardButton(

                        text="✅ Создать миссию",

                        callback_data="confirm_create_mission"

                    ),

                    InlineKeyboardButton(

                        text="❌ Отмена",

                        callback_data="cancel_create_mission"

                    )

                ]

            ]

        )

        await message.answer(

            "✅ <b>ДАННЫЕ МИССИИ СОБРАНЫ</b>\n\n"

            f"🎯 Название: <b>{data['title']}</b>\n"

            f"📄 Описание: {data['description']}\n\n"

            f"⚡ XP: <b>{data['reward_xp']}</b>\n"

            f"⭐ Очки: <b>{data['reward_score']}</b>\n\n"

            "Подтвердить создание миссии?",

            reply_markup=keyboard

        )

        return
        
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