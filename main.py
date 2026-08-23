import os
import sqlite3
import asyncio
import logging
import re
from contextlib import closing
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "ohota.db")

# В RelaxDev добавь:
# ADMIN_ID=твой_telegram_id
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавь токен бота в переменную окружения BOT_TOKEN."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# БАЗА
# ============================================================

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
            hp INTEGER DEFAULT 100,
            reputation INTEGER DEFAULT 50,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            investigations INTEGER DEFAULT 0,
            best_place INTEGER,
            interactions INTEGER DEFAULT 0,
            accusations INTEGER DEFAULT 0,
            correct_accusations INTEGER DEFAULT 0,
            successful_lies INTEGER DEFAULT 0,
            caught_lies INTEGER DEFAULT 0,
            clues_found INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS registrations (
            telegram_id INTEGER,
            hunt_code TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_id, hunt_code)
        );

        CREATE TABLE IF NOT EXISTS hunts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            difficulty TEXT DEFAULT 'Средняя',
            duration INTEGER DEFAULT 60,
            reward INTEGER DEFAULT 500,
            status TEXT DEFAULT 'preparing'
        );

        CREATE TABLE IF NOT EXISTS hunt_progress (
            telegram_id INTEGER,
            hunt_code TEXT,
            stage INTEGER DEFAULT 1,
            path TEXT DEFAULT 'solo',
            started INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            PRIMARY KEY (telegram_id, hunt_code)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            item_description TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            answered INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            action TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        # Добавляем базовую охоту, если её ещё нет.
        existing = db.execute(
            "SELECT id FROM hunts WHERE code = '001'"
        ).fetchone()

        if not existing:
            db.execute(
                """
                INSERT INTO hunts
                (code, title, description, difficulty, duration, reward, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "001",
                    "Последний рейс",
                    "Таинственное исчезновение. "
                    "У тебя есть 20 этапов расследования. "
                    "Часть информации придётся добывать самостоятельно, "
                    "а в некоторых моментах можно взаимодействовать "
                    "с другими игроками.",
                    "Средняя",
                    60,
                    500,
                    "preparing"
                )
            )

        db.commit()


def log_action(telegram_id, action):
    with closing(conn()) as db:
        db.execute(
            "INSERT INTO logs (telegram_id, action) VALUES (?, ?)",
            (telegram_id, action)
        )
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


def update_user(telegram_id, field, value):
    allowed = {
        "xp",
        "hp",
        "reputation",
        "wins",
        "losses",
        "investigations",
        "interactions",
        "accusations",
        "correct_accusations",
        "successful_lies",
        "caught_lies",
        "clues_found",
    }

    if field not in allowed:
        return

    with closing(conn()) as db:
        db.execute(
            f"UPDATE users SET {field} = ? WHERE telegram_id = ?",
            (value, telegram_id)
        )
        db.commit()


def change_stat(telegram_id, field, amount):
    allowed = {
        "xp",
        "hp",
        "reputation",
        "wins",
        "losses",
        "interactions",
        "accusations",
        "correct_accusations",
        "successful_lies",
        "caught_lies",
        "clues_found",
    }

    if field not in allowed:
        return

    with closing(conn()) as db:
        db.execute(
            f"""
            UPDATE users
            SET {field} = MAX(0, {field} + ?)
            WHERE telegram_id = ?
            """,
            (amount, telegram_id)
        )
        db.commit()


# ============================================================
# ВСПОМОГАТЕЛЬНОЕ
# ============================================================

def is_admin(telegram_id):
    return ADMIN_ID != 0 and telegram_id == ADMIN_ID


def level_from_xp(xp):
    return max(1, xp // 250 + 1)


def level_name(level):
    names = {
        1: "Новичок",
        2: "Следопыт",
        3: "Стажёр",
        4: "Детектив",
        5: "Опытный детектив",
        6: "Охотник",
        7: "Мастер расследований",
        8: "Легенда",
    }

    return names.get(level, "Легенда")


def progress_bar(value, maximum=100, size=10):
    value = max(0, min(value, maximum))
    filled = round((value / maximum) * size)
    return "🟩" * filled + "⬜" * (size - filled)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def start_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🔎 НАЧАТЬ",
        callback_data="start_game"
    )

    kb.adjust(1)
    return kb.as_markup()


def main_menu(user_id):
    kb = InlineKeyboardBuilder()

    buttons = [
        ("🎯 ОХОТЫ", "hunt"),
        ("👤 МОЙ ПРОФИЛЬ", "profile"),
        ("🏆 РЕЙТИНГ", "rating"),
        ("🎒 ИНВЕНТАРЬ", "inventory"),
        ("📊 СТАТИСТИКА", "stats"),
        ("📜 ПРАВИЛА", "rules"),
        ("🆘 ПОДДЕРЖКА", "support"),
    ]

    for text, callback in buttons:
        kb.button(text=text, callback_data=callback)

    if is_admin(user_id):
        kb.button(
            text="👑 МОЁ ПРОСТРАНСТВО",
            callback_data="admin"
        )

    kb.adjust(2, 2, 2, 1)

    return kb.as_markup()


def back_button():
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data="menu")
    return kb.as_markup()


def admin_menu():
    kb = InlineKeyboardBuilder()

    buttons = [
        ("🎯 ОХОТЫ", "admin_hunts"),
        ("🧩 ЗАДАНИЯ", "admin_tasks"),
        ("🔎 УЛИКИ", "admin_clues"),
        ("👥 ИГРОКИ", "admin_players"),
        ("📊 СТАТИСТИКА", "admin_stats"),
        ("📢 РАССЫЛКА", "admin_broadcast"),
        ("🆘 ПОДДЕРЖКА", "admin_support"),
        ("⚙️ НАСТРОЙКИ", "admin_settings"),
        ("◀️ В главное меню", "menu"),
    ]

    for text, callback in buttons:
        kb.button(text=text, callback_data=callback)

    kb.adjust(2, 2, 2, 2, 1)

    return kb.as_markup()


# ============================================================
# СТАРТ
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):
    user = get_user(message.from_user.id)

    if user:
        await message.answer(
            "🕵️ <b>ОХОТА</b>\n\n"
            f"С возвращением, <b>{escape(user['nickname'])}</b>.\n\n"
            "Твоё расследование продолжается.",
            reply_markup=main_menu(message.from_user.id)
        )
        return

    await message.answer(
        "🕵️ <b>ОХОТА</b>\n\n"
        "Онлайн-расследования, где каждая улика может изменить "
        "ход дела.\n\n"
        "Готов начать?",
        reply_markup=start_keyboard()
    )


@dp.callback_query(F.data == "start_game")
async def start_game(callback: CallbackQuery):
    user = get_user(callback.from_user.id)

    if user:
        await callback.message.edit_text(
            "🕵️ <b>ОХОТА</b>\n\n"
            f"С возвращением, <b>{escape(user['nickname'])}</b>.",
            reply_markup=main_menu(callback.from_user.id)
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🕵️ <b>РЕГИСТРАЦИЯ</b>\n\n"
        "Придумай игровой псевдоним.\n\n"
        "3–20 символов.\n"
        "Буквы, цифры, пробел, дефис или _.",
    )

    await callback.answer()


# ============================================================
# РЕГИСТРАЦИЯ
# ============================================================

@dp.message(F.text)
async def text_handler(message: Message):
    if message.text.startswith("/"):
        return

    user = get_user(message.from_user.id)

    if not user:
        await registration(message)
        return

    # Сообщение поддержки
    state = get_setting(f"support_{message.from_user.id}")

    if state == "waiting":
        with closing(conn()) as db:
            db.execute(
                """
                INSERT INTO support_messages
                (telegram_id, message)
                VALUES (?, ?)
                """,
                (message.from_user.id, message.text)
            )
            db.commit()

        set_setting(f"support_{message.from_user.id}", "sent")

        if ADMIN_ID:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    "🆘 <b>НОВАЯ ЗАЯВКА В ПОДДЕРЖКУ</b>\n\n"
                    f"Игрок: <b>{escape(user['nickname'])}</b>\n"
                    f"ID: <code>{message.from_user.id}</code>\n\n"
                    f"{escape(message.text)}"
                )
            except Exception:
                pass

        await message.answer(
            "✅ <b>Сообщение отправлено.</b>\n\n"
            "Мы получили твоё обращение.",
            reply_markup=main_menu(message.from_user.id)
        )
        return

    await message.answer(
        "Выбери действие в меню.",
        reply_markup=main_menu(message.from_user.id)
    )


async def registration(message: Message):
    nickname = message.text.strip()

    if not re.fullmatch(
        r"[A-Za-zА-Яа-яЁё0-9 _-]{3,20}",
        nickname
    ):
        await message.answer(
            "❌ Псевдоним должен содержать 3–20 символов.\n"
            "Разрешены буквы, цифры, пробел, дефис и _."
        )
        return

    bad_words = (
        "хуй",
        "пизда",
        "ебать",
        "блядь",
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
            "❌ Такой псевдоним уже занят.\n"
            "Придумай другой."
        )
        return

    log_action(
        message.from_user.id,
        f"Регистрация игрока #{game_number}"
    )

    await message.answer(
        "🕵️ <b>РЕГИСТРАЦИЯ ЗАВЕРШЕНА</b>\n\n"
        f"🎫 Игровой номер: <b>#{game_number}</b>\n"
        f"👤 Псевдоним: <b>{escape(nickname)}</b>\n\n"
        "Теперь ты внутри.\n"
        "Дальше начинается охота.",
        reply_markup=main_menu(message.from_user.id)
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

@dp.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🕵️ <b>ОХОТА</b>\n\n"
        "Выбери действие.",
        reply_markup=main_menu(callback.from_user.id)
    )
    await callback.answer()


# ============================================================
# ПРОФИЛЬ
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)

    if not user:
        await callback.message.edit_text(
            "❌ Сначала нажми /start.",
            reply_markup=back_button()
        )
        await callback.answer()
        return

    level = level_from_xp(user["xp"])

    text = (
        "👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"🎫 Игрок: <b>#{user['game_number']}</b>\n"
        f"🕵️ Имя: <b>{escape(user['nickname'])}</b>\n\n"
        f"🎖 Уровень: <b>{level}</b> — {level_name(level)}\n"
        f"⭐ XP: <b>{user['xp']}</b>\n"
        f"❤️ HP: <b>{user['hp']}</b>/100\n"
        f"🎭 Репутация: <b>{user['reputation']}</b>/100\n\n"
        f"🏆 Победы: <b>{user['wins']}</b>\n"
        f"🔎 Расследования: <b>{user['investigations']}</b>\n"
        f"🤝 Взаимодействия: <b>{user['interactions']}</b>\n"
        f"🎭 Успешные обманы: <b>{user['successful_lies']}</b>\n"
        f"🔍 Раскрытые обманы: <b>{user['caught_lies']}</b>\n"
        f"⚠️ Обвинения: <b>{user['accusations']}</b>\n"
        f"🔎 Найдено улик: <b>{user['clues_found']}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# ============================================================
# СТАТИСТИКА
# ============================================================

@dp.callback_query(F.data == "stats")
async def stats(callback: CallbackQuery):
    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer("Сначала зарегистрируйся.", show_alert=True)
        return

    text = (
        "📊 <b>МОЯ СТАТИСТИКА</b>\n\n"
        f"⭐ XP: <b>{user['xp']}</b>\n"
        f"❤️ HP: <b>{user['hp']}</b>\n"
        f"🎭 Репутация: <b>{user['reputation']}</b>\n\n"
        f"🏆 Побед: <b>{user['wins']}</b>\n"
        f"❌ Поражений: <b>{user['losses']}</b>\n"
        f"🔎 Расследований: <b>{user['investigations']}</b>\n"
        f"🔎 Улик: <b>{user['clues_found']}</b>\n"
        f"🤝 Взаимодействий: <b>{user['interactions']}</b>\n"
        f"🎭 Успешных обманов: <b>{user['successful_lies']}</b>\n"
        f"🔍 Раскрытых обманов: <b>{user['caught_lies']}</b>\n"
        f"⚠️ Обвинений: <b>{user['accusations']}</b>\n"
        f"✅ Правильных обвинений: <b>{user['correct_accusations']}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )
    await callback.answer()


# ============================================================
# РЕЙТИНГ
# ============================================================

@dp.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery):
    with closing(conn()) as db:
        rows = db.execute(
            """
            SELECT nickname, xp, wins, reputation
            FROM users
            ORDER BY xp DESC, wins DESC, reputation DESC
            LIMIT 10
            """
        ).fetchall()

    lines = []

    medals = {
        1: "🥇",
        2: "🥈",
        3: "🥉"
    }

    for index, row in enumerate(rows, 1):
        medal = medals.get(index, f"{index}.")
        lines.append(
            f"{medal} <b>{escape(row['nickname'])}</b>\n"
            f"   ⭐ {row['xp']} XP · 🏆 {row['wins']} · 🎭 {row['reputation']}"
        )

    text = (
        "🏆 <b>ТОП ДЕТЕКТИВОВ</b>\n\n"
        + (
            "\n\n".join(lines)
            if lines
            else "Пока игроков нет."
        )
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )
    await callback.answer()


# ============================================================
# ИНВЕНТАРЬ
# ============================================================

@dp.callback_query(F.data == "inventory")
async def inventory(callback: CallbackQuery):
    with closing(conn()) as db:
        rows = db.execute(
            """
            SELECT item_name, item_description
            FROM inventory
            WHERE telegram_id = ?
            ORDER BY id DESC
            """,
            (callback.from_user.id,)
        ).fetchall()

    if not rows:
        text = (
            "🎒 <b>ИНВЕНТАРЬ</b>\n\n"
            "Пока здесь пусто.\n\n"
            "Во время расследований ты сможешь получать "
            "улики и специальные предметы."
        )
    else:
        lines = []

        for row in rows:
            lines.append(
                f"🔎 <b>{escape(row['item_name'])}</b>\n"
                f"{escape(row['item_description'])}"
            )

        text = (
            "🎒 <b>ИНВЕНТАРЬ</b>\n\n"
            + "\n\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )
    await callback.answer()


# ============================================================
# ПРАВИЛА
# ============================================================

@dp.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery):
    text = (
        "📜 <b>ПРАВИЛА «ОХОТЫ»</b>\n\n"
        "🎯 Каждая охота — отдельное расследование.\n\n"
        "🧠 Большую часть дела можно пройти самостоятельно.\n\n"
        "🤝 В некоторых этапах можно взаимодействовать "
        "с другими игроками.\n\n"
        "🎭 Игроки могут говорить правду или пытаться обмануть.\n\n"
        "🔎 Полученную информацию можно проверять.\n\n"
        "❤️ HP — игровые очки.\n"
        "⭐ XP — постоянный прогресс.\n"
        "🎭 Репутация показывает твою историю действий.\n\n"
        "🏆 В финале расследования определяется результат."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )
    await callback.answer()


# ============================================================
# ОХОТЫ
# ============================================================

@dp.callback_query(F.data == "hunt")
async def hunt(callback: CallbackQuery):
    with closing(conn()) as db:
        rows = db.execute(
            """
            SELECT *
            FROM hunts
            ORDER BY id ASC
            """
        ).fetchall()

    kb = InlineKeyboardBuilder()

    for row in rows:
        status_icon = {
            "preparing": "🟡",
            "active": "🟢",
            "finished": "⚫"
        }.get(row["status"], "⚪")

        kb.button(
            text=f"{status_icon} Охота №{row['code']} — {row['title']}",
            callback_data=f"hunt_{row['code']}"
        )

    kb.button(
        text="◀️ Назад",
        callback_data="menu"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        "🎯 <b>ОХОТЫ</b>\n\n"
        "Выбери расследование.",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("hunt_"))
async def hunt_details(callback: CallbackQuery):
    code = callback.data.replace("hunt_", "", 1)

    with closing(conn()) as db:
        hunt_row = db.execute(
            "SELECT * FROM hunts WHERE code = ?",
            (code,)
        ).fetchone()

    if not hunt_row:
        await callback.answer("Охота не найдена.", show_alert=True)
        return

    status_text = {
        "preparing": "🟡 Подготовка",
        "active": "🟢 Активна",
        "finished": "⚫ Завершена"
    }.get(hunt_row["status"], "⚪ Неизвестно")

    kb = InlineKeyboardBuilder()

    if hunt_row["status"] == "active":
        kb.button(
            text="🔎 ПРОДОЛЖИТЬ",
            callback_data=f"play_{code}"
        )
    elif hunt_row["status"] == "preparing":
        kb.button(
            text="🎯 ЗАПИСАТЬСЯ",
            callback_data=f"join_{code}"
        )

    kb.button(
        text="◀️ Назад",
        callback_data="hunt"
    )

    kb.adjust(1)

    text = (
        f"🕵️ <b>ОХОТА №{hunt_row['code']}</b>\n\n"
        f"📖 <b>«{escape(hunt_row['title'])}»</b>\n\n"
        f"{escape(hunt_row['description'])}\n\n"
        f"🎚 Сложность: <b>{hunt_row['difficulty']}</b>\n"
        f"⏱ Время: <b>{hunt_row['duration']} мин.</b>\n"
        f"⭐ Награда: <b>{hunt_row['reward']} XP</b>\n"
        f"📡 Статус: <b>{status_text}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# РЕГИСТРАЦИЯ НА ОХОТУ
# ============================================================

@dp.callback_query(F.data.startswith("join_"))
async def join_hunt(callback: CallbackQuery):
    code = callback.data.replace("join_", "", 1)

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала зарегистрируйся.",
            show_alert=True
        )
        return

    with closing(conn()) as db:
        try:
            db.execute(
                """
                INSERT INTO registrations
                (telegram_id, hunt_code)
                VALUES (?, ?)
                """,
                (callback.from_user.id, code)
            )

            db.commit()

            db.execute(
                """
                INSERT OR IGNORE INTO hunt_progress
                (telegram_id, hunt_code)
                VALUES (?, ?)
                """,
                (callback.from_user.id, code)
            )

            db.commit()

            text = (
                "✅ <b>ТЫ В СПИСКЕ</b>\n\n"
                f"🎯 Охота №{code}\n\n"
                "Когда расследование начнётся, "
                "бот откроет тебе доступ."
            )

        except sqlite3.IntegrityError:
            text = (
                "ℹ️ <b>Ты уже зарегистрирован.</b>\n\n"
                "Жди запуска расследования."
            )

    await callback.message.edit_text(
        text,
        reply_markup=back_button()
    )

    await callback.answer()


# ============================================================
# ИГРА
# ============================================================

@dp.callback_query(F.data.startswith("play_"))
async def play_hunt(callback: CallbackQuery):
    code = callback.data.replace("play_", "", 1)

    with closing(conn()) as db:
        progress = db.execute(
            """
            SELECT *
            FROM hunt_progress
            WHERE telegram_id = ? AND hunt_code = ?
            """,
            (callback.from_user.id, code)
        ).fetchone()

    if not progress:
        await callback.answer(
            "Сначала зарегистрируйся на охоту.",
            show_alert=True
        )
        return

    stage = progress["stage"]

    kb = InlineKeyboardBuilder()

    kb.button(
        text="🧠 ПРОЙТИ САМОМУ",
        callback_data=f"solo_{code}_{stage}"
    )

    # Взаимодействие доступно на отдельных этапах.
    if stage in (6, 11, 16):
        kb.button(
            text="🤝 ВЗАИМОДЕЙСТВОВАТЬ",
            callback_data=f"social_{code}_{stage}"
        )

    kb.button(
        text="📂 МОИ УЛИКИ",
        callback_data="inventory"
    )

    kb.button(
        text="◀️ Выйти из расследования",
        callback_data="hunt"
    )

    kb.adjust(1)

    text = (
        f"🔎 <b>РАССЛЕДОВАНИЕ</b>\n\n"
        f"🎯 Охота №{code}\n"
        f"🧩 Этап: <b>{stage}/20</b>\n\n"
        f"{progress_bar(stage, 20)}\n\n"
        "Выбери способ продолжения."
    )

    if stage in (6, 11, 16):
        text += (
            "\n\n🤝 На этом этапе можно "
            "получить информацию от другого игрока.\n"
            "Но взаимодействие необязательно."
        )

    await callback.message.edit_text(
        text,
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("solo_"))
async def solo_path(callback: CallbackQuery):
    _, code, stage = callback.data.split("_")
    stage = int(stage)

    # Пока это каркас механики.
    # Реальные задания будем добавлять в следующем этапе.
    await callback.message.edit_text(
        f"🧠 <b>САМОСТОЯТЕЛЬНОЕ РАССЛЕДОВАНИЕ</b>\n\n"
        f"Этап {stage}/20\n\n"
        "Ты решил не обращаться к другим игрокам.\n\n"
        "⚠️ Самостоятельный путь сложнее, "
        "но никто не сможет тебя обмануть.\n\n"
        "🔎 Здесь появится полноценное логическое "
        "задание расследования.",
        reply_markup=back_button()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("social_"))
async def social_path(callback: CallbackQuery):
    _, code, stage = callback.data.split("_")

    change_stat(
        callback.from_user.id,
        "interactions",
        1
    )

    log_action(
        callback.from_user.id,
        f"Взаимодействие в охоте {code}, этап {stage}"
    )

    await callback.message.edit_text(
        "🤝 <b>ВЗАИМОДЕЙСТВИЕ</b>\n\n"
        "Ты решил обратиться к другому игроку.\n\n"
        "🔎 В полноценной версии здесь появится "
        "система поиска подходящего игрока, "
        "приватная комната, обмен информацией, "
        "ложные сведения и проверка.\n\n"
        "⚠️ Помни: другой игрок может солгать.",
        reply_markup=back_button()
    )

    await callback.answer()


# ============================================================
# ПОДДЕРЖКА
# ============================================================

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    set_setting(
        f"support_{callback.from_user.id}",
        "waiting"
    )

    await callback.message.edit_text(
        "🆘 <b>ПОДДЕРЖКА</b>\n\n"
        "Напиши одним сообщением, что произошло.\n\n"
        "Сообщение будет передано администрации.",
        reply_markup=back_button()
    )

    await callback.answer()


# ============================================================
# АДМИНКА
# ============================================================

@dp.callback_query(F.data == "admin")
async def admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "👑 <b>МОЁ ПРОСТРАНСТВО</b>\n\n"
        "Полное управление «ОХОТОЙ».\n\n"
        "Выбери раздел:",
        reply_markup=admin_menu()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("admin_"))
async def admin_sections(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return

    section = callback.data

    if section == "admin_hunts":
        text = (
            "🎯 <b>УПРАВЛЕНИЕ ОХОТАМИ</b>\n\n"
            "Здесь будет управление всеми расследованиями:\n\n"
            "➕ Создание\n"
            "✏️ Редактирование\n"
            "▶️ Запуск\n"
            "⏸ Пауза\n"
            "⏹ Завершение\n"
            "👥 Участники"
        )

    elif section == "admin_tasks":
        text = (
            "🧩 <b>ЗАДАНИЯ</b>\n\n"
            "Здесь будет управление 20 этапами каждой охоты.\n\n"
            "🧠 Самостоятельные задания\n"
            "🤝 Социальные этапы\n"
            "🎭 Особые события\n"
            "⚠️ Финальные обвинения"
        )

    elif section == "admin_clues":
        text = (
            "🔎 <b>УЛИКИ</b>\n\n"
            "Здесь будет управление уликами, "
            "их связями и доступностью для игроков."
        )

    elif section == "admin_players":
        with closing(conn()) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

        text = (
            "👥 <b>ИГРОКИ</b>\n\n"
            f"Всего игроков: <b>{count}</b>\n\n"
            "Полное управление игроками "
            "будет доступно здесь."
        )

    elif section == "admin_stats":
        with closing(conn()) as db:
            players = db.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            registrations = db.execute(
                "SELECT COUNT(*) FROM registrations"
            ).fetchone()[0]

            logs = db.execute(
                "SELECT COUNT(*) FROM logs"
            ).fetchone()[0]

        text = (
            "📊 <b>СТАТИСТИКА ПРОЕКТА</b>\n\n"
            f"👥 Игроков: <b>{players}</b>\n"
            f"🎯 Регистраций на охоты: <b>{registrations}</b>\n"
            f"📋 Действий записано: <b>{logs}</b>"
        )

    elif section == "admin_broadcast":
        text = (
            "📢 <b>РАССЫЛКА</b>\n\n"
            "Здесь ты сможешь отправлять сообщения:\n\n"
            "👥 всем игрокам;\n"
            "🎯 участникам конкретной охоты;\n"
            "🟢 активным игрокам."
        )

    elif section == "admin_support":
        with closing(conn()) as db:
            count = db.execute(
                """
                SELECT COUNT(*)
                FROM support_messages
                WHERE answered = 0
                """
            ).fetchone()[0]

        text = (
            "🆘 <b>ПОДДЕРЖКА</b>\n\n"
            f"Новых обращений: <b>{count}</b>\n\n"
            "Здесь будут обращения игроков "
            "и ответы прямо из админки."
        )

    elif section == "admin_settings":
        text = (
            "⚙️ <b>НАСТРОЙКИ</b>\n\n"
            "👑 Владелец: <b>"
            f"{escape(get_setting('admin_name') or 'Не задан')}"
            "</b>\n\n"
            "Здесь позже можно будет менять "
            "название проекта, описание, имя администратора "
            "и игровые настройки."
        )

    else:
        text = "Раздел пока не настроен."

    kb = InlineKeyboardBuilder()
    kb.button(
        text="◀️ В пространство",
        callback_data="admin"
    )

    await callback.message.edit_text(
        text,
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# НАСТРОЙКИ
# ============================================================

def get_setting(key):
    with closing(conn()) as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        ).fetchone()

    return row["value"] if row else None


def set_setting(key, value):
    with closing(conn()) as db:
        db.execute(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (key, value)
        )
        db.commit()


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    init_db()

    logging.info("🕵️ Бот «ОХОТА» запускается...")
    logging.info("ADMIN_ID=%s", ADMIN_ID)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())