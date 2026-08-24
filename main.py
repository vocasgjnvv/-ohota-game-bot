import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
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

bot = Bot(
    token=BOT_TOKEN,
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
    connection = sqlite3.connect(
        DB_NAME,
        timeout=30
    )
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
            INSERT OR IGNORE INTO settings (
                key,
                value
            )
            VALUES (
                'beta_mode',
                '0'
            )
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
            """
            SELECT value
            FROM settings
            WHERE key = ?
            """,
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
            INSERT INTO settings (
                key,
                value
            )
            VALUES (?, ?)

            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
            """,
            (key, str(value))
        )

        connection.commit()

    finally:
        connection.close()


def save_user(
    user_id,
    username,
    first_name
):
    connection = get_db()

    try:
        connection.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                first_name,
                created_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (
                user_id,
                username or "",
                first_name or "",
                datetime.now().isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()


def is_admin(user_id):
    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


def is_beta_tester(user_id):

    if is_admin(user_id):
        return True

    connection = get_db()

    try:
        row = connection.execute(
            """
            SELECT user_id
            FROM beta_testers
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

        return row is not None

    finally:
        connection.close()


def beta_mode_enabled():
    return get_setting(
        "beta_mode",
        "0"
    ) == "1"


# ============================================================
# ACTIVE GAMES
# ============================================================

games = {}


def get_game(user_id):
    return games.get(user_id)


def delete_game(user_id):
    games.pop(user_id, None)


def game_elapsed(game):

    if not game:
        return 0

    return max(
        0,
        int(
            time.monotonic()
            - game["started"]
        )
    )


def game_expired(game):

    return (
        game is not None
        and game_elapsed(game) >= GAME_TIME
    )


def game_header(game):

    elapsed = game_elapsed(game)

    remaining = max(
        0,
        GAME_TIME - elapsed
    )

    minutes = remaining // 60
    seconds = remaining % 60

    return (
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Осталось: "
        f"<b>{minutes:02d}:{seconds:02d}</b>\n"
        f"🔎 Улик: "
        f"<b>{len(game.get('clues', set()))}</b>\n"
        f"🤝 Взаимодействий: "
        f"<b>{game.get('interactions', 0)}</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def save_active_game(user_id):

    game = games.get(user_id)

    if not game:
        return False

    game.setdefault(
        "step",
        0
    )

    game.setdefault(
        "started",
        time.monotonic()
    )

    game.setdefault(
        "clues",
        set()
    )

    game.setdefault(
        "interactions",
        0
    )

    game.setdefault(
        "screen",
        "story"
    )

    return True


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

Ты видишь только силуэт,
но чувствуешь присутствие.

Голос звучит знакомо,
но в нём что-то изменилось...

<b>Игра подходит к развязке.</b>
"""
    },
]


# ============================================================
# KEYBOARDS
# ============================================================


def main_menu(user_id):

    buttons = [
        [
            InlineKeyboardButton(
                text="🕵️ Начать",
                callback_data="game_start"
            ),
            InlineKeyboardButton(
                text="🎯 Миссии",
                callback_data="missions"
            )
        ],

        [
            InlineKeyboardButton(
                text="👤 Мой профиль",
                callback_data="profile"
            ),
            InlineKeyboardButton(
                text="🏆 Рейтинг",
                callback_data="rating"
            )
        ],

        [
            InlineKeyboardButton(
                text="📜 Правила",
                callback_data="rules"
            ),
            InlineKeyboardButton(
                text="💬 Чат",
                callback_data="chat"
            )
        ],

        [
            InlineKeyboardButton(
                text="🐞 Сообщить об ошибке",
                callback_data="support"
            )
        ],
    ]

    if is_admin(user_id):
        buttons.append([
            InlineKeyboardButton(
                text="⚙️ Моё пространство",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def game_keyboard(game):

    buttons = []

    if game["step"] < len(STORY) - 1:

        buttons.append([
            InlineKeyboardButton(
                text="➡️ Продолжить",
                callback_data="game_next"
            )
        ])

    else:

        buttons.append([
            InlineKeyboardButton(
                text="🏁 Завершить охоту",
                callback_data="game_finish"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="🔎 Осмотреться",
            callback_data="game_clue"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🐞 Сообщить об ошибке",
            callback_data="support"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🏠 Меню",
            callback_data="menu"
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def admin_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Поддержка",
                    callback_data="admin_support"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧪 Бета-режим",
                    callback_data="admin_beta"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin_users"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="menu"
                )
            ]
        ]
    )


# ============================================================
# GAME HELPERS
# ============================================================


def render_game(game):

    story = STORY[
        game["step"]
    ]

    return (
        f"<b>{escape(story['title'])}</b>\n\n"
        f"{story['text']}"
        f"{game_header(game)}"
    )


def format_result_time(seconds):

    minutes = seconds // 60
    seconds = seconds % 60

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def save_result(
    user_id,
    username,
    game
):

    connection = get_db()

    try:
        connection.execute(
            """
            INSERT INTO results (
                user_id,
                username,
                time_seconds,
                clues,
                interactions,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username or "",
                game_elapsed(game),
                len(
                    game.get(
                        "clues",
                        set()
                    )
                ),
                game.get(
                    "interactions",
                    0
                ),
                datetime.now().isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()


async def finish_game(
    event,
    timeout=False
):

    user = event.from_user

    game = get_game(
        user.id
    )

    if not game:

        if isinstance(
            event,
            CallbackQuery
        ):
            await event.answer(
                "Активной охоты нет.",
                show_alert=True
            )

        return

    elapsed = game_elapsed(
        game
    )

    save_result(
        user.id,
        user.username,
        game
    )

    delete_game(
        user.id
    )

    if timeout:

        text = """
⏰ <b>ВРЕМЯ ВЫШЛО</b>

Охота завершена автоматически.

Результат сохранён.
"""

    else:

        text = (
            "🏁 <b>ОХОТА ЗАВЕРШЕНА</b>\n\n"
            f"⏱ Время: "
            f"<b>{format_result_time(elapsed)}</b>\n"
            f"🔎 Улик: "
            f"<b>{len(game.get('clues', set()))}</b>\n"
            f"🤝 Взаимодействий: "
            f"<b>{game.get('interactions', 0)}</b>"
        )

    if isinstance(
        event,
        CallbackQuery
    ):

        await event.message.edit_text(
            text,
            reply_markup=main_menu(
                user.id
            )
        )

        await event.answer()

    else:

        await event.answer(
            text,
            reply_markup=main_menu(
                user.id
            )
        )


async def check_game(
    callback
):

    user_id = callback.from_user.id

    game = get_game(
        user_id
    )

    if not game:

        await callback.answer(
            "Активной охоты нет.",
            show_alert=True
        )

        return None

    if game_expired(game):

        await finish_game(
            callback,
            timeout=True
        )

        return None

    return game


# ============================================================
# START
# ============================================================


@dp.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    if (
        beta_mode_enabled()
        and not is_beta_tester(
            message.from_user.id
        )
    ):

        await message.answer(
            """
🧪 <b>ЗАКРЫТОЕ ТЕСТИРОВАНИЕ</b>

Сейчас бот доступен только участникам тестирования.
"""
        )

        return

    await message.answer(
        """
🌑 <b>ОХОТА</b>

Добро пожаловать.

Выбирай действие:
""",
        reply_markup=main_menu(
            message.from_user.id
        )
    )


@dp.message(Command("menu"))
async def command_menu(
    message: Message
):

    await message.answer(
        "🏠 <b>Главное меню</b>",
        reply_markup=main_menu(
            message.from_user.id
        )
    )


# ============================================================
# MENU
# ============================================================


@dp.callback_query(F.data == "menu")
async def callback_menu(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        reply_markup=main_menu(
            callback.from_user.id
        )
    )

    await callback.answer()


# ============================================================
# GAME START
# ============================================================


@dp.callback_query(F.data == "game_start")
async def game_start(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    if (
        beta_mode_enabled()
        and not is_beta_tester(user_id)
    ):

        await callback.answer(
            "Сейчас доступно только тестерам.",
            show_alert=True
        )

        return

    games[user_id] = {
        "step": 0,
        "started": time.monotonic(),
        "clues": set(),
        "interactions": 0,
        "screen": "story"
    }

    game = games[user_id]

    await callback.message.edit_text(
        render_game(game),
        reply_markup=game_keyboard(game)
    )

    await callback.answer(
        "Охота началась!"
    )


# ============================================================
# GAME NEXT
# ============================================================


@dp.callback_query(F.data == "game_next")
async def game_next(
    callback: CallbackQuery
):

    game = await check_game(
        callback
    )

    if not game:
        return

    game["interactions"] += 1

    if game["step"] < len(STORY) - 1:

        game["step"] += 1

    await callback.message.edit_text(
        render_game(game),
        reply_markup=game_keyboard(game)
    )

    await callback.answer()


# ============================================================
# CLUES
# ============================================================


@dp.callback_query(F.data == "game_clue")
async def game_clue(
    callback: CallbackQuery
):

    game = await check_game(
        callback
    )

    if not game:
        return

    game["interactions"] += 1

    clue_id = game["step"] + 1

    game["clues"].add(
        clue_id
    )

    clues = [
        "👣 Ты замечаешь свежие следы.",
        "📄 Ты находишь фрагмент записки.",
        "🔩 Металлическая деталь явно от старого замка.",
        "🔎 На стене обнаруживается тот же знак.",
        "📱 В разбитом телефоне сохранился последний сигнал."
    ]

    clue = clues[
        game["step"] % len(clues)
    ]

    await callback.answer(
        clue,
        show_alert=True
    )


# ============================================================
# FINISH GAME
# ============================================================


@dp.callback_query(F.data == "game_finish")
async def game_finish(
    callback: CallbackQuery
):

    await finish_game(
        callback
    )


# ============================================================
# MISSIONS
# ============================================================


@dp.callback_query(F.data == "missions")
async def missions(
    callback: CallbackQuery
):

    if (
        beta_mode_enabled()
        and not is_beta_tester(
            callback.from_user.id
        )
    ):

        text = """
🎯 <b>МИССИИ</b>

🔒 Сейчас доступно только участникам тестирования.
"""

    else:

        text = """
🎯 <b>МИССИИ</b>

🕵️ <b>Охота</b>

Основная игровая миссия.

⏱ Максимальное время:
<b>60 минут</b>

🔎 Собирай улики.
🤝 Взаимодействуй.
🏆 Заверши охоту и попади в рейтинг.
"""

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🕵️ Начать охоту",
                    callback_data="game_start"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="menu"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# PROFILE
# ============================================================


@dp.callback_query(F.data == "profile")
async def profile(
    callback: CallbackQuery
):

    user_id = callback.from_user.id

    connection = get_db()

    try:

        count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM results
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()["count"]

        best = connection.execute(
            """
            SELECT MIN(time_seconds) AS best
            FROM results
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()["best"]

    finally:
        connection.close()

    if best is None:
        best_text = "—"
    else:
        best_text = format_result_time(
            best
        )

    name = escape(
        callback.from_user.first_name
        or "Охотник"
    )

    text = (
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"👤 {name}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        f"🏁 Завершено охот: "
        f"<b>{count}</b>\n"
        f"⚡ Лучшее время: "
        f"<b>{best_text}</b>"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="menu"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# RATING
# ============================================================


@dp.callback_query(F.data == "rating")
async def rating(
    callback: CallbackQuery
):

    connection = get_db()

    try:

        rows = connection.execute(
            """
            SELECT
                user_id,
                username,
                MIN(time_seconds) AS best
            FROM results
            GROUP BY user_id
            ORDER BY best ASC
            LIMIT 10
            """
        ).fetchall()

    finally:
        connection.close()

    if not rows:

        text = """
🏆 <b>РЕЙТИНГ</b>

Пока никто не завершил охоту.
"""

    else:

        lines = [
            "🏆 <b>ТОП-10 ОХОТНИКОВ</b>",
            ""
        ]

        for index, row in enumerate(
            rows,
            start=1
        ):

            if row["username"]:
                username = (
                    "@"
                    + row["username"]
                )
            else:
                username = (
                    str(row["user_id"])
                )

            lines.append(
                f"{index}. "
                f"{escape(username)} — "
                f"<b>{format_result_time(row['best'])}</b>"
            )

        text = "\n".join(
            lines
        )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="menu"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# RULES
# ============================================================


@dp.callback_query(F.data == "rules")
async def rules(
    callback: CallbackQuery
):

    text = """
📜 <b>ПРАВИЛА</b>

1️⃣ На прохождение охоты даётся 60 минут.

2️⃣ За время игры учитываются найденные улики.

3️⃣ Учитываются игровые взаимодействия.

4️⃣ После завершения результат сохраняется.

5️⃣ Лучшее время попадает в рейтинг.

6️⃣ Использование багов для получения преимущества запрещено.

🐞 Если обнаружил проблему — сообщи через раздел поддержки.
"""

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data="menu"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================


@dp.callback_query(F.data == "support")
async def support_start(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        SupportState.waiting_message
    )

    await callback.message.edit_text(
        """
🐞 <b>ПОДДЕРЖКА</b>

Напиши описание проблемы одним сообщением.

Можно сообщить:
• ошибку в игре;
• проблему с кнопкой;
• проблему с ботом;
• предложение.

Сообщение автоматически сохранится
в админском разделе поддержки.

Для отмены:
<b>/menu</b>
"""
    )

    await callback.answer()


@dp.message(
    SupportState.waiting_message
)
async def support_message(
    message: Message,
    state: FSMContext
):

    text = (
        message.text
        or ""
    ).strip()

    if not text:

        await message.answer(
            "❗ Напиши текст обращения."
        )

        return

    user = message.from_user

    save_user(
        user.id,
        user.username,
        user.first_name
    )

    connection = get_db()

    try:

        cursor = connection.execute(
            """
            INSERT INTO support (
                user_id,
                message,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                user.id,
                text,
                datetime.now().isoformat()
            )
        )

        support_id = cursor.lastrowid

        connection.commit()

    finally:
        connection.close()

    await state.clear()

    await message.answer(
        f"""
✅ <b>Обращение #{support_id} сохранено.</b>

Администратор получит сообщение
и сможет ответить тебе.
""",
        reply_markup=main_menu(
            user.id
        )
    )

    if ADMIN_ID:

        try:

            admin_text = (
                f"🐞 <b>НОВОЕ ОБРАЩЕНИЕ #{support_id}</b>\n\n"
                f"👤 {escape(user.full_name)}\n"
                f"🆔 <code>{user.id}</code>\n"
                f"🔗 @{escape(user.username or 'нет')}\n\n"
                f"{escape(text)}"
            )

            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✍️ Ответить",
                            callback_data=(
                                f"support_reply:{support_id}"
                            )
                        )
                    ]
                ]
            )

            await bot.send_message(
                ADMIN_ID,
                admin_text,
                reply_markup=markup
            )

        except Exception:

            logger.exception(
                "Не удалось отправить обращение администратору"
            )


