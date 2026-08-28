import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramNetworkError
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

    # ============================================================
# NEXT STORY
# ============================================================



# ============================================================
# GAME BACK
# ============================================================



# ============================================================
# CLUES
# ============================================================



# ============================================================
# CLUE TRACKS
# ============================================================



# ============================================================
# CLUE DOCUMENT
# ============================================================



# ============================================================
# CLUE DETAIL
# ============================================================



# ============================================================
# LOCATION
# ============================================================



# ============================================================
# INTERACTION
# ============================================================



# ============================================================
# ALONE
# ============================================================



# ============================================================
# GAME EXIT
# ============================================================



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