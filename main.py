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

Он останавливается напротив тебя.

— Ты тоже его ищешь?
"""
    },
    {
        "title": "ЭПИЗОД 5",
        "text": """
После разговора становится ясно:

исчезнувший человек собирал цепочку улик.

Последняя часть расследования находится здесь.
"""
    },
    {
        "title": "ЭПИЗОД 6",
        "text": """
На столе лежат старые фотографии.

На каждой изображено одно и то же место.

Но сделаны они в разные годы.

На последней фотографии появляется знакомый человек.
"""
    },
    {
        "title": "ЭПИЗОД 7",
        "text": """
На столе лежит записка:

<i>«Не верь первому человеку, который предложит помощь.»</i>
"""
    },
    {
        "title": "ЭПИЗОД 8",
        "text": """
Внутри шкафа находится вторая часть записки.

На ней координаты.

Но одной цифры не хватает.
"""
    },
    {
        "title": "ЭПИЗОД 9",
        "text": """
Ты снова встречаешь того человека.

Он говорит, что ничего не знает.

Но на его руке тот же знак,
который был на стене.
"""
    },
    {
        "title": "ЭПИЗОД 10",
        "text": """
Теперь нужно решить:

довериться ему

или проверить всё самостоятельно.
"""
    },
    {
        "title": "ЭПИЗОД 11",
        "text": """
Проверка показывает противоречие.

Он соврал.

Но это ещё не доказывает его причастность.
"""
    },
    {
        "title": "ЭПИЗОД 12",
        "text": """
В архиве находится старое дело.

Дата последнего события совпадает
с датой исчезновения.
"""
    },
    {
        "title": "ЭПИЗОД 13",
        "text": """
Последняя страница дела вырвана.

Кто-то не хотел,
чтобы её нашли.
"""
    },
    {
        "title": "ЭПИЗОД 14",
        "text": """
След приводит тебя к следующему месту.

Теперь ты понимаешь:

за тобой тоже наблюдают.
"""
    },
    {
        "title": "ЭПИЗОД 15",
        "text": """
В тайнике находятся:

📷 фотографии
🔑 ключ
📄 последняя часть сообщения
"""
    },
    {
        "title": "ЭПИЗОД 16",
        "text": """
Сообщение содержит главное:

исчезновение было запланировано.

Но неизвестно кем.
"""
    },
    {
        "title": "ЭПИЗОД 17",
        "text": """
Все найденные улики складываются
в одну последовательность.

Ты понимаешь, куда идти.
"""
    },
    {
        "title": "ЭПИЗОД 18",
        "text": """
Последняя дверь заперта.

Ключ из тайника подходит.
"""
    },
    {
        "title": "ЭПИЗОД 19",
        "text": """
Внутри находится человек,
которого ты искал.

Он смотрит на тебя и говорит:

— Ты всё понял неправильно.
"""
    },
    {
        "title": "ФИНАЛ",
        "text": """
Ты смотришь на все найденные улики.

Теперь решение за тобой.

Кто на самом деле организовал исчезновение?

