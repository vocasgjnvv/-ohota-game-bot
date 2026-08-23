import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from html import escape

import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Максимальное время одной охоты — 60 минут
GAME_TIME = 60 * 60

# Имя базы данных
DB_NAME = "ohota.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("ohota")

# ============================================================
# BOT
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь переменную окружения BOT_TOKEN."
    )

# Настройки aiohttp-сессии можно изменить через переменные окружения:
# OHOTA_AIO_LIMIT, OHOTA_AIO_KEEPALIVE, OHOTA_AIO_TIMEOUT
aio_limit = int(os.getenv("OHOTA_AIO_LIMIT", "100"))
aio_keepalive = int(os.getenv("OHOTA_AIO_KEEPALIVE", "75"))
aio_timeout_total = int(os.getenv("OHOTA_AIO_TIMEOUT", "60"))

# Глобальные переменные для сессии и бота
session = None
bot = None

async def init_bot():
    """Инициализирует aiohttp сессию и бота внутри async контекста"""
    global session, bot
    
    connector = aiohttp.TCPConnector(limit=aio_limit, keepalive_timeout=aio_keepalive)
    timeout = aiohttp.ClientTimeout(total=aio_timeout_total)
    session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    
    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

dp = Dispatcher(
    storage=MemoryStorage()
)

# ============================================================
# DATABASE
# ============================================================

def get_db():
    connection = sqlite3.connect(DB_NAME, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS support (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                answered INTEGER DEFAULT 0,
                answer TEXT DEFAULT '',
                answered_at TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS beta_testers (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                time_seconds INTEGER NOT NULL,
                clues INTEGER DEFAULT 0,
                interactions INTEGER DEFAULT 0,
                finished_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES ('beta_mode', '0')
        """)

        connection.commit()
    finally:
        connection.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_setting(key, default=""):
    connection = get_db()
    try:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        ).fetchone()
        if row is None:
            return default
        return row["value"]
    finally:
        connection.close()


def set_setting(key, value):
    connection = get_db()
    try:
        connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value))
        )
        connection.commit()
    finally:
        connection.close()


def save_user(user_id, username, first_name):
    connection = get_db()
    try:
        connection.execute("""
            INSERT INTO users (user_id, username, first_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            user_id,
            username or "",
            first_name or "",
            datetime.now().isoformat()
        ))
        connection.commit()
    finally:
        connection.close()


def is_admin(user_id):
    return (ADMIN_ID != 0 and user_id == ADMIN_ID)


def is_beta_tester(user_id):
    if is_admin(user_id):
        return True
    connection = get_db()
    try:
        row = connection.execute(
            "SELECT user_id FROM beta_testers WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def beta_mode_enabled():
    return get_setting("beta_mode", "0") == "1"


# ============================================================
# ACTIVE GAMES (in-memory)
# ============================================================

# games[user_id] = {
#   "step": int,
#   "started": monotonic_timestamp,
#   "clues": set([...]),
#   "interactions": int,
#   "screen": str
# }

games = {}


def get_game(user_id):
    return games.get(user_id)


def delete_game(user_id):
    games.pop(user_id, None)


def game_elapsed(game):
    if not game:
        return 0
    return max(0, int(time.monotonic() - game["started"]))


def game_expired(game):
    return (game is not None) and (game_elapsed(game) >= GAME_TIME)


def game_header(game):
    elapsed = game_elapsed(game)
    remaining = max(0, GAME_TIME - elapsed)
    minutes = remaining // 60
    seconds = remaining % 60
    return (
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Осталось: <b>{minutes:02d}:{seconds:02d}</b>\n"
        f"🔎 Улик: <b>{len(game.get('clues', set()))}</b>\n"
        f"🤝 Взаимодействий: <b>{game.get('interactions', 0)}</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def save_active_game(user_id):
    game = games.get(user_id)
    if not game:
        return False
    game.setdefault("step", 0)
    game.setdefault("started", time.monotonic())
    game.setdefault("clues", set())
    game.setdefault("interactions", 0)
    game.setdefault("screen", "story")
    return True


async def check_game(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = get_game(user_id)
    if not game:
        await callback.answer("Активной охоты нет.", show_alert=True)
        return None
    if game_expired(game):
        await finish_game(callback, timeout=True)
        return None
    return game


# ============================================================
# STATES
# ============================================================

class SupportState(StatesGroup):
    waiting_message = State()


class SupportReplyState(StatesGroup):
    waiting_reply = State()


class BetaState(StatesGroup):
    waiting_user_id = State()


class ChatState(StatesGroup):
    waiting_message = State()


# ============================================================
# STORY CONTENT
# ============================================================

STORY = [
    {
        "title": "ПРОЛОГ",
        "text": """
🌑 <b>ОХОТА НАЧИНАЕТСЯ</b>

23:47.

Телефон вибрирует один раз.

На экране — неизвестный номер.

Ты открываешь сообщение.

<i>«Если ты это читаешь — значит, я уже исчез.

Не звони.

Не обращайся в полицию.

Просто найди место, о котором я говорил.»</i>

Ниже находится фотография заброшенного здания.

На двери виден свежий след.

Кто-то был здесь совсем недавно.

<b>Твоя охота начинается сейчас.</b>
"""
    },
    {
        "title": "ЭПИЗОД 1",
        "text": """
Ты подходишь к зданию.

Возле двери находятся:

👣 следы обуви
📄 кусок бумаги
🔩 металлическая деталь

Что-то здесь произошло совсем недавно.
"""
    },
    {
        "title": "ЭПИЗОД 2",
        "text": """
Следы заканчиваются возле старой стены.

На стене находится странный знак.

Кто-то специально оставил его здесь.
"""
    },
    {
        "title": "ЭПИЗОД 3",
        "text": """
За стеной находится небольшой проход.

На полу лежит разбитый телефон.

На экране осталось последнее уведомление:

<b>«Он знает, что ты пришёл.»</b>
"""
    },
    {
        "title": "ЭПИЗОД 4",
        "text": """
Из темноты появляется человек.

Ты видишь только силуэт, но чувствуешь присутствие.

Голос звучит знакомо, но в нём что-то изменилось...

<b>Игра подходит к развязке.</b>
"""
    },
]


async def main():
    """Главная функция для запуска бота"""
    await init_bot()
    init_db()
    
    # Здесь добавьте ваш остальной код инициализации
    # например, регистрация обработчиков и запуск полинга
    
    try:
        await dp.start_polling(bot)
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
