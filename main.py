import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import settings
from database.db import init_db, SessionLocal
from database.repositories.players import get_or_create_player


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    async with SessionLocal() as session:
        player = await get_or_create_player(
            session=session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

        await session.commit()

    await message.answer(
        f"🕵️ <b>ОХОТА</b>\n\n"
        f"Привет, {message.from_user.first_name or 'охотник'}!\n\n"
        f"Твой профиль создан.\n\n"
        f"⭐ Очки: <b>{player.total_score}</b>\n"
        f"⚡ Опыт: <b>{player.xp}</b>\n\n"
        f"🎯 Скоро здесь появятся первые миссии."
    )


@dp.message()
async def message_handler(message: Message):
    await message.answer(
        "🕵️ <b>ОХОТА</b>\n\n"
        "Используй /start, чтобы открыть свой профиль."
    )


async def main():
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    try:
        logging.info("Бот запускается...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())