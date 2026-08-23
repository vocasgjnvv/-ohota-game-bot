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

GAME_TIME = 60 * 60
DB_NAME = "ohota.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ============================================================
# BOT / DISPATCHER
# ВАЖНО: создаём ДО обработчиков @dp...
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(storage=MemoryStorage())


# ============================================================
# DATABASE
# ============================================================

def get_db():
    return sqlite3.connect(DB_NAME)


def init_db():
    connection = get_db()
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
            answered INTEGER DEFAULT 0
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

    connection.commit()
    connection.close()


def save_user(user_id, username, first_name):
    connection = get_db()

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
    connection.close()


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ============================================================
# GAME STATE
# ============================================================

games = {}


# ============================================================
# STATES
# ============================================================

class SupportState(StatesGroup):
    waiting_message = State()


class BetaState(StatesGroup):
    waiting_user_id = State()


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
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
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

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_keyboard():
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
# MAIN MENU
# ============================================================

async def send_main_menu(message: Message):
    await message.answer(
        """
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

<b>Готов начать?</b>
""",
        reply_markup=main_keyboard(message.from_user.id)
    )


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext):
    await state.clear()

    save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await send_main_menu(message)


# ============================================================
# "СТАРТ"
# ============================================================

@dp.message(F.text.casefold() == "старт")
async def text_start(message: Message, state: FSMContext):
    await state.clear()
    await send_main_menu(message)


# ============================================================
# BACK
# ============================================================

@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await callback.message.edit_text(
        """
🔎 <b>OHOTA</b>

Главное меню.
""",
        reply_markup=main_keyboard(callback.from_user.id)
    )

    await callback.answer()


# ============================================================
# HOW TO PLAY
# ============================================================

@dp.callback_query(F.data == "how_to_play")
async def how_to_play(callback: CallbackQuery):
    await callback.message.edit_text(
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
async def game_start(callback: CallbackQuery):
    user_id = callback.from_user.id

    games[user_id] = {
        "step": 0,
        "started": time.monotonic(),
        "clues": set(),
        "interactions": 0
    }

    await callback.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▱▱▱▱
"""
    )

    await asyncio.sleep(0.35)

    await callback.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▱▱▱
"""
    )

    await asyncio.sleep(0.35)

    await callback.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▱▱
"""
    )

    await asyncio.sleep(0.35)

    await callback.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▰▱
"""
    )

    await asyncio.sleep(0.35)

    await callback.message.edit_text(
        """
🔎 <b>ПОДГОТОВКА ОХОТЫ...</b>

▰▰▰▰▰

<b>ОХОТА НАЧИНАЕТСЯ.</b>
"""
    )

    await asyncio.sleep(0.5)

    await show_story(callback)

    await callback.answer()


# ============================================================
# STORY
# ============================================================

