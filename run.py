import asyncio
import logging
import os
import random

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

import main


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("ohota_runner")

INITIAL_BACKOFF = float(os.getenv("OHOTA_INITIAL_BACKOFF", "1"))
MAX_BACKOFF = float(os.getenv("OHOTA_MAX_BACKOFF", "60"))


async def run_polling_with_retries():
    main.init_db()

    bot = Bot(token=main.BOT_TOKEN)

    backoff = INITIAL_BACKOFF

    try:
        while True:
            try:
                logger.info("Запуск ОХОТЫ...")

                await main.dp.start_polling(bot)

                logger.info("Polling остановлен.")
                break

            except asyncio.CancelledError:
                raise

            except TelegramNetworkError as error:
                logger.warning(
                    "Ошибка сети Telegram: %s",
                    error,
                )

            except (ConnectionResetError, OSError) as error:
                logger.warning(
                    "Ошибка соединения: %s",
                    error,
                )

            except Exception as error:
                logger.exception(
                    "Ошибка бота: %s",
                    error,
                )

            sleep_for = min(backoff, MAX_BACKOFF) + random.random()

            logger.info(
                "Повторная попытка через %.1f сек.",
                sleep_for,
            )

            await asyncio.sleep(sleep_for)

            backoff = min(
                backoff * 2,
                MAX_BACKOFF,
            )

    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_polling_with_retries())

    except KeyboardInterrupt:
        logger.info("Бот остановлен.")

    except Exception:
        logger.exception("Критическая ошибка.")