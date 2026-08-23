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
        "BOT_TOKEN не задан. "
        "Добавь переменную окружения BOT_TOKEN."
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
    """
    Возвращает новое подключение к SQLite.

    Каждый вызов получает отдельное соединение,
    поэтому соединения не хранятся глобально.
    """
    connection = sqlite3.connect(
        DB_NAME,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():
    """
    Создаёт все необходимые таблицы.

    CREATE TABLE IF NOT EXISTS позволяет
    безопасно запускать функцию при каждом старте.
    """

    connection = get_db()

    try:
        cursor = connection.cursor()

        # ----------------------------------------------------
        # USERS
        # ----------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)

        # ----------------------------------------------------
        # SUPPORT
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # BETA TESTERS
        # ----------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS beta_testers (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )
        """)

        # ----------------------------------------------------
        # RESULTS
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # CHAT
        # ----------------------------------------------------

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # ----------------------------------------------------
        # SETTINGS
        # ----------------------------------------------------
        #
        # Нужны для нормального управления режимом бета-теста.
        # Раньше beta_mode_enabled() фактически определял
        # режим по наличию хотя бы одного тестера.
        #
        # Это неправильно:
        # добавил одного тестера -> весь бот автоматически
        # становился закрытым.
        #
        # Теперь режим хранится отдельно.
        # ----------------------------------------------------

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
    """
    Получает настройку из БД.
    """

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
    """
    Записывает настройку в БД.
    """

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
            (
                key,
                str(value)
            )
        )

        connection.commit()

    finally:
        connection.close()


def save_user(
    user_id,
    username,
    first_name
):
    """
    Создаёт пользователя или обновляет его данные.
    """

    connection = get_db()

    try:
        connection.execute("""
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
    """
    Проверка администратора.
    """

    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


def is_beta_tester(user_id):
    """
    Проверяет, находится ли пользователь
    в списке бета-тестеров.

    Администратор всегда имеет доступ.
    """

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
    """
    Возвращает состояние закрытого бета-теста.

    1 = включён
    0 = выключен
    """

    return get_setting(
        "beta_mode",
        "0"
    ) == "1"


# ============================================================
# ACTIVE GAMES
# ============================================================

# В оперативной памяти находятся активные игровые сессии.
#
# Структура:
#
# games[user_id] = {
#     "step": 0,
#     "started": monotonic_timestamp,
#     "clues": set(),
#     "interactions": 0,
#     "screen": "story"
# }
#
# Важный момент:
# monotonic() используется именно для таймера.
# Его нельзя заменять datetime.now(), потому что
# системное время может измениться.

games = {}


def get_game(user_id):
    """
    Получить активную игру пользователя.
    """

    return games.get(user_id)


def delete_game(user_id):
    """
    Безопасно удалить активную игру.
    """

    games.pop(user_id, None)


def game_elapsed(game):
    """
    Сколько секунд прошло с начала игры.
    """

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
    """
    Проверка истечения 60 минут.
    """

    return (
        game is not None
        and game_elapsed(game) >= GAME_TIME
    )


def game_header(game):
    """
    Общий информационный блок игры.
    """

    elapsed = game_elapsed(game)

    remaining = max(
        0,
        GAME_TIME - elapsed
    )

    minutes = remaining // 60
    seconds = remaining % 60

    return f"""
⏱ Осталось:
<b>{minutes:02d}:{seconds:02d}</b>

🔎 Улик: <b>{len(game["clues"])}</b>
🤝 Взаимодействий: <b>{game["interactions"]}</b>
"""


async def check_game(callback: CallbackQuery):
    """
    Проверяет наличие активной игры и таймер.

    Возвращает игру, если всё нормально.

    Если игры нет или время закончилось —
    самостоятельно показывает соответствующий экран.
    """

    user_id = callback.from_user.id
    game = get_game(user_id)

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


def save_active_game(user_id):
    """
    Совместимость с существующей логикой проекта.

    Сейчас активная игра хранится в games.
    Функция оставлена, чтобы существующие места
    кода, где она вызывается, не ломались.

    При необходимости сюда можно подключить
    полноценное постоянное сохранение игровой сессии.
    """

    game = games.get(user_id)

    if not game:
        return False

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
# STORY
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


# ============================================================
# STORY VALIDATION
# ============================================================

def validate_story():
    """
    Проверяет структуру сюжета при запуске.

    Это позволяет поймать случайное удаление
    title/text ещё до начала игры.
    """

    if not STORY:
        raise RuntimeError(
            "STORY не содержит эпизодов."
        )

    for index, chapter in enumerate(STORY):
        if not isinstance(chapter, dict):
            raise RuntimeError(
                f"STORY[{index}] должен быть dict."
            )

        if not chapter.get("title"):
            raise RuntimeError(
                f"STORY[{index}] не имеет title."
            )

        if not chapter.get("text"):
            raise RuntimeError(
                f"STORY[{index}] не имеет text."
            )


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
    """
    Главное меню пользователя.
    """

    buttons = [
        [
            InlineKeyboardButton(
                text="🔥 НАЧАТЬ ОХОТУ",
                callback_data="game_start"
            )
        ],

        [
            InlineKeyboardButton(
                text="📖 Как играть",
                callback_data="how_to_play"
            ),
            InlineKeyboardButton(
                text="🏆 Результаты",
                callback_data="results"
            )
        ],

        [
            InlineKeyboardButton(
                text="💬 Чат",
                callback_data="chat"
            ),
            InlineKeyboardButton(
                text="🆘 Поддержка",
                callback_data="support"
            )
        ]
    ]

    if is_admin(user_id):
        buttons.append([
            InlineKeyboardButton(
                text="🏠 МОЁ ПРОСТРАНСТВО",
                callback_data="admin_panel"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def back_keyboard():
    """
    Универсальная кнопка возврата в главное меню.
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="back_main"
                )
            ]
        ]
    )


def game_exit_keyboard():
    """
    Кнопка выхода из текущей игры.
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 Выйти из охоты",
                    callback_data="game_exit"
                )
            ]
        ]
    )


