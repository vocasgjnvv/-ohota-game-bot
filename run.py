import asyncio
import logging
import os
import random

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import main


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("ohota_runner")


INITIAL_BACKOFF = float(
    os.getenv("OHOTA_INITIAL_BACKOFF", "2")
)

MAX_BACKOFF = float(
    os.getenv("OHOTA_MAX_BACKOFF", "60")
)


async def run_polling_with_retries():

    if not main.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан в переменных окружения"
        )

    # Создаём необходимые таблицы базы
    main.init_db()

    bot = Bot(
        token=main.BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    backoff = INITIAL_BACKOFF

    try:

        # Удаляем webhook.
        # Он может мешать запуску long polling.
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Webhook удалён. Запускаем polling..."
        )

        while True:

            try:

                logger.info(
                    "ОХОТА: polling запущен"
                )

                await main.dp.start_polling(bot)

                logger.info(
                    "Polling остановлен штатно."
                )

                break

            except asyncio.CancelledError:
                raise

            except TelegramNetworkError as error:

                logger.exception(
                    "Ошибка сети Telegram: %s",
                    error,
                )

            except (ConnectionResetError, OSError) as error:

                logger.exception(
                    "Ошибка соединения: %s",
                    error,
                )

            except Exception as error:

                logger.exception(
                    "Критическая ошибка polling: %s",
                    error,
                )

            sleep_for = (
                min(backoff, MAX_BACKOFF)
                + random.random()
            )

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

        logger.info(
            "Bot session закрыта"
        )


if __name__ == "__main__":

    try:

        asyncio.run(
            run_polling_with_retries()
        )

    except KeyboardInterrupt:

        logger.info(
            "Бот остановлен."
        )

    except Exception:

        logger.exception(
            "Критическая ошибка запуска ОХОТЫ"
        )