async def show_story(callback: CallbackQuery):
    user_id = callback.from_user.id

    game = games.get(user_id)

    if not game:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    step = game["step"]

    if step >= len(STORY):
        return

    chapter = STORY[step]

    if step == 0:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔥 НАЧАТЬ РАССЛЕДОВАНИЕ",
                        callback_data="next_story"
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
                        callback_data="back_main"
                    )
                ]
            ]
        )

    await callback.message.edit_text(
        f"""
<b>{chapter["title"]}</b>

{chapter["text"]}

⭐ Сложность: <b>★★★★☆☆</b>
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

    if time.monotonic() - game["started"] >= GAME_TIME:
        await finish_game(callback, timeout=True)
        return

    game["step"] += 1
    step = game["step"]

    if step >= len(STORY):
        await finish_game(callback)
        return

    if step in {1, 2, 6, 9, 12, 15}:
        await callback.message.edit_text(
            f"""
🔎 <b>ИССЛЕДОВАНИЕ</b>

{STORY[step]["text"]}

Что будешь делать?
""",
            reply_markup=research_keyboard()
        )

    elif step in {4, 19}:
        await callback.message.edit_text(
            f"""
{STORY[step]["text"]}

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
    await callback.message.edit_text(
        """
🔎 <b>ИССЛЕДОВАНИЕ</b>

Ты осматриваешь место ещё раз.

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
    await callback.message.edit_text(
        """
🔎 <b>УЛИКИ</b>

Несколько деталей могут оказаться
важными для расследования.

Изучи их внимательно.
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer()


@dp.callback_query(F.data == "clue_tracks")
async def clue_tracks(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    games[user_id]["clues"].add("tracks")

    await callback.message.edit_text(
        """
👣 <b>СЛЕДЫ</b>

Следы идут от двери к стене.

Но есть странность:

человек остановился примерно
в двух метрах от стены.

Следов обратно нет.

Значит, он либо ушёл другим путём,
либо его кто-то забрал.

🔎 <b>Улика добавлена.</b>
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer("Улика найдена")


@dp.callback_query(F.data == "clue_document")
async def clue_document(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    games[user_id]["clues"].add("document")

    await callback.message.edit_text(
        """
📄 <b>ДОКУМЕНТ</b>

На бумаге видна часть адреса:

<b>17 / 04 / 23</b>

Ниже написано:

<i>«Не там, где ищут все.»</i>

Возможно, это не дата.

🔎 <b>Улика добавлена.</b>
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer("Улика найдена")


@dp.callback_query(F.data == "clue_detail")
async def clue_detail(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    games[user_id]["clues"].add("detail")

    await callback.message.edit_text(
        """
🔍 <b>СТРАННАЯ ДЕТАЛЬ</b>

На металлической детали есть символ.

Он совпадает со знаком
на стене.

Это уже не случайность.

🔎 <b>Улика добавлена.</b>
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

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        """
👤 <b>ОСМОТР МЕСТА</b>

Ты замечаешь старую камеру наблюдения.

Она отключена.

Но индикатор питания мигает.

Кто-то недавно включал систему.
""",
        reply_markup=clue_keyboard()
    )

    await callback.answer()


# ============================================================
# INTERACTION
# ============================================================

@dp.callback_query(F.data == "interact")
async def interact(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    games[user_id]["interactions"] += 1

    await callback.message.edit_text(
        """
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
                ]
            ]
        )
    )

    await callback.answer()


@dp.callback_query(F.data == "alone")
async def alone(callback: CallbackQuery):
    user_id = callback.from_user.id

    if user_id not in games:
        await callback.answer(
            "Начни новую охоту.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        """
🚶 <b>ПРОЙТИ САМОМУ</b>

Ты не доверяешь незнакомцу.

Продолжаешь исследование самостоятельно.

Через несколько минут находишь
новую деталь, которую он явно
не хотел, чтобы ты увидел.
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
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# FINISH
# ============================================================

async def finish_game(callback: CallbackQuery, timeout=False):
    user_id = callback.from_user.id
    game = games.get(user_id)

    if not game:
        await callback.message.edit_text(
            "Охота уже завершена.",
            reply_markup=main_keyboard(user_id)
        )
        return

    elapsed = int(time.monotonic() - game["started"])

    if timeout:
        del games[user_id]

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

    connection = get_db()

    connection.execute("""
        INSERT INTO results (
            user_id,
            username,
            time_seconds,
            clues,
            interactions,
            finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        callback.from_user.username or "",
        elapsed,
        len(game["clues"]),
        game["interactions"],
        datetime.now().isoformat()
    ))

    connection.commit()
    connection.close()

    minutes = elapsed // 60
    seconds = elapsed % 60

    del games[user_id]

    await callback.message.edit_text(
        f"""
🏆 <b>ОХОТА ЗАВЕРШЕНА</b>

Ты завершил расследование.

⏱ Время:
<b>{minutes:02d}:{seconds:02d}</b>

🔎 Улик:
<b>{len(game["clues"])}</b>

🤝 Взаимодействий:
<b>{game["interactions"]}</b>

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

    rows = connection.execute("""
        SELECT username, time_seconds
        FROM results
        ORDER BY time_seconds ASC
        LIMIT 10
    """).fetchall()

    connection.close()

    text = "🏆 <b>ТАБЛИЦА ЛИДЕРОВ</b>\n\n"

    if not rows:
        text += "Пока никто не завершил охоту."
    else:
        for index, row in enumerate(rows, 1):
            username = row[0] or "Игрок"
            seconds = row[1]

            text += (
                f"<b>{index}.</b> "
                f"@{username} — "
                f"<b>{seconds // 60:02d}:{seconds % 60:02d}</b>\n"
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
async def chat(callback: CallbackQuery):
    await callback.message.edit_text(
        """
💬 <b>ЧАТ</b>

Общение игроков.

Здесь можно обсуждать охоту,
делиться впечатлениями
и соревноваться с другими.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportState.waiting_message)

    await callback.message.edit_text(
        """
🆘 <b>ПОДДЕРЖКА</b>

Напиши сообщение о проблеме.

Например:

<i>«Улика не открывается»</i>

После отправки обращение попадёт
в твоё пространство администратора.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


@dp.message(SupportState.waiting_message)
async def receive_support(
    message: Message,
    state: FSMContext
):
    if message.text and message.text.casefold() == "старт":
        await state.clear()
        await send_main_menu(message)
        return

    if not message.text:
        await message.answer(
            "Отправь текстовое сообщение."
        )
        return

    connection = get_db()

    connection.execute("""
        INSERT INTO support (
            user_id,
            message,
            created_at
        )
        VALUES (?, ?, ?)
    """, (
        message.from_user.id,
        message.text,
        datetime.now().isoformat()
    ))

    connection.commit()
    connection.close()

    await state.clear()

    await message.answer(
        """
✅ <b>Сообщение отправлено.</b>

Обращение сохранено.
""",
        reply_markup=main_keyboard(message.from_user.id)
    )

    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"""
🆘 <b>НОВОЕ ОБРАЩЕНИЕ</b>

ID: <code>{message.from_user.id}</code>

Пользователь:
@{message.from_user.username or "без username"}

Сообщение:

{message.text}
"""
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

    await callback.message.edit_text(
        """
🎮 <b>УПРАВЛЕНИЕ ОХОТОЙ</b>

📖 Сюжет: активен
🧩 Эпизодов: 20
⭐ Сложность: 4/6
⏱ Лимит: 60 минут
🏆 Соревнование: включено
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

    total = connection.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    connection.close()

    await callback.message.edit_text(
        f"""
👥 <b>ПОЛЬЗОВАТЕЛИ</b>

Всего:
<b>{total}</b>

Сейчас проходят охоту:
<b>{len(games)}</b>
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

    await state.set_state(BetaState.waiting_user_id)

    await callback.message.edit_text(
        """
🧪 <b>БЕТА-ТЕСТ</b>

Отправь Telegram ID тестера.

Например:

<code>123456789</code>
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


@dp.message(BetaState.waiting_user_id)
async def receive_beta_id(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        return

    try:
        user_id = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Telegram ID должен состоять только из цифр."
        )
        return

    connection = get_db()

    connection.execute("""
        INSERT OR IGNORE INTO beta_testers (
            user_id,
            added_at
        )
        VALUES (?, ?)
    """, (
        user_id,
        datetime.now().isoformat()
    ))

    connection.commit()
    connection.close()

    await state.clear()

    await message.answer(
        f"""
✅ <b>Тестер добавлен.</b>

ID:
<code>{user_id}</code>
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

    rows = connection.execute("""
        SELECT id, user_id, message, created_at
        FROM support
        ORDER BY id DESC
        LIMIT 15
    """).fetchall()

    connection.close()

    text = "🆘 <b>ОБРАЩЕНИЯ</b>\n\n"

    if not rows:
        text += "Обращений пока нет."
    else:
        for row in rows:
            text += (
                f"<b>#{row[0]}</b>\n"
                f"👤 ID: <code>{row[1]}</code>\n"
                f"💬 {row[2][:400]}\n\n"
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

    total = connection.execute(
        "SELECT COUNT(*) FROM results"
    ).fetchone()[0]

    rows = connection.execute("""
        SELECT username, time_seconds, clues, interactions
        FROM results
        ORDER BY time_seconds ASC
        LIMIT 10
    """).fetchall()

    connection.close()

    text = (
        f"📊 <b>РЕЗУЛЬТАТЫ</b>\n\n"
        f"Всего завершений: <b>{total}</b>\n\n"
    )

    for index, row in enumerate(rows, 1):
        username = row[0] or "Игрок"

        text += (
            f"{index}. @{username}\n"
            f"⏱ {row[1] // 60:02d}:{row[1] % 60:02d}\n"
            f"🔎 Улик: {row[2]}\n"
            f"🤝 Взаимодействий: {row[3]}\n\n"
        )

    if not rows:
        text += "Результатов пока нет."

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
        """
🖼 <b>КОНТЕНТ</b>

Раздел предназначен для управления:

📖 сюжетом
🧩 этапами
🔎 уликами
👤 персонажами
📸 изображениями

Игровая логика отдельно от контента.
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
    await message.answer(
        """
🔎 <b>OHOTA</b>

Я не понял эту команду.

Используй кнопки меню.
""",
        reply_markup=main_keyboard(message.from_user.id)
    )


# ============================================================
# START BOT
# ============================================================

async def main():
    init_db()

    logging.info("OHOTA BOT STARTED")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())