def research_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Искать улики",
                    callback_data="clues"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👤 Осмотреть место",
                    callback_data="location"
                )
            ],

            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="game_back"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🏠 Главное меню",
                    callback_data="back_main"
                )
            ]
        ]
    )


def clue_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👣 Следы",
                    callback_data="clue_tracks"
                ),

                InlineKeyboardButton(
                    text="📄 Документ",
                    callback_data="clue_document"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔍 Странная деталь",
                    callback_data="clue_detail"
                )
            ],

            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="game_back"
                )
            ]
        ]
    )


def interaction_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤝 Взаимодействовать",
                    callback_data="interact"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🚶 Пройти самому",
                    callback_data="alone"
                )
            ],

            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="game_back"
                )
            ]
        ]
    )


# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit(
    callback: CallbackQuery,
    text,
    reply_markup=None
):
    """
    Безопасно изменяет сообщение.

    Telegram может вернуть ошибку, если пользователь
    нажал одну и ту же кнопку повторно и сообщение
    уже содержит тот же текст/клавиатуру.
    """

    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup
        )

    except Exception as error:
        error_text = str(error).lower()

        if (
            "message is not modified" not in error_text
        ):
            logger.exception(
                "Ошибка изменения сообщения: %s",
                error
            )

            await callback.answer(
                "Не удалось обновить экран.",
                show_alert=True
            )

            return False

    return True


# ============================================================
# MAIN MENU
# ============================================================

async def send_main_menu(message: Message):
    """
    Показывает главное меню.
    """

    user_id = message.from_user.id

    beta_text = ""

    if (
        beta_mode_enabled()
        and not is_beta_tester(user_id)
    ):
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


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def command_start(
    message: Message,
    state: FSMContext
):
    """
    /start

    Всегда сбрасывает FSM-состояние,
    чтобы пользователь не оставался внутри
    чата/поддержки/админского ввода.
    """

    await state.clear()

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await send_main_menu(message)


@dp.message(F.text.casefold() == "старт")
async def text_start(
    message: Message,
    state: FSMContext
):
    """
    Команда «старт» текстом.
    """

    await state.clear()

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await send_main_menu(message)


# ============================================================
# BACK MAIN
# ============================================================

@dp.callback_query(F.data == "back_main")
async def back_main(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Возврат в главное меню.

    FSM обязательно очищается.
    Активная охота при этом НЕ удаляется.
    """

    await state.clear()

    await safe_edit(
        callback,
        """
🔎 <b>OHOTA</b>

Главное меню.
""",
        reply_markup=main_keyboard(
            callback.from_user.id
        )
    )

    await callback.answer()


# ============================================================
# HOW TO PLAY
# ============================================================

@dp.callback_query(F.data == "how_to_play")
async def how_to_play(
    callback: CallbackQuery
):
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
# GAME START
# ============================================================

@dp.callback_query(F.data == "game_start")
async def game_start(
    callback: CallbackQuery
):
    """
    Начало новой охоты.

    Если пользователь уже играет,
    не создаём вторую игру поверх первой.
    """

    user_id = callback.from_user.id

    # --------------------------------------------------------
    # BETA ACCESS
    # --------------------------------------------------------

    if (
        beta_mode_enabled()
        and not is_beta_tester(user_id)
    ):
        await callback.answer(
            "Сейчас доступ только для бета-тестеров.",
            show_alert=True
        )
        return

    # --------------------------------------------------------
    # EXISTING GAME
    # --------------------------------------------------------

    existing_game = get_game(user_id)

    if existing_game:
        if game_expired(existing_game):
            await finish_game(
                callback,
                timeout=True
            )
            return

        elapsed = game_elapsed(existing_game)
        remaining = max(
            0,
            GAME_TIME - elapsed
        )

        minutes = remaining // 60
        seconds = remaining % 60

        await callback.answer(
            f"У тебя уже идёт охота. "
            f"Осталось {minutes:02d}:{seconds:02d}.",
            show_alert=True
        )
        return

    # --------------------------------------------------------
    # CREATE GAME
    # --------------------------------------------------------

    games[user_id] = {
        "step": 0,
        "started": time.monotonic(),
        "clues": set(),
        "interactions": 0,
        "screen": "story"
    }

    save_active_game(user_id)

    # --------------------------------------------------------
    # START ANIMATION
    # --------------------------------------------------------

    await safe_edit(
        callback,
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▱▱▱▱
"""
    )

    await asyncio.sleep(0.25)

    # Проверяем, что игру не удалили во время задержки.
    if user_id not in games:
        await callback.answer()
        return

    await safe_edit(
        callback,
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▱▱▱
"""
    )

    await asyncio.sleep(0.25)

    if user_id not in games:
        await callback.answer()
        return

    await safe_edit(
        callback,
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▱▱
"""
    )

    await asyncio.sleep(0.25)

    if user_id not in games:
        await callback.answer()
        return

    await safe_edit(
        callback,
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▰▱
"""
    )

    await asyncio.sleep(0.25)

    if user_id not in games:
        await callback.answer()
        return

    await safe_edit(
        callback,
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▰▰

<b>ОХОТА НАЧИНАЕТСЯ.</b>
"""
    )

    await asyncio.sleep(0.4)

    if user_id not in games:
        await callback.answer()
        return

    await show_story(callback)

    await callback.answer()


# ============================================================
# STORY DISPLAY
# ============================================================