# ============================================================
# CHAT
# ============================================================


@dp.callback_query(F.data == "chat")
async def chat_start(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        ChatState.waiting_message
    )

    await callback.message.edit_text(
        """
💬 <b>ЧАТ</b>

Напиши сообщение.

Оно будет сохранено
в игровом чате.

Для отмены:
<b>/menu</b>
"""
    )

    await callback.answer()


@dp.message(
    ChatState.waiting_message
)
async def chat_message(
    message: Message,
    state: FSMContext
):

    text = (
        message.text
        or ""
    ).strip()

    if not text:

        await message.answer(
            "❗ Напиши текст сообщения."
        )

        return

    connection = get_db()

    try:

        connection.execute(
            """
            INSERT INTO chat_messages (
                user_id,
                username,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                message.from_user.id,
                message.from_user.username or "",
                text,
                datetime.now().isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()

    await state.clear()

    await message.answer(
        "✅ Сообщение сохранено.",
        reply_markup=main_menu(
            message.from_user.id
        )
    )


# ============================================================
# ADMIN PANEL
# ============================================================


@dp.callback_query(F.data == "admin")
async def admin_panel(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    connection = get_db()

    try:

        users = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            """
        ).fetchone()["count"]

        support_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM support
            WHERE answered = 0
            """
        ).fetchone()["count"]

        testers = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM beta_testers
            """
        ).fetchone()["count"]

        results = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM results
            """
        ).fetchone()["count"]

    finally:
        connection.close()

    beta_text = (
        "🟢 включён"
        if beta_mode_enabled()
        else
        "🔴 выключен"
    )

    text = (
        "⚙️ <b>МОЁ ПРОСТРАНСТВО</b>\n\n"
        f"👥 Пользователей: "
        f"<b>{users}</b>\n"
        f"🏁 Результатов: "
        f"<b>{results}</b>\n"
        f"💬 Новых обращений: "
        f"<b>{support_count}</b>\n"
        f"🧪 Тестеров: "
        f"<b>{testers}</b>\n"
        f"🧪 Бета-режим: "
        f"<b>{beta_text}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN USERS
# ============================================================


@dp.callback_query(F.data == "admin_users")
async def admin_users(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    connection = get_db()

    try:

        rows = connection.execute(
            """
            SELECT
                user_id,
                username,
                first_name
            FROM users
            ORDER BY created_at DESC
            LIMIT 15
            """
        ).fetchall()

    finally:
        connection.close()

    lines = [
        "👥 <b>ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>",
        ""
    ]

    if not rows:

        lines.append(
            "Пользователей пока нет."
        )

    else:

        for row in rows:

            name = escape(
                row["first_name"]
                or "Без имени"
            )

            username = (
                "@"
                + row["username"]
                if row["username"]
                else
                "без username"
            )

            lines.append(
                f"• {name}\n"
                f"  {escape(username)}\n"
                f"  ID: <code>{row['user_id']}</code>"
            )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Назад",
                    callback_data="admin"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# ADMIN SUPPORT
# ============================================================


@dp.callback_query(F.data == "admin_support")
async def admin_support(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    connection = get_db()

    try:

        rows = connection.execute(
            """
            SELECT
                id,
                user_id,
                message,
                created_at
            FROM support
            WHERE answered = 0
            ORDER BY id DESC
            LIMIT 10
            """
        ).fetchall()

    finally:
        connection.close()

    if not rows:

        text = """
