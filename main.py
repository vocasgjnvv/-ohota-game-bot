import asyncio
import logging
import os
import sqlite3
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")

# ТВОЙ Telegram ID.
# Например: ADMIN_ID=123456789
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

GAME_TIME = 60 * 60

logging.basicConfig(level=logging.INFO)


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

DB = "ohota.db"


def db():
    return sqlite3.connect(DB)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS support (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            created_at TEXT,
            answered INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS beta_testers (
            user_id INTEGER PRIMARY KEY,
            added_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            time_seconds INTEGER,
            finished_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_user(user_id, username, first_name):
    conn = db()
    conn.execute("""
        INSERT OR REPLACE INTO users
        (user_id, username, first_name, created_at)
        VALUES (?, ?, ?, COALESCE(
            (SELECT created_at FROM users WHERE user_id = ?),
            ?
        ))
    """, (
        user_id,
        username or "",
        first_name or "",
        user_id,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


# ============================================================
# СОСТОЯНИЯ
# ============================================================

class SupportState(StatesGroup):
    waiting_message = State()


class AdminState(StatesGroup):
    waiting_beta_id = State()


# ============================================================
# ИГРОВЫЕ СЕССИИ
# ============================================================

# Незавершённая игра хранится только в памяти.
# Пользователь не может продолжить старый забег после перезапуска бота.
games = {}


def is_admin(user_id):
    return user_id == ADMIN_ID and ADMIN_ID != 0


def is_beta(user_id):
    conn = db()
    row = conn.execute(
        "SELECT user_id FROM beta_testers WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row is not None


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton(
            text="🔥 НАЧАТЬ ОХОТУ",
            callback_data="game_start"
        )],
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
                text="🏠 Моё пространство",
                callback_data="admin_panel"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="back_main"
            )
        ]
    ])


def game_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
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
                text="🏠 Главное меню",
                callback_data="back_main"
            )
        ]
    ])


def clue_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])


def interaction_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
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
        ]
    ])


# ============================================================
# СЮЖЕТ
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

Ниже находится фотография.

На фотографии — заброшенное здание.

И только одна деталь кажется неправильной:
на двери виден свежий след.

Кто-то был там совсем недавно.

<b>Твоя охота начинается сейчас.</b>
""",
    },
    {
        "title": "ЭПИЗОД 1",
        "text": """
Ты подходишь к зданию.

Вокруг тишина.

Но возле двери находятся три вещи:

👣 следы обуви
📄 кусок бумаги
🔩 металлическая деталь

Что-то здесь явно произошло недавно.
""",
    },
    {
        "title": "ЭПИЗОД 2",
        "text": """
Следы ведут не внутрь здания.

Они заканчиваются возле старой стены.

На стене виден едва заметный знак.

Кто-то явно хотел оставить ориентир.
""",
    },
    {
        "title": "ЭПИЗОД 3",
        "text": """
За стеной находится небольшой проход.

На полу лежит телефон.

Экран разбит.

Но одно уведомление всё ещё отображается:

<b>«Он знает, что ты пришёл.»</b>
""",
    },
    {
        "title": "ЭПИЗОД 4",
        "text": """
Ты слышишь шаги.

Из темноты появляется человек.

Он останавливается в нескольких метрах.

— Ты тоже его ищешь?
""",
    },
    {
        "title": "ЭПИЗОД 5",
        "text": """
После разговора становится ясно:

исчезнувший человек расследовал не одно событие.

Он собирал цепочку улик.

И последняя улика находится здесь.
""",
    },
    {
        "title": "ЭПИЗОД 6",
        "text": """
Внутри помещения находятся старые фотографии.

На каждой фотографии — одно и то же место.

Но сделаны они в разные годы.

На последней фотографии появляется человек,
которого ты уже видел.
""",
    },
    {
        "title": "ЭПИЗОД 7",
        "text": """
Ты понимаешь:

кто-то следил за расследованием ещё до твоего появления.

На столе лежит записка:

<i>«Не верь первому человеку, который предложит помощь.»</i>
""",
    },
    {
        "title": "ЭПИЗОД 8",
        "text": """
Вторая часть записки спрятана внутри старого шкафа.

Она содержит координаты.

Но одной цифры не хватает.
""",
    },
    {
        "title": "ЭПИЗОД 9",
        "text": """
Ты возвращаешься к человеку, которого встретил раньше.

Он утверждает, что ничего не знает.

Но на его руке есть тот же знак,
который был возле здания.
""",
    },
    {
        "title": "ЭПИЗОД 10",
        "text": """
Теперь у тебя два варианта.

Довериться человеку.

Или проверить его историю самостоятельно.
""",
    },
    {
        "title": "ЭПИЗОД 11",
        "text": """
Проверка показывает противоречие.

Он соврал.

Но это ещё не доказывает,
что именно он причастен к исчезновению.
""",
    },
    {
        "title": "ЭПИЗОД 12",
        "text": """
В старом архиве находится дело,
которое связывает все найденные места.

Дата последнего события совпадает
с датой исчезновения.
""",
    },
    {
        "title": "ЭПИЗОД 13",
        "text": """
В деле отсутствует последняя страница.

Кто-то специально её вырвал.

На обратной стороне папки остаётся отпечаток.
""",
    },
    {
        "title": "ЭПИЗОД 14",
        "text": """