async def show_story(
    callback: CallbackQuery
):
    """
    Показывает текущий эпизод.
    """

    user_id = callback.from_user.id
    game = get_game(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    # --------------------------------------------------------
    # TIMER
    # --------------------------------------------------------

    if game_expired(game):
        await finish_game(
            callback,
            timeout=True
        )
        return

    step = game["step"]

    # Защита от выхода за границы STORY.
    if step < 0:
        step = 0
        game["step"] = 0

    if step >= len(STORY):
        await finish_game(callback)
        return

    chapter = STORY[step]

    # --------------------------------------------------------
    # KEYBOARD
    # --------------------------------------------------------

    if step == 0:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔥 НАЧАТЬ РАССЛЕДОВАНИЕ",
                        callback_data="next_story"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )

    else:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )

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
    # ============================================================
# NEXT STORY
# ============================================================

@dp.callback_query(F.data == "next_story")
async def next_story(callback: CallbackQuery):
    """
    Переход к следующему эпизоду.

    Перед переходом:
    - проверяем игру;
    - проверяем таймер;
    - увеличиваем шаг;
    - выбираем нужный тип экрана.
    """

    user_id = callback.from_user.id
    game = get_game(user_id)

    if not game:
        await callback.answer(
            "Сначала начни новую охоту.",
            show_alert=True
        )
        return

    if game_expired(game):
        await finish_game(
            callback,
            timeout=True
        )
        return

    # Защита от повторного нажатия.
    if game["step"] >= len(STORY) - 1:
        await finish_game(callback)
        return

    game["step"] += 1
    game["screen"] = "story"

    step = game["step"]

    # --------------------------------------------------------
    # СПЕЦИАЛЬНЫЕ ЭПИЗОДЫ
    # --------------------------------------------------------

    # Эпизоды, в которых игрок должен исследовать место.
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

    # Эпизоды с персонажем.
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


# ============================================================
# GAME BACK
# ============================================================

@dp.callback_query(F.data == "game_back")
async def game_back(callback: CallbackQuery):
    """
    Возврат внутри текущей охоты.

    ВАЖНО:
    Назад не уменьшает step.
    Это возврат к экрану исследования,
    а не откат сюжета.

    Благодаря этому игрок не сможет случайно
    пройти один и тот же сюжет назад-вперёд.
    """

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


# ============================================================
# CLUES
# ============================================================

@dp.callback_query(F.data == "clues")
async def clues(callback: CallbackQuery):
    """
    Открывает список доступных улик.
    """

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


# ============================================================
# CLUE TRACKS
# ============================================================