💬 <b>ПОДДЕРЖКА</b>

Новых обращений нет.
"""

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Назад",
                        callback_data="admin"
                    )
                ]
            ]
        )

    else:

        lines = [
            "💬 <b>НОВЫЕ ОБРАЩЕНИЯ</b>",
            ""
        ]

        buttons = []

        for row in rows:

            preview = (
                row["message"]
                .replace("\n", " ")
            )[:60]

            lines.append(
                f"#{row['id']} "
                f"· ID <code>{row['user_id']}</code>\n"
                f"{escape(preview)}"
            )

            buttons.append([
                InlineKeyboardButton(
                    text=f"✍️ Ответить #{row['id']}",
                    callback_data=(
                        f"support_reply:{row['id']}"
                    )
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="⚙️ Назад",
                callback_data="admin"
            )
        ])

        text = "\n\n".join(
            lines
        )

        markup = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

    await callback.message.edit_text(
        text,
        reply_markup=markup
    )

    await callback.answer()


# ============================================================
# ADMIN SUPPORT REPLY
# ============================================================


@dp.callback_query(
    F.data.startswith("support_reply:")
)
async def support_reply_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    try:

        support_id = int(
            callback.data.split(
                ":",
                1
            )[1]
        )

    except (
        ValueError,
        IndexError
    ):

        await callback.answer(
            "Некорректное обращение.",
            show_alert=True
        )

        return

    await state.update_data(
        support_id=support_id
    )

    await state.set_state(
        SupportReplyState.waiting_reply
    )

    await callback.message.edit_text(
        f"""
