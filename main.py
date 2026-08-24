from pathlib import Path
import zipfile, textwrap

root = Path("/mnt/data/ohota_game_bot_new")
if root.exists():
    import shutil
    shutil.rmtree(root)

files = {
"main.py": r'''import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import settings
from database.db import init_db
from database.repositories.users import upsert_user
from handlers.menu import router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

dp = Dispatcher()
dp.include_router(router)


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await upsert_user(message.from_user)
    await message.answer(
        "👋 Добро пожаловать в <b>Охота</b>!\n\n"
        "Выбери раздел в меню ниже."
    )


async def main() -> None:
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    logging.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
''',

"config.py": r'''import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    database_url: str
    beta_mode: bool


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    admin_id_raw = os.getenv("ADMIN_ID", "0").strip()
    try:
        admin_id = int(admin_id_raw)
    except ValueError as exc:
        raise RuntimeError("ADMIN_ID must be an integer") from exc

    database_url = os.getenv("DATABASE_URL", "sqlite:///ohota.db").strip()
    beta_mode = os.getenv("BETA_MODE", "true").lower() in {
        "1", "true", "yes", "on"
    }

    return Settings(
        bot_token=token,
        admin_id=admin_id,
        database_url=database_url,
        beta_mode=beta_mode,
    )


settings = load_settings()
''',

"database/__init__.py": "",
"database/db.py": r'''from pathlib import Path

import aiosqlite


DB_PATH = Path("ohota.db")


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                points INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()
''',

"database/repositories/__init__.py": "",
"database/repositories/users.py": r'''import aiosqlite
from aiogram.types import User

from database.db import DB_PATH


async def upsert_user(user: User) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (
                telegram_id, username, first_name, last_name
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user.id,
                user.username,
                user.first_name or "",
                user.last_name or "",
            ),
        )
        await db.commit()


async def get_user(telegram_id: int) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT telegram_id, username, first_name, last_name, points
            FROM users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        return await cursor.fetchone()


async def get_top_users(limit: int = 10) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT telegram_id, username, first_name, points
            FROM users
            ORDER BY points DESC, telegram_id ASC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()
''',

"handlers/__init__.py": "",
"handlers/menu.py": r'''from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.repositories.users import get_top_users, get_user


router = Router()


def main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🕵️ Начать", callback_data="start_game")
    builder.button(text="🎯 Миссии", callback_data="missions")
    builder.button(text="👤 Мой профиль", callback_data="profile")
    builder.button(text="🏆 Рейтинг", callback_data="rating")
    builder.button(text="📜 Правила", callback_data="rules")
    builder.button(text="💬 Чат", callback_data="chat")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


async def show_menu(message: Message) -> None:
    await message.answer(
        "📋 <b>Главное меню</b>\n\nВыбери нужный раздел:",
        reply_markup=main_menu(),
    )


@router.message(F.text == "☰ Меню")
async def menu_message(message: Message) -> None:
    await show_menu(message)


@router.callback_query(F.data == "start_game")
async def start_game(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "🕵️ <b>Начать</b>\n\n"
        "Игровая механика пока находится в разработке.\n"
        "Следующим этапом подключим реальные миссии."
        ,
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "missions")
async def missions(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "🎯 <b>Миссии</b>\n\n"
        "Пока активных миссий нет.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery) -> None:
    await callback.answer()
    user = await get_user(callback.from_user.id)

    if not user:
        text = "👤 Профиль пока не создан."
    else:
        telegram_id, username, first_name, last_name, points = user
        name = " ".join(x for x in [first_name, last_name] if x).strip()
        username_text = f"@{username}" if username else "не указан"
        text = (
            "👤 <b>Мой профиль</b>\n\n"
            f"Имя: {name or 'не указано'}\n"
            f"Username: {username_text}\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"⭐ Очки: <b>{points}</b>"
        )

    await callback.message.edit_text(text, reply_markup=main_menu())


@router.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery) -> None:
    await callback.answer()
    users = await get_top_users()

    if not users:
        text = "🏆 <b>Рейтинг</b>\n\nПока здесь никого нет."
    else:
        lines = ["🏆 <b>Рейтинг</b>\n"]
        for index, (_, username, first_name, points) in enumerate(users, 1):
            name = f"@{username}" if username else (first_name or "Игрок")
            lines.append(f"{index}. {name} — ⭐ {points}")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=main_menu())


@router.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "📜 <b>Правила</b>\n\n"
        "1. Выполняй доступные миссии.\n"
        "2. Следуй условиям каждой миссии.\n"
        "3. Не используй запрещённые способы выполнения.\n"
        "4. За выполнение миссий начисляются очки.\n\n"
        "Подробные правила добавим вместе с игровой механикой.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "chat")
async def chat(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text(
        "💬 <b>Чат</b>\n\n"
        "Ссылка на чат будет добавлена позже.",
        reply_markup=main_menu(),
    )
''',

".env.example": r'''BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_ID=YOUR_TELEGRAM_ID
DATABASE_URL=sqlite:///ohota.db
BETA_MODE=true
''',

".gitignore": r'''__pycache__/
*.py[cod]
*.db
.env
.venv/
venv/
.idea/
.vscode/
.DS_Store
''',

"requirements.txt": r'''aiogram>=3.22,<4
aiosqlite>=0.21,<1
python-dotenv>=1.1,<2
''',

"README.md": r'''# OhotaGameBot

Чистая базовая версия Telegram-бота на Python + aiogram 3.

## Что уже есть

- `/start`
- главное меню
- 🕵️ Начать
- 🎯 Миссии
- 👤 Мой профиль
- 🏆 Рейтинг
- 📜 Правила
- 💬 Чат
- SQLite база пользователей
- настройки через `.env`

## Запуск

1. Установить Python 3.11+.
2. Установить зависимости:

```bash
pip install -r requirements.txt
```

3. Скопировать `.env.example` в `.env`.
4. Заполнить `BOT_TOKEN` и `ADMIN_ID`.
5. Запустить:

```bash
python main.py
```

## Важно

Файл `.env` не загружается в GitHub. Токен бота хранится только в переменных окружения.
''',
}

for rel, content in files.items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

zip_path = Path("/mnt/data/ohota_game_bot_new.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for path in root.rglob("*"):
        if path.is_file():
            z.write(path, path.relative_to(root))

print(f"Готово: {zip_path}")
print("Файлов:", len(files))