@dp.callback_query(F.data == "clue_tracks")
async def clue_tracks(callback: CallbackQuery):
    """
    Улика: следы.
    """

    game = await check_game(callback)

    if not game:
        return

    # set() автоматически не даёт начислять
    # одну и ту же улику несколько раз.
    already_found = "tracks" in game["clues"]

    game["clues"].add("tracks")
    game["screen"] = "clue_tracks"

    if already_found:
        message = """
👣 <b>СЛЕДЫ</b>

Ты уже внимательно изучал это место.

Следы идут от двери к стене.

Но есть странность:

человек остановился примерно
в двух метрах от стены.

Следов обратно нет.

Значит, он либо ушёл другим путём,
либо его кто-то забрал.

🔎 <b>Эта улика уже была найдена.</b>
"""
    else:
        message = """
👣 <b>СЛЕДЫ</b>

Следы идут от двери к стене.

Но есть странность:

человек остановился примерно
в двух метрах от стены.

Следов обратно нет.

Значит, он либо ушёл другим путём,
либо его кто-то забрал.

🔎 <b>Улика добавлена.</b>
"""

    await safe_edit(
        callback,
        f"""
{message}

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer(
        "Улика уже изучена."
        if already_found
        else "Улика найдена"
    )


# ============================================================
# CLUE DOCUMENT
# ============================================================

@dp.callback_query(F.data == "clue_document")
async def clue_document(callback: CallbackQuery):
    """
    Улика: документ.
    """

    game = await check_game(callback)

    if not game:
        return

    already_found = "document" in game["clues"]

    game["clues"].add("document")
    game["screen"] = "clue_document"

    if already_found:
        status = "🔎 <b>Эта улика уже была изучена.</b>"
    else:
        status = "🔎 <b>Улика добавлена.</b>"

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

    await callback.answer(
        "Улика уже изучена."
        if already_found
        else "Улика найдена"
    )


# ============================================================
# CLUE DETAIL
# ============================================================

@dp.callback_query(F.data == "clue_detail")
async def clue_detail(callback: CallbackQuery):
    """
    Улика: странная металлическая деталь.
    """

    game = await check_game(callback)

    if not game:
        return

    already_found = "detail" in game["clues"]

    game["clues"].add("detail")
    game["screen"] = "clue_detail"

    if already_found:
        status = "🔎 <b>Эта улика уже была изучена.</b>"
    else:
        status = "🔎 <b>Улика добавлена.</b>"

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

    await callback.answer(
        "Улика уже изучена."
        if already_found
        else "Улика найдена"
    )


# ============================================================
# LOCATION
# ============================================================

@dp.callback_query(F.data == "location")
async def location(callback: CallbackQuery):
    """
    Осмотр места.

    В исходной версии эта кнопка могла бесконечно
    добавлять одну и ту же улику.
    Теперь set() защищает счётчик.
    """

    game = await check_game(callback)

    if not game:
        return

    already_found = "camera" in game["clues"]

    game["clues"].add("camera")
    game["screen"] = "location"

    if already_found:
        status = "🔎 <b>Ты уже осматривал камеру.</b>"
    else:
        status = "🔎 <b>Новая улика добавлена.</b>"

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

    await callback.answer(
        "Камера уже изучена."
        if already_found
        else "Обнаружена новая улика"
    )


# ============================================================
# INTERACTION
# ============================================================

@dp.callback_query(F.data == "interact")
async def interact(callback: CallbackQuery):
    """
    Взаимодействие с персонажем.
    """

    game = await check_game(callback)

    if not game:
        return

    game["interactions"] += 1
    game["screen"] = "interaction_result"

    save_active_game(
        callback.from_user.id
    )

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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="game_back"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ALONE
# ============================================================

@dp.callback_query(F.data == "alone")
async def alone(callback: CallbackQuery):
    """
    Игрок решает продолжить самостоятельно.
    """

    game = await check_game(callback)

    if not game:
        return

    game["interactions"] += 1
    game["screen"] = "alone_result"

    save_active_game(
        callback.from_user.id
    )

    await safe_edit(
        callback,
        f"""
🚶 <b>ПРОЙТИ САМОМУ</b>

Ты не доверяешь незнакомцу.

Продолжаешь исследование самостоятельно.

Через несколько минут находишь
новую деталь, которую он явно
не хотел, чтобы ты увидел.

{game_header(game)}
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="game_back"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# GAME EXIT
# ============================================================

@dp.callback_query(F.data == "game_exit")
async def game_exit(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Выход из охоты.

    Игра удаляется только после подтверждения,
    чтобы случайное нажатие не уничтожало прогресс.
    """

    game = get_game(
        callback.from_user.id
    )

    if not game:
        await state.clear()

        await safe_edit(
            callback,
            """
🔎 <b>OHOTA</b>

Активной охоты нет.
""",
            reply_markup=main_keyboard(
                callback.from_user.id
            )
        )

        await callback.answer()
        return

    if game_expired(game):
        await finish_game(
            callback,
            timeout=True
        )
        return

    await safe_edit(
        callback,
        f"""
⚠️ <b>ВЫЙТИ ИЗ ОХОТЫ?</b>

Текущий прогресс будет потерян.

🔎 Улик: <b>{len(game["clues"])}</b>
🤝 Взаимодействий: <b>{game["interactions"]}</b>

Ты действительно хочешь выйти?
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Да, выйти",
                        callback_data="game_exit_confirm"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="↩️ Продолжить охоту",
                        callback_data="game_exit_cancel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# GAME EXIT CONFIRM
# ============================================================

@dp.callback_query(F.data == "game_exit_confirm")
async def game_exit_confirm(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Подтверждённый выход из игры.
    """

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

    await callback.answer(
        "Охота завершена."
    )


# ============================================================
# GAME EXIT CANCEL
# ============================================================

@dp.callback_query(F.data == "game_exit_cancel")
async def game_exit_cancel(
    callback: CallbackQuery
):
    """
    Отмена выхода.
    Возвращает пользователя на предыдущий
    экран игры.
    """

    game = await check_game(callback)

    if not game:
        return

    screen = game.get(
        "screen",
        "story"
    )

    # --------------------------------------------------------
    # ВОЗВРАТ К ЭКРАНУ
    # --------------------------------------------------------

    if screen == "research":
        await safe_edit(
            callback,
            f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

{STORY[game["step"]]["text"]}

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

Изучи найденные детали
внимательнее.

{game_header(game)}
""",
            reply_markup=clue_keyboard()
        )

    elif screen == "interaction":
        await safe_edit(
            callback,
            f"""
{STORY[game["step"]]["text"]}

{game_header(game)}

Что будешь делать?
""",
            reply_markup=interaction_keyboard()
        )

    else:
        await show_story(callback)

    await callback.answer()


# ============================================================
# SUPPORT FROM GAME
# ============================================================

@dp.callback_query(F.data == "game_support")
async def game_support(
    callback: CallbackQuery,
    state: FSMContext
):
    """
    Поддержка прямо во время игры.

    Прогресс игры НЕ удаляется.
    """

    game = await check_game(callback)

    if not game:
        return

    await state.set_state(
        SupportState.waiting_message
    )

    await state.update_data(
        from_game=True,
        game_step=game["step"]
    )

    await safe_edit(
        callback,
        f"""
🆘 <b>СООБЩИТЬ О ПРОБЛЕМЕ</b>

Ты находишься в эпизоде:
<b>{game["step"] + 1}</b>

Опиши проблему одним сообщением.

Например:

<i>«Кнопка с уликой не работает»</i>

После отправки обращения
ты сможешь вернуться к охоте.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# GAME STATUS
# ============================================================

@dp.callback_query(F.data == "game_status")
async def game_status(
    callback: CallbackQuery
):
    """
    Дополнительный экран текущего прогресса.
    """

    game = await check_game(callback)

    if not game:
        return

    step = game["step"]

    chapter_number = min(
        step + 1,
        len(STORY)
    )

    await safe_edit(
        callback,
        f"""
📊 <b>ТЕКУЩИЙ ПРОГРЕСС</b>

📖 Эпизод:
<b>{chapter_number} / {len(STORY)}</b>

🔎 Найдено улик:
<b>{len(game["clues"])}</b>

🤝 Взаимодействий:
<b>{game["interactions"]}</b>

{game_header(game)}
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="game_exit_cancel"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN CHECK HELPER
# ============================================================

async def require_admin(
    callback: CallbackQuery
):
    """
    Единая проверка доступа администратора.

    Возвращает True, если доступ разрешён.
    """

    if not is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return False

    return True


async def require_admin_message(
    message: Message
):
    """
    Проверка администратора для обычных сообщений.
    """

    return is_admin(
        message.from_user.id
    )


# ============================================================
# SUPPORT SHORTCUT
# ============================================================

def support_keyboard():
    """
    Клавиатура поддержки.
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="back_main"
                )
            ]
        ]
    )


# ============================================================
# CHAT KEYBOARD
# ============================================================

def chat_keyboard():
    """
    Клавиатура чата.
    """

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить чат",
                    callback_data="chat"
                )
            ],

            [
                InlineKeyboardButton(
                    text="◀️ Выйти из чата",
                    callback_data="back_main"
                )
            ]
        ]
    )


# ============================================================
# ADMIN BACK KEYBOARD
# ============================================================

def admin_back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="admin_panel"
                )
            ]
        ]
    )
    # ============================================================
# STORY DISPLAY
# ============================================================

async def show_story(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Охота не запущена.",
            show_alert=True
        )
        return

    # Проверяем таймер перед каждым экраном
    if time.monotonic() - game["started"] >= GAME_TIME:
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

    # Первый экран расследования
    if step == 0:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔥 НАЧАТЬ РАССЛЕДОВАНИЕ",
                        callback_data="next_story"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )

    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )

    await callback.message.edit_text(
        f"""
<b>{escape(chapter["title"])}</b>

{chapter["text"]}

⭐ Сложность: <b>★★★★☆☆</b>

{game_header(game)}
""",
        reply_markup=keyboard
    )


# ============================================================
# NEXT STORY
# ============================================================

@dp.callback_query(F.data == "next_story")
async def next_story(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Сначала начни новую охоту.",
            show_alert=True
        )
        return

    # Таймер
    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    current_step = game.get("step", 0)

    # Защита от повторного нажатия
    if current_step >= len(STORY) - 1:
        await finish_game(callback)
        return

    game["step"] = current_step + 1

    # Сохраняем активную игру
    save_active_game(user_id)

    step = game["step"]

    if step >= len(STORY):
        await finish_game(callback)
        return

    # ========================================================
    # ИССЛЕДОВАНИЕ
    # ========================================================

    if step in {1, 2, 6, 9, 12, 15}:
        await callback.message.edit_text(
            f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

{STORY[step]["text"]}

Что будешь делать?

{game_header(game)}
""",
            reply_markup=research_keyboard()
        )

    # ========================================================
    # ВЗАИМОДЕЙСТВИЕ
    # ========================================================

    elif step in {4, 19}:
        await callback.message.edit_text(
            f"""
{STORY[step]["text"]}

Что будешь делать?

{game_header(game)}
""",
            reply_markup=interaction_keyboard()
        )

    # ========================================================
    # ОБЫЧНЫЙ СЮЖЕТ
    # ========================================================

    else:
        await show_story(callback)

    await callback.answer()


# ============================================================
# GAME BACK
# ============================================================

@dp.callback_query(F.data == "game_back")
async def game_back(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Охота уже завершена.",
            show_alert=True
        )
        return

    # Проверяем таймер
    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    await callback.message.edit_text(
        f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

Ты снова осматриваешь место.

Несколько деталей могут оказаться
важными для расследования.

Что проверить?

{game_header(game)}
""",
        reply_markup=research_keyboard()
    )

    await callback.answer()


# ============================================================
# GAME EXIT
# ============================================================

@dp.callback_query(F.data == "game_exit")
async def game_exit(
    callback: CallbackQuery,
    state: FSMContext
):
    user_id = callback.from_user.id

    await state.clear()

    if user_id in games:
        del games[user_id]

    await callback.message.edit_text(
        """
🏠 <b>ОХОТА ПРЕРВАНА</b>

Текущий забег завершён.

Ты можешь начать новое расследование
в любое время.
""",
        reply_markup=main_keyboard(user_id)
    )

    await callback.answer()


# ============================================================
# CLUES
# ============================================================

@dp.callback_query(F.data == "clues")
async def clues(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Охота не запущена.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    await callback.message.edit_text(
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


# ============================================================
# CLUE: TRACKS
# ============================================================

@dp.callback_query(F.data == "clue_tracks")
async def clue_tracks(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["clues"].add("tracks")

    save_active_game(user_id)

    await callback.message.edit_text(
        f"""
👣 <b>СЛЕДЫ</b>

Следы идут от двери к стене.

Но есть странность:

человек остановился примерно
в двух метрах от стены.

Следов обратно нет.

Значит, он либо ушёл другим путём,
либо его кто-то забрал.

🔎 <b>Улика добавлена.</b>

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer("Улика найдена")


# ============================================================
# CLUE: DOCUMENT
# ============================================================

@dp.callback_query(F.data == "clue_document")
async def clue_document(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["clues"].add("document")

    save_active_game(user_id)

    await callback.message.edit_text(
        f"""
📄 <b>ДОКУМЕНТ</b>

На бумаге видна часть адреса:

<b>17 / 04 / 23</b>

Ниже написано:

<i>«Не там, где ищут все.»</i>

Возможно, это не дата.

🔎 <b>Улика добавлена.</b>

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer("Улика найдена")


# ============================================================
# CLUE: DETAIL
# ============================================================

@dp.callback_query(F.data == "clue_detail")
async def clue_detail(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["clues"].add("detail")

    save_active_game(user_id)

    await callback.message.edit_text(
        f"""
🔍 <b>СТРАННАЯ ДЕТАЛЬ</b>

На металлической детали есть символ.

Он совпадает со знаком
на стене.

Это уже не случайность.

🔎 <b>Улика добавлена.</b>

{game_header(game)}
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer("Улика найдена")


# ============================================================
# LOCATION
# ============================================================

@dp.callback_query(F.data == "location")
async def location(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["clues"].add("camera")

    save_active_game(user_id)

    await callback.message.edit_text(
        f"""
👤 <b>ОСМОТР МЕСТА</b>

Ты замечаешь старую камеру наблюдения.

Она отключена.

Но индикатор питания мигает.

Кто-то недавно включал систему.

🔎 <b>Улика добавлена.</b>

{game_header(game)}
""",
        reply_markup=research_keyboard()
    )

    await callback.answer("Обнаружена новая улика")


# ============================================================
# INTERACTION
# ============================================================

@dp.callback_query(F.data == "interact")
async def interact(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["interactions"] += 1

    save_active_game(user_id)

    await callback.message.edit_text(
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="game_back"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ALONE
# ============================================================

@dp.callback_query(F.data == "alone")
async def alone(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["interactions"] += 1
    game["clues"].add("alone_discovery")

    save_active_game(user_id)

    await callback.message.edit_text(
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➡️ Продолжить",
                        callback_data="next_story"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="game_back"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Выйти",
                        callback_data="game_exit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# GAME HEADER
# ============================================================

def game_header(game):
    elapsed = int(time.monotonic() - game["started"])

    remaining = max(
        0,
        GAME_TIME - elapsed
    )

    minutes = remaining // 60
    seconds = remaining % 60

    return (
        "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Осталось: <b>{minutes:02d}:{seconds:02d}</b>\n"
        f"🔎 Улик: <b>{len(game.get('clues', set()))}</b>\n"
        f"🤝 Взаимодействий: "
        f"<b>{game.get('interactions', 0)}</b>\n"
        "━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# ACTIVE GAME SAVE
# ============================================================

def save_active_game(user_id):
    """
    Безопасно сохраняет текущую игру в памяти.

    Основное состояние игры остаётся в games.
    Эта функция используется как единая точка
    для будущего расширения сохранения прогресса.
    """

    game = games.get(user_id)

    if not game:
        return False

    # Нормализуем структуру игры
    game.setdefault("step", 0)
    game.setdefault("started", time.monotonic())
    game.setdefault("clues", set())
    game.setdefault("interactions", 0)

    return True


# ============================================================
# CHECK GAME
# ============================================================

async def check_game(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Охота не запущена.",
            show_alert=True
        )
        return None

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(
            callback,
            timeout=True
        )
        return None

    return game
    # ============================================================
# FINISH GAME
# ============================================================

async def finish_game(
    callback: CallbackQuery,
    timeout: bool = False
):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.message.edit_text(
            "Охота уже завершена.",
            reply_markup=main_keyboard(user_id)
        )
        return

    elapsed = int(
        time.monotonic() - game["started"]
    )

    # ========================================================
    # TIMEOUT
    # ========================================================

    if timeout:
        games.pop(user_id, None)

        await callback.message.edit_text(
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

    # ========================================================
    # RESULT DATA
    # ========================================================

    clues_count = len(
        game.get("clues", set())
    )

    interactions_count = game.get(
        "interactions",
        0
    )

    username = (
        callback.from_user.username
        or ""
    )

    # ========================================================
    # SAVE RESULT
    # ========================================================

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
                username,
                elapsed,
                clues_count,
                interactions_count,
                datetime.now().isoformat()
            )
        )

        connection.commit()

    finally:
        connection.close()

    # ========================================================
    # REMOVE ACTIVE GAME
    # ========================================================

    games.pop(user_id, None)

    minutes = elapsed // 60
    seconds = elapsed % 60

    await callback.message.edit_text(
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
# RESULTS
# ============================================================

@dp.callback_query(F.data == "results")
async def results(callback: CallbackQuery):
    connection = get_db()

    try:
        rows = connection.execute(
            """
            SELECT username, time_seconds
            FROM results
            ORDER BY time_seconds ASC, id ASC
            LIMIT 10
            """
        ).fetchall()

    finally:
        connection.close()

    text = (
        "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"
    )

    if not rows:
        text += (
            "Пока никто не завершил охоту."
        )

    else:
        for index, row in enumerate(
            rows,
            1
        ):
            username = row[0] or "Игрок"
            seconds = max(0, int(row[1]))

            if username != "Игрок":
                display_name = (
                    f"@{escape(username)}"
                )
            else:
                display_name = "Игрок"

            text += (
                f"<b>{index}.</b> "
                f"{display_name} — "
                f"<b>"
                f"{seconds // 60:02d}:"
                f"{seconds % 60:02d}"
                f"</b>\n"
            )

    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# CHAT
# ============================================================

@dp.callback_query(F.data == "chat")
async def chat(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(
        ChatState.waiting_message
    )

    connection = get_db()

    try:
        rows = connection.execute(
            """
            SELECT username, message
            FROM chat_messages
            ORDER BY id DESC
            LIMIT 10
            """
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
        text += (
            "<b>Последние сообщения:</b>\n\n"
        )

        for username, message_text in reversed(
            rows
        ):
            name = (
                f"@{escape(username)}"
                if username
                else "Игрок"
            )

            text += (
                f"<b>{name}</b>: "
                f"{escape(message_text[:300])}"
                "\n\n"
            )

    else:
        text += (
            "Пока сообщений нет.\n"
        )

    text += (
        "\n<i>Напиши сообщение ниже.</i>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Выйти из чата",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# RECEIVE CHAT MESSAGE
# ============================================================

@dp.message(ChatState.waiting_message)
async def receive_chat_message(
    message: Message,
    state: FSMContext
):
    if not message.text:
        await message.answer(
            "В чат можно отправить только текст."
        )
        return

    if message.text.casefold().strip() == "старт":
        await state.clear()
        await send_main_menu(message)
        return

    user_id = message.from_user.id

    username = (
        message.from_user.username
        or ""
    )

    first_name = (
        message.from_user.first_name
        or ""
    )

    # Всегда обновляем пользователя
    save_user(
        user_id,
        username,
        first_name
    )

    # Ограничиваем длину сообщения
    chat_message = message.text[:2000]

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
                user_id,
                username,
                chat_message,
                datetime.now().isoformat()
            )
        )

        users = connection.execute(
            """
            SELECT user_id
            FROM users
            """
        ).fetchall()

        connection.commit()

    finally:
        connection.close()

    safe_username = escape(username)
    safe_message = escape(chat_message)

    display_name = (
        f"@{safe_username}"
        if username
        else "Игрок"
    )

    chat_text = (
        "💬 <b>ЧАТ</b>\n\n"
        f"<b>{display_name}</b>:\n"
        f"{safe_message}"
    )

    sent = 0

    # Рассылаем сообщение пользователям
    for row in users:
        target_id = row[0]

        try:
            await bot.send_message(
                target_id,
                chat_text
            )
            sent += 1

        except Exception as error:
            logging.warning(
                "Chat send failed for %s: %s",
                target_id,
                error
            )

    await message.answer(
        f"""
✅ <b>Сообщение отправлено.</b>

Его получили активные пользователи чата.
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Продолжить чат",
                        callback_data="chat"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="back_main"
                    )
                ]
            ]
        )
    )


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(
        SupportState.waiting_message
    )

    await callback.message.edit_text(
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


# ============================================================
# RECEIVE SUPPORT
# ============================================================

@dp.message(SupportState.waiting_message)
async def receive_support(
    message: Message,
    state: FSMContext
):
    if (
        message.text
        and message.text.casefold().strip()
        == "старт"
    ):
        await state.clear()
        await send_main_menu(message)
        return

    if not message.text:
        await message.answer(
            "Отправь текстовое сообщение."
        )
        return

    user_id = message.from_user.id

    username = (
        message.from_user.username
        or ""
    )

    first_name = (
        message.from_user.first_name
        or ""
    )

    save_user(
        user_id,
        username,
        first_name
    )

    support_message = message.text[:5000]

    connection = get_db()

    try:
        cursor = connection.execute(
            """
            INSERT INTO support (
                user_id,
                message,
                created_at,
                answered
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                user_id,
                support_message,
                datetime.now().isoformat()
            )
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

    # ========================================================
    # ADMIN NOTIFICATION
    # ========================================================

    if ADMIN_ID:
        try:
            admin_username = escape(
                username or "без username"
            )

            safe_support = escape(
                support_message
            )

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
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✏️ Ответить",
                                callback_data=(
                                    f"support_reply:"
                                    f"{support_id}"
                                )
                            )
                        ]
                    ]
                )
            )

        except Exception as error:
            logging.error(
                "Support notification error: %s",
                error
            )
            # ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎮 Управление охотой",
                    callback_data="admin_game"
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
                    text="🧪 Бета-тест",
                    callback_data="admin_beta"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    callback_data="admin_support"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Результаты",
                    callback_data="admin_results"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Контент",
                    callback_data="admin_content"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Главное меню",
                    callback_data="back_main"
                )
            ]
        ]
    )


# ============================================================
# ADMIN PANEL OPEN
# ============================================================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
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


# ============================================================
# ADMIN GAME
# ============================================================

@dp.callback_query(F.data == "admin_game")
async def admin_game(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    active_games = len(games)

    await callback.message.edit_text(
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="admin_panel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN USERS
# ============================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    connection = get_db()

    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        beta = connection.execute(
            "SELECT COUNT(*) FROM beta_testers"
        ).fetchone()[0]

        results_count = connection.execute(
            "SELECT COUNT(*) FROM results"
        ).fetchone()[0]

        support_count = connection.execute(
            "SELECT COUNT(*) FROM support"
        ).fetchone()[0]

        unanswered = connection.execute(
            """
            SELECT COUNT(*)
            FROM support
            WHERE answered = 0
            """
        ).fetchone()[0]

    finally:
        connection.close()

    await callback.message.edit_text(
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="admin_panel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN BETA
# ============================================================

@dp.callback_query(F.data == "admin_beta")
async def admin_beta(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    connection = get_db()

    try:
        testers = connection.execute(
            """
            SELECT user_id, added_at
            FROM beta_testers
            ORDER BY added_at DESC
            """
        ).fetchall()

    finally:
        connection.close()

    text = """
🧪 <b>БЕТА-ТЕСТ</b>

"""

    if testers:
        text += (
            "<b>Добавленные тестеры:</b>\n\n"
        )

        for row in testers[:30]:
            text += (
                f"• <code>{row[0]}</code>\n"
            )

        if len(testers) > 30:
            text += (
                f"\n<i>И ещё "
                f"{len(testers) - 30}...</i>\n"
            )

        text += "\n"

    else:
        text += (
            "Тестеров пока нет.\n\n"
        )

    text += (
        "Отправь Telegram ID, чтобы "
        "добавить тестера."
    )

    await state.set_state(
        BetaState.waiting_user_id
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# RECEIVE BETA ID
# ============================================================

@dp.message(BetaState.waiting_user_id)
async def receive_beta_id(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer(
            "Отправь Telegram ID."
        )
        return

    value = message.text.strip()

    try:
        beta_user_id = int(value)

    except ValueError:
        await message.answer(
            "Telegram ID должен состоять только "
            "из цифр."
        )
        return

    if beta_user_id <= 0:
        await message.answer(
            "Некорректный Telegram ID."
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
                beta_user_id,
                datetime.now().isoformat()
            )
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


# ============================================================
# ADMIN SUPPORT
# ============================================================

@dp.callback_query(F.data == "admin_support")
async def admin_support(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
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
                created_at,
                answered
            FROM support
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    finally:
        connection.close()

    text = (
        "🆘 <b>ОБРАЩЕНИЯ</b>\n\n"
    )

    buttons = []

    if not rows:
        text += (
            "Обращений пока нет."
        )

    else:
        for row in rows:
            support_id = row[0]
            target_user_id = row[1]
            support_message = row[2]
            answered = row[4]

            status = (
                "✅ Отвечено"
                if answered
                else "🕐 Ожидает ответа"
            )

            text += (
                f"<b>#{support_id}</b> — "
                f"{status}\n"
                f"👤 ID: "
                f"<code>{target_user_id}</code>\n"
                f"💬 "
                f"{escape(support_message[:500])}\n\n"
            )

            if not answered:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=(
                                f"✏️ Ответить "
                                f"#{support_id}"
                            ),
                            callback_data=(
                                f"support_reply:"
                                f"{support_id}"
                            )
                        )
                    ]
                )

    buttons.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin_panel"
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


# ============================================================
# SUPPORT REPLY
# ============================================================

@dp.callback_query(
    F.data.startswith("support_reply:")
)
async def support_reply(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    try:
        support_id = int(
            callback.data.split(":", 1)[1]
        )

    except (ValueError, IndexError):
        await callback.answer(
            "Ошибка обращения.",
            show_alert=True
        )
        return

    connection = get_db()

    try:
        row = connection.execute(
            """
            SELECT
                user_id,
                message,
                answered
            FROM support
            WHERE id = ?
            """,
            (support_id,)
        ).fetchone()

    finally:
        connection.close()

    if not row:
        await callback.answer(
            "Обращение не найдено.",
            show_alert=True
        )
        return

    target_user_id = row[0]
    support_message = row[1]
    answered = row[2]

    if answered:
        await callback.answer(
            "На это обращение уже ответили.",
            show_alert=True
        )
        return

    await state.set_state(
        SupportReplyState.waiting_reply
    )

    await state.update_data(
        support_id=support_id,
        target_user_id=target_user_id
    )

    await callback.message.edit_text(
        f"""
✏️ <b>ОТВЕТ НА ОБРАЩЕНИЕ #{support_id}</b>

👤 Пользователь:
<code>{target_user_id}</code>

Сообщение пользователя:

<i>{escape(support_message[:2000])}</i>

━━━━━━━━━━━━━━━━━━

Напиши ответ одним сообщением.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# RECEIVE SUPPORT REPLY
# ============================================================

@dp.message(
    SupportReplyState.waiting_reply
)
async def receive_support_reply(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await message.answer(
            "Отправь текстовый ответ."
        )
        return

    data = await state.get_data()

    support_id = data.get(
        "support_id"
    )

    target_user_id = data.get(
        "target_user_id"
    )

    if not support_id or not target_user_id:
        await state.clear()

        await message.answer(
            "Обращение не найдено.",
            reply_markup=admin_keyboard()
        )
        return

    reply_text = message.text[:5000]

    # ========================================================
    # SEND TO USER
    # ========================================================

    try:
        await bot.send_message(
            target_user_id,
            f"""
🆘 <b>ОТВЕТ ПОДДЕРЖКИ</b>

{escape(reply_text)}
"""
        )

    except Exception as error:
        logging.error(
            "Support reply error: %s",
            error
        )

        await message.answer(
            """
❌ <b>Не удалось отправить ответ.</b>

Возможно, пользователь заблокировал бота
или Telegram временно не позволяет
отправить сообщение.
""",
            reply_markup=admin_keyboard()
        )

        return

    # ========================================================
    # SAVE ANSWER
    # ========================================================

    connection = get_db()

    try:
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
                reply_text,
                datetime.now().isoformat(),
                support_id
            )
        )

        connection.commit()

    finally:
        connection.close()

    await state.clear()

    await message.answer(
        f"""
✅ <b>Ответ отправлен.</b>

Обращение #{support_id}
помечено как отвеченное.
""",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN RESULTS
# ============================================================

@dp.callback_query(F.data == "admin_results")
async def admin_results(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    connection = get_db()

    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM results"
        ).fetchone()[0]

        rows = connection.execute(
            """
            SELECT
                username,
                time_seconds,
                clues,
                interactions
            FROM results
            ORDER BY time_seconds ASC, id ASC
            LIMIT 10
            """
        ).fetchall()

    finally:
        connection.close()

    text = (
        "📊 <b>РЕЗУЛЬТАТЫ</b>\n\n"
        f"Всего завершений: "
        f"<b>{total}</b>\n\n"
    )

    if rows:
        for index, row in enumerate(
            rows,
            1
        ):
            username = (
                row[0] or "Игрок"
            )

            seconds = max(
                0,
                int(row[1])
            )

            if username != "Игрок":
                display_name = (
                    f"@{escape(username)}"
                )
            else:
                display_name = "Игрок"

            text += (
                f"<b>{index}.</b> "
                f"{display_name}\n"
                f"⏱ "
                f"{seconds // 60:02d}:"
                f"{seconds % 60:02d}\n"
                f"🔎 Улик: "
                f"<b>{row[2]}</b>\n"
                f"🤝 Взаимодействий: "
                f"<b>{row[3]}</b>\n\n"
            )

    else:
        text += (
            "Результатов пока нет."
        )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="admin_panel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# ADMIN CONTENT
# ============================================================

@dp.callback_query(F.data == "admin_content")
async def admin_content(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        f"""
🖼 <b>КОНТЕНТ</b>

Игровой контент отделён
от интерфейса управления.

📖 Сюжет:
<b>{len(STORY)} эпизодов</b>

🔎 Улики:
<b>4+</b>

👤 Персонажи:
<b>присутствуют</b>

📸 Изображения:
<b>готовы для расширения</b>

━━━━━━━━━━━━━━━━━━

Игровая логика работает
отдельно от контента.
""",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="admin_panel"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# UNKNOWN MESSAGE
# ============================================================

@dp.message()
async def unknown_message(message: Message):
    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(
        """
🔎 <b>OHOTA</b>

Я не понял эту команду.

Используй кнопки меню.
""",
        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# START BOT
# ============================================================

async def main():
    init_db()

    logging.info(
        "OHOTA BOT STARTED"
    )

    try:
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())