Отпечаток приводит тебя к следующей точке.

Но теперь ты понимаешь:

за тобой тоже наблюдают.
""",
    },
    {
        "title": "ЭПИЗОД 15",
        "text": """
Ты находишь тайник.

Внутри — фотографии,
ключ и последняя часть сообщения.
""",
    },
    {
        "title": "ЭПИЗОД 16",
        "text": """
Сообщение содержит главное:

исчезновение было запланировано.

Но неизвестно, кем.
""",
    },
    {
        "title": "ЭПИЗОД 17",
        "text": """
Все найденные улики складываются
в одну последовательность.

Ты наконец понимаешь,
куда нужно идти.
""",
    },
    {
        "title": "ЭПИЗОД 18",
        "text": """
Последнее место находится совсем рядом.

Но дверь заперта.

Ключ из тайника подходит.
""",
    },
    {
        "title": "ЭПИЗОД 19",
        "text": """
Внутри находится человек.

Тот самый, которого ты искал.

Но он говорит:

— Ты всё понял неправильно.
""",
    },
    {
        "title": "ФИНАЛ",
        "text": """
Ты смотришь на все найденные улики.

Теперь решение за тобой.

Кто на самом деле организовал исчезновение?

Твой ответ определит финал охоты.
"""
    }
]


# ============================================================
# ОТПРАВКА ГЛАВНОГО ЭКРАНА
# ============================================================

async def show_main(message: Message):
    await message.answer(
        """
🔎 <b>OHOTA</b>

Добро пожаловать.

Здесь тебя ждёт одна история,
одна охота и ограниченное время.

⏱ <b>Лимит: 60 минут</b>
⭐ <b>Сложность: 4/6</b>

Найди улики.
Общайся с людьми.
Принимай решения.
И постарайся закончить расследование быстрее остальных.

<b>Готов начать?</b>
""",
        reply_markup=main_menu(message.from_user.id)
    )


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext):
    await state.clear()

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await show_main(message)


# ============================================================
# ОБЫЧНЫЙ ТЕКСТ "СТАРТ"
# ============================================================

@dp.message(F.text.lower() == "старт")
async def text_start(message: Message, state: FSMContext):
    await state.clear()
    await show_main(message)


# ============================================================
# НАЗАД
# ============================================================

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()

    await call.message.edit_text(
        """
🔎 <b>OHOTA</b>

Главное меню.

Выбери действие:
""",
        reply_markup=main_menu(call.from_user.id)
    )

    await call.answer()


# ============================================================
# КАК ИГРАТЬ
# ============================================================

@dp.callback_query(F.data == "how_to_play")
async def how_to_play(call: CallbackQuery):
    await call.message.edit_text(
        """
📖 <b>КАК ИГРАТЬ</b>

Перед тобой одна большая история.

🔎 Ищи улики.
👤 Общайся с персонажами.
🧩 Анализируй найденную информацию.
⚠️ Принимай решения.

⭐ Сложность: <b>★★★★☆☆</b>

⏱ На весь забег — <b>60 минут</b>.

🏆 Побеждает тот, кто быстрее
и правильно проходит охоту.

<b>Важно:</b> не все подсказки лежат
на поверхности.
""",
        reply_markup=back_menu()
    )

    await call.answer()


# ============================================================
# НАЧАЛО ИГРЫ
# ============================================================

@dp.callback_query(F.data == "game_start")
async def game_start(call: CallbackQuery):
    user_id = call.from_user.id

    # Новая игра
    games[user_id] = {
        "step": 0,
        "started": time.time(),
        "clues": set(),
        "interactions": 0
    }

    # Маленькая загрузка
    await call.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▱▱▱▱
"""
    )

    await asyncio.sleep(0.5)

    await call.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▱▱▱
"""
    )

    await asyncio.sleep(0.5)

    await call.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▱▱
"""
    )

    await asyncio.sleep(0.5)

    await call.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▰▱
"""
    )

    await asyncio.sleep(0.5)

    await show_story(call)

    await call.answer()


async def show_story(call: CallbackQuery):
    user_id = call.from_user.id

    if user_id not in games:
        return

    step = games[user_id]["step"]

    if step >= len(STORY):
        return

    chapter = STORY[step]

    if step == 0:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 НАЧАТЬ РАССЛЕДОВАНИЕ",
                    callback_data="next_story"
                )
            ]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Продолжить",
                    callback_data="next_story"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Выйти",
                    callback_data="back_main"
                )
            ]
        ])

    await call.message.edit_text(
        f"""
<b>{chapter["title"]}</b>

{chapter["text"]}

⭐ Сложность: <b>★★★★☆☆</b>
⏱ Забег продолжается.
""",
        reply_markup=keyboard
    )


# ============================================================
# СЛЕДУЮЩИЙ ЭТАП
# ============================================================

@dp.callback_query(F.data == "next_story")
async def next_story(call: CallbackQuery):
    user_id = call.from_user.id

    if user_id not in games:
        await call.answer(
            "Сначала начни новую охоту.",
            show_alert=True
        )
        return

    elapsed = time.time() - games[user_id]["started"]

    if elapsed >= GAME_TIME:
        await finish_game(call, timeout=True)
        return

    games[user_id]["step"] += 1

    step = games[user_id]["step"]

    # Перед несколькими сюжетными этапами
    # даём интерактивное