Это последний шаг охоты.
"""
    }
]


def validate_story():
    if not STORY:
        raise RuntimeError("STORY не содержит эпизодов.")
    for i, ch in enumerate(STORY):
        if not isinstance(ch, dict):
            raise RuntimeError(f"STORY[{i}] должен быть dict.")
        if not ch.get("title"):
            raise RuntimeError(f"STORY[{i}] не имеет title.")
        if not ch.get("text"):
            raise RuntimeError(f"STORY[{i}] не имеет text.")


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
    buttons = [
        [InlineKeyboardButton(text="🔥 НАЧАТЬ ОХОТУ", callback_data="game_start")],
        [
            InlineKeyboardButton(text="📖 Как играть", callback_data="how_to_play"),
            InlineKeyboardButton(text="🏆 Результаты", callback_data="results"),
        ],
        [
            InlineKeyboardButton(text="💬 Чат", callback_data="chat"),
            InlineKeyboardButton(text="🆘 Поддержка", callback_data="support"),
        ],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="🏠 МОЁ ПРОСТРАНСТВО", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])


def research_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Искать улики", callback_data="clues")],
        [InlineKeyboardButton(text="👤 Осмотреть место", callback_data="location")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="game_back")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main")],
    ])


def clue_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👣 Следы", callback_data="clue_tracks"),
         InlineKeyboardButton(text="📄 Документ", callback_data="clue_document")],
        [InlineKeyboardButton(text="🔍 Странная деталь", callback_data="clue_detail")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="game_back")],
    ])


def interaction_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤝 Взаимодействовать", callback_data="interact")],
        [InlineKeyboardButton(text="🚶 Пройти самому", callback_data="alone")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="game_back")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Управление охотой", callback_data="admin_game")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🧪 Бета-тест", callback_data="admin_beta")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="admin_support")],
        [InlineKeyboardButton(text="📊 Результаты", callback_data="admin_results")],
        [InlineKeyboardButton(text="🖼 Контент", callback_data="admin_content")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
    ])


# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit(callback: CallbackQuery, text, reply_markup=None):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception as error:
        # message is not modified -> ignore
        msg = str(error).lower()
        if "message is not modified" in msg:
            return True
        logger.exception("Ошибка изменения сообщения: %s", error)
        try:
            await callback.answer("Не удалось обновить экран.", show_alert=True)
        except Exception:
            pass
        return False
    return True


# ============================================================
# MAIN MENU / START
# ============================================================

async def send_main_menu(message: Message):
    user_id = message.from_user.id
    beta_text = ""
    if beta_mode_enabled() and not is_beta_tester(user_id):
        beta_text = """
🧪 <b>Сейчас открыт закрытый бета-тест.</b>

Доступ к охоте имеют приглашённые тестеры.
"""
    await message.answer(
        f"""
🔎 <b>OHOTA</b>

Одна история.
Одна охота.
60 минут.

⭐ Сложность: <b>★★★★☆☆</b>

Ищи улики.
Разговаривай с людьми.
Принимай решения.

🏆 Побеждает тот,
кто быстрее пройдёт расследование.

{beta_text}

<b>Готов начать?</b>
""",
        reply_markup=main_keyboard(user_id)
    )


@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext):
    await state.clear()
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await send_main_menu(message)


@dp.message(F.text.casefold() == "старт")
async def text_start(message: Message, state: FSMContext):
    await state.clear()
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await send_main_menu(message)


@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(callback, "🔎 <b>OHOTA</b>\n\nГлавное меню.", reply_markup=main_keyboard(callback.from_user.id))
    await callback.answer()


@dp.callback_query(F.data == "how_to_play")
async def how_to_play(callback: CallbackQuery):
    await safe_edit(
        callback,
        """
📖 <b>КАК ИГРАТЬ</b>

Перед тобой одна большая история.

🔎 Ищи улики.
👤 Взаимодействуй с персонажами.
🧩 Сопоставляй информацию.
⚠️ Принимай решения.

⭐ Сложность: <b>★★★★☆☆</b>

⏱ Лимит одного забега:
<b>60 минут</b>

