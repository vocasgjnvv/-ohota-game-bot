import asyncio
import logging
import os
import random

from aiogram.exceptions import TelegramNetworkError

import main

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ohota_runner")

# Настройки ретраев через переменные окружения
INITIAL_BACKOFF = float(os.getenv("OHOTA_INITIAL_BACKOFF", "1"))
MAX_BACKOFF = float(os.getenv("OHOTA_MAX_BACKOFF", "60"))


async def _close_bot():
    try:
        # aiogram.Bot хранит aiohttp session в .session — корректно закрываем
        if hasattr(main.bot, "session") and main.bot.session:
            await main.bot.session.close()
    except Exception as e:
        logger.warning("Ошибка при закрытии сессии бота: %s", e)


async def run_polling_with_retries():
    # Инициализируем БД и валидацию сюжета (если требуется)
    try:
        main.init_db()
    except Exception:
        logger.exception("init_db() завершилась с ошибкой")

    try:
        main.validate_story()
    except Exception:
        logger.exception("validate_story() завершилась с ошибкой")

    backoff = INITIAL_BACKOFF

    while True:
        try:
            logger.info("Запуск polling (start_polling)")
            # dp.start_polling блокирует до остановки; при сетевых ошибках бросает исключения
            await main.dp.start_polling(main.bot)
            logger.info("Polling остановлен корректно (start_polling вернул/завершился)")
            break
        except asyncio.CancelledError:
            # Позволяем внешнему сигналу завершить программу
            logger.info("Получен CancelledError — завершаем polling")
            raise
        except TelegramNetworkError as e:
            logger.warning("TelegramNetworkError — %s", e)
        except ConnectionResetError as e:
            logger.warning("ConnectionResetError — %s", e)
        except OSError as e:
            logger.warning("OSError при polling — %s", e)
        except Exception as e:
            # Ловим любые другие исключения — логируем и будем пытаться перезапустить
            logger.exception("Необработанная ошибка в polling: %s", e)

        # Ждём с экспоненциальным backoff + джиттер
        sleep_for = min(backoff, MAX_BACKOFF) + random.random()
        logger.info("Переподключение через %.1f секунд (backoff=%.1f) ...", sleep_for, backoff)
        try:
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            raise
        backoff = min(backoff * 2, MAX_BACKOFF)


async def main_async():
    try:
        await run_polling_with_retries()
    finally:
        await _close_bot()


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Остановка по KeyboardInterrupt")
    except Exception:
        logger.exception("Фатальная ошибка в runner")