✍️ <b>ОТВЕТ НА ОБРАЩЕНИЕ #{support_id}</b>

Напиши ответ одним сообщением.

Для отмены:
<b>/menu</b>
"""
    )

    await callback.answer()


@dp.message(
    SupportReplyState.waiting_reply
)
async def support_reply_message(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    data = await state.get_data()

    support_id = data.get(
        "support_id"
    )

    answer = (
        message.text
        or ""
    ).strip()

    if not answer:

        await message.answer(
            "❗ Ответ не может быть пустым."
        )

        return

    connection = get_db()

    try:

        row = connection.execute(
            """
            SELECT user_id
            FROM support
            WHERE id = ?
            """,
            (support_id,)
        ).fetchone()

        if row:

            connection.execute(
                """
                UPDATE support
                SET
                    answered = 1,
                    answer = ?,
                    answered_at = ?
                WHERE id = ?
                """,
                (
                    answer,
                    datetime.now().isoformat(),
                    support_id
                )
            )

            connection.commit()

    finally:
        connection.close()

    await state.clear()

    if not row:

        await message.answer(
            "❌ Обращение не найдено.",
            reply_markup=main_menu(
                message.from_user.id
            )
        )

        return

    try:

        await bot.send_message(
            row["user_id"],
            (
                "💬 <b>ОТВЕТ ПОДДЕРЖКИ</b>\n\n"
                f"{escape(answer)}"
            )
        )

    except Exception:

        logger.exception(
            "Не удалось отправить ответ пользователю"
        )

    await message.answer(
        f"✅ Ответ на обращение "
        f"<b>#{support_id}</b> отправлен.",
        reply_markup=main_menu(
            message.from_user.id
        )
    )


# ============================================================
# BETA
# ============================================================


@dp.callback_query(F.data == "admin_beta")
async def admin_beta(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    enabled = beta_mode_enabled()

    state_text = (
        "🟢 ВКЛЮЧЁН"
        if enabled
        else
        "🔴 ВЫКЛЮЧЕН"
    )

    toggle_text = (
        "🔴 Выключить"
        if enabled
        else
        "🟢 Включить"
    )

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="beta_toggle"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Добавить тестера",
                    callback_data="beta_add"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Назад",
                    callback_data="admin"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"""