🏆 Твоя цель —
пройти расследование быстрее других.
""",
        reply_markup=back_keyboard()
    )
    await callback.answer()


# ============================================================
# GAME FLOW
# ============================================================

@dp.callback_query(F.data == "game_start")
async def game_start(callback: CallbackQuery):
    user_id = callback.from_user.id

    if beta_mode_enabled() and not is_beta_tester(user_id):
        await callback.answer("Сейчас доступ только для бета-тестеров.", show_alert=True)
        return

    existing_game = get_game(user_id)
    if existing_game:
        if game_expired(existing_game):
            await finish_game(callback, timeout=True)
            return
        elapsed = game_elapsed(existing_game)
        remaining = max(0, GAME_TIME - elapsed)
        minutes = remaining // 60
        seconds = remaining % 60
        await callback.answer(f"У тебя уже идёт охота. Осталось {minutes:02d}:{seconds:02d}.", show_alert=True)
        return

    games[user_id] = {
        "step": 0,
        "started": time.monotonic(),
        "clues": set(),
        "interactions": 0,
        "screen": "story"
    }
    save_active_game(user_id)

    # Start animation
    await safe_edit(callback, "🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>\n\n▰▱▱▱▱")
    await asyncio.sleep(0.25)
    if user_id not in games:
        await callback.answer()
        return
    await safe_edit(callback, "🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>\n\n▰▰▱▱▱")
    await asyncio.sleep(0.25)
    if user_id not in games:
        await callback.answer()
        return
    await safe_edit(callback, "🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>\n\n▰▰▰▱▱")
    await asyncio.sleep(0.25)
    if user_id not in games:
        await callback.answer()
        return
    await safe_edit(callback, "🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>\n\n▰▰▰▰▱")
    await asyncio.sleep(0.25)
    if user_id not in games:
        await callback.answer()
        return
    await safe_edit(callback, "🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>\n\n▰▰▰▰▰\n\n<b>ОХОТА НАЧИНАЕТСЯ.</b>")
    await asyncio.sleep(0.4)
    if user_id not in games:
        await callback.answer()
        return

    await show_story(callback)
    await callback.answer()


async def show_story(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = get_game(user_id)
    if not game:
        await callback.answer("Начни новую охоту.", show_alert=True)
        return
    if game_expired(game):
        await finish_game(callback, timeout=True)
        return

    step = game.get("step", 0)
    if step < 0:
        step = 0
        game["step"] = 0
    if step >= len(STORY):
        await finish_game(callback)
        return

    chapter = STORY[step]

    if step == 0:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔥 НАЧАТЬ РАССЛЕДОВАНИЕ", callback_data="next_story")],
            [InlineKeyboardButton(text="🏠 Выйти", callback_data="game_exit")]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="next_story")],
            [InlineKeyboardButton(text="🏠 Выйти", callback_data="game_exit")]
        ])

    game["screen"] = "story"
    await safe_edit(
        callback,
        f"""
<b>{escape(chapter["title"])}</b>

{chapter["text"]}

⭐ Сложность: <b>★★★★☆☆</b>

{game_header(game)}
""",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "next_story")
async def next_story(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = get_game(user_id)
    if not game:
        await callback.answer("Сначала начни новую охоту.", show_alert=True)
        return
    if game_expired(game):
        await finish_game(callback, timeout=True)
        return

    current = game.get("step", 0)
    if current >= len(STORY) - 1:
        await finish_game(callback)
        return

    game["step"] = current + 1
    save_active_game(user_id)
    step = game["step"]

    # Special screens
    if step in {1, 2, 6, 9, 12, 15}:
        game["screen"] = "research"
        await safe_edit(
            callback,
            f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

{STORY[step]["text"]}

{game_header(game)}

Что будешь делать?
""",
            reply_markup=research_keyboard()
        )
    elif step in {4, 19}:
        game["screen"] = "interaction"
        await safe_edit(
            callback,
            f"""
{STORY[step]["text"]}

{game_header(game)}

Что будешь делать?
""",
            reply_markup=interaction_keyboard()
        )
    else:
        await show_story(callback)

    await callback.answer()


