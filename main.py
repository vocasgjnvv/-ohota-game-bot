import os
import sqlite3
import asyncio
import logging
import re
from contextlib import closing
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/ohota.db")

if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with closing(conn()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            game_number INTEGER UNIQUE NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            xp INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            investigations INTEGER DEFAULT 0,
            best_place INTEGER,
            interactions INTEGER DEFAULT 0,
            accusations INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS registrations (
            telegram_id INTEGER,
            hunt_code TEXT,
            UNIQUE(telegram_id, hunt_code)
        );
        """)
        db.commit()

def menu():
    kb = InlineKeyboardBuilder()
    for text, data in [
        ("🎯 Начать охоту", "hunt"),
        ("👤 Мой профиль", "profile"),
        ("🏆 Рейтинг", "rating"),
        ("📜 Правила", "rules"),
        ("💬 Чат", "chat"),
    ]:
        kb.button(text=text, callback_data=data)
    kb.adjust(1)
    return kb.as_markup()

def back():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="menu")
    return kb.as_markup()

def get_user(tg_id):
    with closing(conn()) as db:
        return db.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()

@dp.message(CommandStart())
async def start(message: Message):
    user = get_user(message.from_user.id)
    if user:
        await message.answer(
            f"🕵️ <b>ОХОТА</b>\n\nС возвращением, {user['nickname']}! "
            f"Твой номер: <b>#{user['game_number']}</b>.",
            reply_markup=menu()
        )
    else:
        await message.answer(
            "🕵️ <b>Добро пожаловать в «ОХОТУ»!</b>\n\n"
            "Придумай игровой псевдоним и отправь его следующим сообщением.\n"
            "3–20 символов, без мата и оскорблений."
        )

@dp.message(F.text)
async def register(message: Message):
    if message.text.startswith("/"):
        return
    if get_user(message.from_user.id):
        await message.answer("Выбери действие:", reply_markup=menu())
        return

    nickname = message.text.strip()
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9 _-]{3,20}", nickname):
        await message.answer("❌ Псевдоним должен содержать 3–20 букв, цифр, пробел, дефис или _.")
        return

    bad = ("хуй", "пизд", "еб", "бляд", "сука", "дебил")
    if any(x in nickname.lower().replace("ё", "е") for x in bad):
        await message.answer("❌ Такой псевдоним нельзя использовать.")
        return

    with closing(conn()) as db:
        last = db.execute("SELECT COALESCE(MAX(game_number),1000) FROM users").fetchone()[0]
        try:
            db.execute(
                "INSERT INTO users (telegram_id,game_number,nickname) VALUES (?,?,?)",
                (message.from_user.id, last + 1, nickname)
            )
            db.commit()
        except sqlite3.IntegrityError:
            await message.answer("❌ Этот псевдоним уже занят. Придумай другой.")
            return

    await message.answer(
        f"✅ Регистрация завершена!\n\nИгровой номер: <b>#{last+1}</b>\n"
        f"Псевдоним: <b>{nickname}</b>",
        reply_markup=menu()
    )

@dp.callback_query(F.data == "menu")
async def menu_cb(c: CallbackQuery):
    await c.message.edit_text("🕵️ <b>Главное меню «ОХОТЫ»</b>", reply_markup=menu())
    await c.answer()

@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    u = get_user(c.from_user.id)
    if not u:
        await c.message.edit_text("Сначала отправь /start.", reply_markup=back())
    else:
        await c.message.edit_text(
            f"👤 <b>Мой профиль</b>\n\n"
            f"Номер: <b>#{u['game_number']}</b>\n"
            f"Псевдоним: <b>{u['nickname']}</b>\n"
            f"⭐ XP: <b>{u['xp']}</b>\n"
            f"🏆 Победы: <b>{u['wins']}</b>\n"
            f"🔎 Расследования: <b>{u['investigations']}</b>\n"
            f"🥇 Лучшее место: <b>{u['best_place'] or '—'}</b>",
            reply_markup=back()
        )
    await c.answer()

@dp.callback_query(F.data == "rules")
async def rules(c: CallbackQuery):
    await c.message.edit_text(
        "📜 <b>Правила «ОХОТЫ»</b>\n\n"
        "Охота длится 60 минут. Игроки получают зацепки, расследуют дело, "
        "взаимодействуют друг с другом и в конце могут сделать финальное обвинение.\n\n"
        "Полная игровая механика будет добавлена следующим этапом.",
        reply_markup=back()
    )
    await c.answer()

@dp.callback_query(F.data == "rating")
async def rating(c: CallbackQuery):
    with closing(conn()) as db:
        rows = db.execute(
            "SELECT nickname,xp FROM users ORDER BY xp DESC, game_number LIMIT 10"
        ).fetchall()
    text = "🏆 <b>Рейтинг</b>\n\n"
    text += "\n".join(f"{i}. {r['nickname']} — ⭐ {r['xp']} XP" for i,r in enumerate(rows,1)) or "Пока пусто."
    await c.message.edit_text(text, reply_markup=back())
    await c.answer()

@dp.callback_query(F.data == "hunt")
async def hunt(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 УЧАСТВОВАТЬ", callback_data="join")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    await c.message.edit_text(
        "🕵️ <b>ОХОТА №001</b>\n"
        "«Последний рейс»\n\n"
        "⏱ 60 минут\n💰 Бесплатно\n🏆 Награды: XP и рейтинг\n\n"
        "Охота пока находится в подготовке.",
        reply_markup=kb.as_markup()
    )
    await c.answer()

@dp.callback_query(F.data == "join")
async def join(c: CallbackQuery):
    if not get_user(c.from_user.id):
        await c.message.edit_text("Сначала зарегистрируйся через /start.", reply_markup=back())
    else:
        with closing(conn()) as db:
            try:
                db.execute("INSERT INTO registrations VALUES (?,?)", (c.from_user.id, "001"))
                db.commit()
                text = "✅ Ты зарегистрирован на ОХОТУ №001!"
            except sqlite3.IntegrityError:
                text = "ℹ️ Ты уже зарегистрирован на эту охоту."
        await c.message.edit_text(text, reply_markup=back())
    await c.answer()

@dp.callback_query(F.data == "chat")
async def chat(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Открыть чат", url="https://t.me/ohota_online_chat")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    await c.message.edit_text("💬 Официальный чат «ОХОТЫ»", reply_markup=kb.as_markup())
    await c.answer()

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