🧪 <b>БЕТА-РЕЖИМ</b>

Состояние:
<b>{state_text}</b>

При включённом режиме
играть смогут только администратор
и пользователи из списка тестеров.
""",
        reply_markup=markup
    )

    await callback.answer()


@dp.callback_query(F.data == "beta_toggle")
async def beta_toggle(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    new_value = (
        "0"
        if beta_mode_enabled()
        else
        "1"
    )

    set_setting(
        "beta_mode",
        new_value
    )

    await callback.answer(
        "Настройка изменена."
    )

    await admin_beta(
        callback
    )


@dp.callback_query(F.data == "beta_add")
async def beta_add(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )

        return

    await state.set_state(
        BetaState.waiting_user_id
    )

    await callback.message.edit_text(
        """
➕ <b>ДОБАВИТЬ ТЕСТЕРА</b>

Отправь Telegram ID пользователя
обычным сообщением.

Например:

<code>123456789</code>
"""
    )

    await callback.answer()


@dp.message(
    BetaState.waiting_user_id
)
async def beta_add_message(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:

        user_id = int(
            (
                message.text
                or ""
            ).strip()
        )

    except ValueError:

        await message.answer(
            "❗ Нужен числовой Telegram ID."
        )

        return

    connection = get_db()

    try:

        connection.execute(
            """
            INSERT OR IGNORE INTO beta_testers (
                user_id,
                added_at
            )
            VALUES (?, ?)
            """,
            (
                user_id,
                datetime.now().isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()

    await state.clear()

    await message.answer(
        f"""
✅ Пользователь
<code>{user_id}</code>

добавлен в список тестеров.
""",
        reply_markup=main_menu(
            message.from_user.id
        )
    )


# ============================================================
# UNKNOWN CALLBACK
# ============================================================


@dp.callback_query()
async def unknown_callback(
    callback: CallbackQuery
):

    await callback.answer(
        "Кнопка устарела. Открой меню заново.",
        show_alert=True
    )


# ============================================================
# MAIN
# ============================================================


async def main():

    init_db()

    logger.info(
        "🤖 Бот запускается..."
    )

    try:

        await dp.start_polling(
            bot
        )

    except Exception:

        logger.exception(
            "Критическая ошибка бота"
        )

    finally:

        await bot.session.close()

        logger.info(
            "🛑 Бот остановлен"
        )


if __name__ == "__main__":
    asyncio.run(main())