@dp.callback_query(F.data == "game_back")
async def game_back(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    game["screen"] = "research"
    await safe_edit(
        callback,
        f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

Ты осматриваешь место ещё раз.

{game_header(game)}

Что проверить?
""",
        reply_markup=research_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "game_exit")
async def game_exit(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    game = get_game(user_id)
    # if no active game — just go to main menu and clear FSM
    if not game:
        await state.clear()
        await safe_edit(callback, "🔎 <b>OHOTA</b>\n\nАктивной охоты нет.", reply_markup=main_keyboard(user_id))
        await callback.answer()
        return

    if game_expired(game):
        await finish_game(callback, timeout=True)
        return

    await safe_edit(
        callback,
        f"""
⚠️ <b>ВЫЙТИ ИЗ ОХОТЫ?</b>

Текущий прогресс будет потерян.

🔎 Улик: <b>{len(game.get("clues", set()))}</b>
🤝 Взаимодействий: <b>{game.get("interactions", 0)}</b>

Ты действительно хочешь выйти?
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Да, выйти", callback_data="game_exit_confirm")],
            [InlineKeyboardButton(text="↩️ Продолжить охоту", callback_data="game_exit_cancel")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "game_exit_confirm")
async def game_exit_confirm(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    delete_game(user_id)
    await state.clear()
    await safe_edit(
        callback,
        """
🏠 <b>ОХОТА ЗАВЕРШЕНА</b>

Ты вышел из текущего расследования.

Прогресс этого забега не записан
в таблицу лидеров.
""",
        reply_markup=main_keyboard(user_id)
    )
    await callback.answer("Охота завершена.")


@dp.callback_query(F.data == "game_exit_cancel")
async def game_exit_cancel(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    screen = game.get("screen", "story")
    if screen == "research":
        await safe_edit(
            callback,
            f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

{STORY[game['step']]["text"]}

{game_header(game)}

Что будешь делать?
""",
            reply_markup=research_keyboard()
        )
    elif screen == "clues":
        await safe_edit(
            callback,
            f"""
🔎 <b>УЛИКИ</b>

Изучи найденные детали внимательнее.

{game_header(game)}
""",
            reply_markup=clue_keyboard()
        )
    elif screen == "interaction":
        await safe_edit(
            callback,
            f"""
{STORY[game['step']]["text"]}

{game_header(game)}

Что будешь делать?
""",
            reply_markup=interaction_keyboard()
        )
    else:
        await show_story(callback)
    await callback.answer()


# Clues / research / interaction handlers (use check_game to centralize timer check)

@dp.callback_query(F.data == "clues")
async def handler_clues(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    game["screen"] = "clues"
    await safe_edit(
        callback,
        f"""
🔎 <b>УЛИКИ</b>

Несколько деталей могут оказаться
важными для расследования.

Изучи их внимательно.

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "clue_tracks")
async def handler_clue_tracks(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    already = "tracks" in game.get("clues", set())
    game["clues"].add("tracks")
    game["screen"] = "clue_tracks"
    status_msg = "🔎 <b>Улика добавлена.</b>" if not already else "🔎 <b>Эта улика уже была найдена.</b>"
    await safe_edit(
        callback,
        f"""
👣 <b>СЛЕДЫ</b>

Следы идут от двери к стене.

Но есть странность:

человек остановился примерно
в двух метрах от стены.

Следов обратно нет.

Значит, он либо ушёл другим путём,
либо его кто-то забрал.

{status_msg}

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )
    save_active_game(callback.from_user.id)
    await callback.answer("Улика уже изучена." if already else "Улика найдена")


@dp.callback_query(F.data == "clue_document")
async def handler_clue_document(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    already = "document" in game.get("clues", set())
    game["clues"].add("document")
    game["screen"] = "clue_document"
    status = "🔎 <b>Эта улика уже была изучена.</b>" if already else "🔎 <b>Улика добавлена.</b>"
    await safe_edit(
        callback,
        f"""
📄 <b>ДОКУМЕНТ</b>

На бумаге видна часть адреса:

<b>17 / 04 / 23</b>

Ниже написано:

<i>«Не там, где ищут все.»</i>

Возможно, это не дата.

{status}

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )
    save_active_game(callback.from_user.id)
    await callback.answer("Улика уже изучена." if already else "Улика найдена")


@dp.callback_query(F.data == "clue_detail")
async def handler_clue_detail(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    already = "detail" in game.get("clues", set())
    game["clues"].add("detail")
    game["screen"] = "clue_detail"
    status = "🔎 <b>Эта улика уже была изучена.</b>" if already else "🔎 <b>Улика добавлена.</b>"
    await safe_edit(
        callback,
        f"""
🔍 <b>СТРАННАЯ ДЕТАЛЬ</b>

На металлической детали есть символ.

Он совпадает со знаком
на стене.

Это уже не случайность.

{status}

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )
    save_active_game(callback.from_user.id)
    await callback.answer("Улика уже изучена." if already else "Улика найдена")


@dp.callback_query(F.data == "location")
async def handler_location(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    already = "camera" in game.get("clues", set())
    game["clues"].add("camera")
    game["screen"] = "location"
    status = "🔎 <b>Ты уже осматривал камеру.</b>" if already else "🔎 <b>Новая улика добавлена.</b>"
    await safe_edit(
        callback,
        f"""
👤 <b>ОСМОТР МЕСТА</b>

Ты замечаешь старую камеру наблюдения.

Она отключена.

Но индикатор питания мигает.

Кто-то недавно включал систему.

{status}

{game_header(game)}
""",
        reply_markup=research_keyboard()
    )
    save_active_game(callback.from_user.id)
    await callback.answer("Камера уже изучена." if already else "Обнаружена новая улика")


@dp.callback_query(F.data == "interact")
async def handler_interact(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    game["interactions"] = game.get("interactions", 0) + 1
    game["screen"] = "interaction_result"
    save_active_game(callback.from_user.id)
    await safe_edit(
        callback,
        f"""
🤝 <b>ВЗАИМОДЕЙСТВИЕ</b>

Ты решаешь поговорить с человеком.

Он сначала молчит.

Затем произносит:

— Ты ищешь не того человека.

Ты показываешь найденную улику.

Его лицо меняется.

— Где ты это нашёл?

Теперь ясно:

он знает больше,
чем говорит.

{game_header(game)}
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="next_story")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="game_back")],
            [InlineKeyboardButton(text="🏠 Выйти", callback_data="game_exit")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "alone")
async def handler_alone(callback: CallbackQuery):
    game = await check_game(callback)
    if not game:
        return
    game["interactions"] = game.get("interactions", 0) + 1
    game["clues"].add("alone_discovery")
    game["screen"] = "alone_result"
    save_active_game(callback.from_user.id)
    await safe_edit(
        callback,
        f"""
🚶 <b>ПРОЙТИ САМОМУ</b>

Ты не доверяешь незнакомцу.

Продолжаешь исследование самостоятельно.

Через несколько минут находишь
новую деталь, которую он явно
не хотел, чтобы ты увидел.

🔎 <b>Новая улика добавлена.</b>

{game_header(game)}
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="next_story")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="game_back")],
            [InlineKeyboardButton(text="🏠 Выйти", callback_data="game_exit")],
        ])
    )
    await callback.answer()


async def finish_game(callback: CallbackQuery, timeout: bool = False):
    user_id = callback.from_user.id
    game = get_game(user_id)
    if not game:
        await safe_edit(callback, "Охота уже завершена.", reply_markup=main_keyboard(user_id))
        return

    elapsed = game_elapsed(game)

    if timeout:
        games.pop(user_id, None)
        await safe_edit(
            callback,
            """
⏰ <b>ВРЕМЯ ВЫШЛО</b>

60 минут закончились.

Ты не успел завершить расследование.

Попробуй ещё раз.
""",
            reply_markup=main_keyboard(user_id)
        )
        await callback.answer()
        return

    clues_count = len(game.get("clues", set()))
    interactions_count = game.get("interactions", 0)
    username = callback.from_user.username or ""

    connection = get_db()
    try:
        connection.execute(
            """
            INSERT INTO results (user_id, username, time_seconds, clues, interactions, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, elapsed, clues_count, interactions_count, datetime.now().isoformat())
        )
        connection.commit()
    finally:
        connection.close()

    games.pop(user_id, None)

    minutes = elapsed // 60
    seconds = elapsed % 60

    await safe_edit(
        callback,
        f"""
🏆 <b>ОХОТА ЗАВЕРШЕНА</b>

Ты завершил расследование.

⏱ Время:
<b>{minutes:02d}:{seconds:02d}</b>

🔎 Улик:
<b>{clues_count}</b>

🤝 Взаимодействий:
<b>{interactions_count}</b>

Результат записан в таблицу лидеров.
""",
        reply_markup=main_keyboard(user_id)
    )
    await callback.answer()


# ============================================================
# RESULTS, CHAT, SUPPORT, ADMIN
# ============================================================

@dp.callback_query(F.data == "results")
async def results(callback: CallbackQuery):
    connection = get_db()
    try:
        rows = connection.execute(
            "SELECT username, time_seconds FROM results ORDER BY time_seconds ASC, id ASC LIMIT 10"
        ).fetchall()
    finally:
        connection.close()

    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
    if not rows:
        text += "Пока никто не завершил охоту."
    else:
        for idx, row in enumerate(rows, 1):
            username = row[0] or "Игрок"
            seconds = max(0, int(row[1]))
            display = f"@{escape(username)}" if username != "Игрок" else "Игрок"
            text += f"<b>{idx}.</b> {display} — <b>{seconds // 60:02d}:{seconds % 60:02d}</b>\n"

    await safe_edit(callback, text, reply_markup=back_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "chat")
async def chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ChatState.waiting_message)
    connection = get_db()
    try:
        rows = connection.execute(
            "SELECT username, message FROM chat_messages ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        connection.close()

    text = """
💬 <b>ЧАТ ИГРОКОВ</b>

Здесь общаются игроки OHOTA.

Напиши сообщение — оно появится
у остальных игроков.

"""
    if rows:
        text += "<b>Последние сообщения:</b>\n\n"
        for username, message_text in reversed(rows):
            name = f"@{escape(username)}" if username else "Игрок"
            text += f"<b>{name}</b>: {escape(message_text[:300])}\n\n"
    else:
        text += "Пока сообщений нет.\n"

    text += "\n<i>Напиши сообщение ниже.</i>"

    await safe_edit(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Выйти из чата", callback_data="back_main")]
    ]))
    await callback.answer()


@dp.message(ChatState.waiting_message)
async def receive_chat_message(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("В чат можно отправить только текст.")
        return
    if message.text.casefold().strip() == "старт":
        await state.clear()
        await send_main_menu(message)
        return

    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    save_user(user_id, username, first_name)

    chat_message = message.text[:2000]
    connection = get_db()
    try:
        connection.execute(
            "INSERT INTO chat_messages (user_id, username, message, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, chat_message, datetime.now().isoformat())
        )
        users = connection.execute("SELECT user_id FROM users").fetchall()
        connection.commit()
    finally:
        connection.close()

    safe_username = escape(username)
    safe_message = escape(chat_message)
    display_name = f"@{safe_username}" if username else "Игрок"
    chat_text = f"💬 <b>ЧАТ</b>\n\n<b>{display_name}</b>:\n{safe_message}"

    for row in users:
        target_id = row[0]
        try:
            await bot.send_message(target_id, chat_text)
        except Exception as error:
            logger.warning("Chat send failed for %s: %s", target_id, error)

    await message.answer(
        """
✅ <b>Сообщение отправлено.</b>

Его получили активные пользователи чата.
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Продолжить чат", callback_data="chat")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_main")],
        ])
    )


@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportState.waiting_message)
    await safe_edit(
        callback,
        """
🆘 <b>ПОДДЕРЖКА</b>

Напиши сообщение о проблеме.

Например:

<i>«Улика не открывается»</i>

Обращение сохранится
в твоём пространстве администратора.

Администратор сможет ответить
тебе прямо через бота.
""",
        reply_markup=back_keyboard()
    )
    await callback.answer()


@dp.message(SupportState.waiting_message)
async def receive_support(message: Message, state: FSMContext):
    if message.text and message.text.casefold().strip() == "старт":
        await state.clear()
        await send_main_menu(message)
        return
    if not message.text:
        await message.answer("Отправь текстовое сообщение.")
        return

    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    save_user(user_id, username, first_name)

    support_message = message.text[:5000]
    connection = get_db()
    try:
        cursor = connection.execute(
            "INSERT INTO support (user_id, message, created_at, answered) VALUES (?, ?, ?, 0)",
            (user_id, support_message, datetime.now().isoformat())
        )
        support_id = cursor.lastrowid
        connection.commit()
    finally:
        connection.close()

    await state.clear()
    await message.answer(
        """
✅ <b>Сообщение отправлено.</b>

Обращение сохранено.

Администратор сможет ответить
тебе прямо через бота.
""",
        reply_markup=main_keyboard(user_id)
    )

    if ADMIN_ID:
        try:
            admin_username = escape(username or "без username")
            safe_support = escape(support_message)
            await bot.send_message(
                ADMIN_ID,
                f"""
🆘 <b>НОВОЕ ОБРАЩЕНИЕ #{support_id}</b>

👤 ID:
<code>{user_id}</code>

👤 Пользователь:
@{admin_username}

💬 Сообщение:

{safe_support}
""",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"support_reply:{support_id}")]
                ])
            )
        except Exception as error:
            logger.error("Support notification error: %s", error)


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await safe_edit(
        callback,
        """
🏠 <b>МОЁ ПРОСТРАНСТВО</b>

🔐 Закрытая панель владельца.

Здесь находится управление ботом,
игрой, тестерами, контентом,
поддержкой и результатами.
""",
        reply_markup=admin_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_game")
async def admin_game(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    active_games = len(games)
    await safe_edit(
        callback,
        f"""
🎮 <b>УПРАВЛЕНИЕ ОХОТОЙ</b>

📖 Сюжет: активен
🧩 Эпизодов: <b>{len(STORY)}</b>
⭐ Сложность: 4/6
⏱ Лимит: <b>60 минут</b>
🏆 Соревнование: включено

👥 Сейчас проходят охоту:
<b>{active_games}</b>
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    connection = get_db()
    try:
        total = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        beta = connection.execute("SELECT COUNT(*) FROM beta_testers").fetchone()[0]
        results_count = connection.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        support_count = connection.execute("SELECT COUNT(*) FROM support").fetchone()[0]
        unanswered = connection.execute("SELECT COUNT(*) FROM support WHERE answered = 0").fetchone()[0]
    finally:
        connection.close()

    await safe_edit(
        callback,
        f"""
👥 <b>ПОЛЬЗОВАТЕЛИ</b>

Всего пользователей:
<b>{total}</b>

🎮 Сейчас проходят охоту:
<b>{len(games)}</b>

🧪 Бета-тестеров:
<b>{beta}</b>

🏆 Завершений:
<b>{results_count}</b>

🆘 Всего обращений:
<b>{support_count}</b>

⏳ Без ответа:
<b>{unanswered}</b>
""",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_beta")
async def admin_beta(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    connection = get_db()
    try:
        testers = connection.execute("SELECT user_id, added_at FROM beta_testers ORDER BY added_at DESC").fetchall()
    finally:
        connection.close()

    text = "🧪 <b>БЕТА-ТЕСТ</b>\n\n"
    if testers:
        text += "<b>Добавленные тестеры:</b>\n\n"
        for row in testers[:30]:
            text += f"• <code>{row[0]}</code>\n"
        if len(testers) > 30:
            text += f"\n<i>И ещё {len(testers)-30}...</i>\n"
        text += "\n"
    else:
        text += "Тестеров пока нет.\n\n"
    text += "Отправь Telegram ID, чтобы добавить тестера."

    await state.set_state(BetaState.waiting_user_id)
    await safe_edit(callback, text, reply_markup=back_keyboard())
    await callback.answer()


@dp.message(BetaState.waiting_user_id)
async def receive_beta_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("Отправь Telegram ID.")
        return
    try:
        beta_user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Telegram ID должен состоять только из цифр.")
        return
    if beta_user_id <= 0:
        await message.answer("Некорректный Telegram ID.")
        return

    connection = get_db()
    try:
        connection.execute(
            "INSERT OR IGNORE INTO beta_testers (user_id, added_at) VALUES (?, ?)",
            (beta_user_id, datetime.now().isoformat())
        )
        connection.commit()
    finally:
        connection.close()

    await state.clear()
    await message.answer(
        f"""
✅ <b>Тестер добавлен.</b>

ID:
<code>{beta_user_id}</code>

Теперь он сможет начать охоту,
если бета-режим включён.
""",
        reply_markup=admin_keyboard()
    )


@dp.callback_query(F.data == "admin_support")
async def admin_support(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    connection = get_db()
    try:
        rows = connection.execute(
            "SELECT id, user_id, message, created_at, answered FROM support ORDER BY id DESC LIMIT 20"
        ).fetchall()
    finally:
        connection.close()

    text = "🆘 <b>ОБРАЩЕНИЯ</b>\n\n"
    buttons = []
    if not rows:
        text += "Обращений пока нет."
    else:
        for row in rows:
            support_id, target_user_id, support_message, created_at, answered = row
            status = "✅ Отвечено" if answered else "🕐 Ожидает ответа"
            text += f"<b>#{support_id}</b> — {status}\n👤 ID: <code>{target_user_id}</code>\n💬 {escape(support_message[:500])}\n\n"
            if not answered:
                buttons.append([InlineKeyboardButton(text=f
