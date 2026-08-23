import os
import re
import html
import sqlite3
import asyncio
import logging
from contextlib import closing
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавь BOT_TOKEN в переменные окружения."
    )

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

DB_PATH = os.getenv("DB_PATH", "ohota.db")

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
# DATABASE
# ============================================================

def db_connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with closing(db_connect()) as db:

        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            game_number INTEGER UNIQUE NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            xp INTEGER DEFAULT 0,
            hp INTEGER DEFAULT 100,
            reputation INTEGER DEFAULT 50,
            chapter INTEGER DEFAULT 0,
            investigations INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            interactions INTEGER DEFAULT 0,
            successful_interactions INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            story TEXT NOT NULL,
            image_file_id TEXT,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS clues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            clue_key TEXT NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            reward_xp INTEGER DEFAULT 15,
            UNIQUE(chapter_id, clue_key)
        );

        CREATE TABLE IF NOT EXISTS found_clues (
            telegram_id INTEGER NOT NULL,
            clue_id INTEGER NOT NULL,
            found_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(telegram_id, clue_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            user1 INTEGER NOT NULL,
            user2 INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS room_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            result TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS answers (
            telegram_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            answer_key TEXT NOT NULL,
            correct INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_id, chapter_id)
        );
        """)

        # Default settings
        db.execute("""
            INSERT OR IGNORE INTO settings(key, value)
            VALUES ('beta_active', '1')
        """)

        db.commit()


def get_setting(key, default=None):
    with closing(db_connect()) as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,)
        ).fetchone()

        return row["value"] if row else default


def set_setting(key, value):
    with closing(db_connect()) as db:
        db.execute("""
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, str(value)))
        db.commit()


def beta_active():
    return get_setting("beta_active", "1") == "1"


def get_user(user_id):
    with closing(db_connect()) as db:
        return db.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (user_id,)
        ).fetchone()


def create_user(user_id, nickname):
    with closing(db_connect()) as db:

        last = db.execute(
            "SELECT COALESCE(MAX(game_number), 1000) FROM users"
        ).fetchone()[0]

        game_number = last + 1

        db.execute("""
            INSERT INTO users
            (telegram_id, game_number, nickname)
            VALUES (?, ?, ?)
        """, (
            user_id,
            game_number,
            nickname
        ))

        db.commit()

        return game_number


def update_user(user_id, **fields):
    if not fields:
        return

    allowed = {
        "xp",
        "hp",
        "reputation",
        "chapter",
        "investigations",
        "wins",
        "interactions",
        "successful_interactions",
        "nickname",
    }

    fields = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not fields:
        return

    assignments = ", ".join(
        f"{key}=?" for key in fields
    )

    values = list(fields.values())
    values.append(user_id)

    with closing(db_connect()) as db:
        db.execute(
            f"UPDATE users SET {assignments} WHERE telegram_id=?",
            values
        )
        db.commit()


def change_stats(
    user_id,
    xp=0,
    hp=0,
    reputation=0,
    interactions=0,
    successful_interactions=0
):
    with closing(db_connect()) as db:
        db.execute("""
            UPDATE users
            SET
                xp = MAX(0, xp + ?),
                hp = MAX(0, MIN(100, hp + ?)),
                reputation = MAX(0, MIN(100, reputation + ?)),
                interactions = interactions + ?,
                successful_interactions =
                    successful_interactions + ?
            WHERE telegram_id=?
        """, (
            xp,
            hp,
            reputation,
            interactions,
            successful_interactions,
            user_id
        ))

        db.commit()


# ============================================================
# STORY SEED
# ============================================================

STORY_SEED = [

    {
        "id": 0,
        "title": "ПРОЛОГ — Последний рейс",
        "story": """
<b>ДЕЛО №417</b>

23:47.

Ночной поезд №417 исчез с радаров на участке длиной семь километров.

Через двадцать минут диспетчер получил сообщение с телефона машиниста:

<i>«Не ищите поезд. Ищите пассажира №17.»</i>

Проблема была в другом.

По списку пассажиров места №17 вообще не существовало.

Утром поезд нашли.

Все двери были заперты изнутри.

Машинист исчез.

Пассажиры утверждали, что ничего не произошло.

На стекле последнего вагона обнаружили надпись:

<b>«ОДИН ИЗ ВАС УЖЕ ЗНАЕТ ПРАВДУ.»</b>

Ты открываешь материалы дела.

На первой странице только одна строка:

<b>«Если ты читаешь это — расследование уже началось.»</b>
""",
        "clues": []
    },

    {
        "id": 1,
        "title": "ГЛАВА 1 — Вагон №7",
        "story": """
<b>ГЛАВА 1 — ВАГОН №7</b>

Ты входишь в последний вагон.

Внутри пахнет мокрым металлом и дешёвым табаком.

Девятнадцать пассажиров.

Но в документах указано двадцать.

На столике лежат часы.

Они остановились ровно в <b>23:51</b>.

Рядом лежит билет:

<b>0417-17</b>.

На обратной стороне:

<i>«Не верь тому, кто первым скажет, что ничего не видел.»</i>

Ты начинаешь осмотр.
""",
        "clues": [
            {
                "key": "clock",
                "title": "⌚ Осмотреть часы",
                "text": """
Часы исправны.

Кто-то остановил механизм вручную.

На задней крышке царапина в форме буквы:

<b>R</b>.

Это не случайная царапина.

Кто-то хотел оставить знак.
""",
                "reward": 15
            },
            {
                "key": "ticket",
                "title": "🎫 Проверить билет",
                "text": """
Билет настоящий.

Но номер <b>0417-17</b> отсутствует в системе.

Страннее другое.

Билет был напечатан уже после отправления поезда.

Получается, кто-то печатал билет, когда поезд уже ехал.
""",
                "reward": 20
            },
            {
                "key": "window",
                "title": "🪟 Осмотреть окно",
                "text": """
На стекле следы пальцев.

Под ними едва заметная надпись:

<b>R-17</b>.

Ты фотографируешь улику.

Теперь у тебя есть первый настоящий след.
""",
                "reward": 20
            }
        ]
    },

    {
        "id": 2,
        "title": "ГЛАВА 2 — Пассажир №17",
        "story": """
<b>ГЛАВА 2 — ПАССАЖИР №17</b>

Камеры показывают человека в капюшоне.

Он сидит на месте №17.

Но никто не видел, как он вошёл.

Ты проверяешь записи ещё раз.

В 23:39 вагон пуст.

В 23:41 появляется пассажир.

В 23:42 камера на несколько секунд теряет сигнал.

После этого пассажир уже сидит на месте №17.

Кто-то вмешался в систему наблюдения.
""",
        "clues": [
            {
                "key": "camera",
                "title": "📹 Изучить камеры",
                "text": """
Пассажир не входил через дверь.

На записи отсутствуют четыре секунды.

Именно четыре секунды были вырезаны вручную.

Это была не поломка.
""",
                "reward": 20
            },
            {
                "key": "phone",
                "title": "📱 Проверить телефон №16",
                "text": """
Пассажир №16 утверждает, что спал.

Но в 23:49 он отправил сообщение:

<b>«Он здесь.»</b>

Получатель удалён.
""",
                "reward": 25
            },
            {
                "key": "watch",
                "title": "⌚ Сравнить часы",
                "text": """
На часах неизвестного пассажира такая же гравировка:

<b>R-17</b>.

Теперь связь с машинистом выглядит очевидной.
""",
                "reward": 25
            }
        ]
    },

    {
        "id": 3,
        "title": "ГЛАВА 3 — Четыре секунды",
        "story": """
<b>ГЛАВА 3 — ЧЕТЫРЕ СЕКУНДЫ</b>

Ты восстанавливаешь повреждённую запись.

На четвёртой секунде появляется рука.

Человек передаёт кому-то маленький металлический контейнер.

Контейнер исчезает из кадра.

Через несколько секунд поезд теряет связь.

У тебя появляется первая версия:

кто-то передал устройство, которое отключило поезд от системы.
""",
        "clues": [
            {
                "key": "container",
                "title": "📦 Исследовать контейнер",
                "text": """
По форме контейнер похож на старый блок доступа.

Такие использовались на железной дороге двадцать лет назад.

На нём:

<b>R-17 / ARCHIVE</b>.
""",
                "reward": 25
            },
            {
                "key": "archive",
                "title": "🗄 Проверить архив",
                "text": """
В архиве найдено старое дело №417.

Дата:

<b>17 лет назад.</b>

Оно закрыто без объяснения причин.
""",
                "reward": 25
            },
            {
                "key": "signature",
                "title": "✍️ Сравнить подписи",
                "text": """
Подпись в старом деле совпадает
с подписью нынешнего начальника станции.

Но начальник утверждает, что впервые видит материалы.
""",
                "reward": 30
            }
        ]
    },

    {
        "id": 4,
        "title": "ГЛАВА 4 — Архив",
        "story": """
<b>ГЛАВА 4 — АРХИВ</b>

Ты находишь старую фотографию.

На ней двадцать человек.

Девятнадцать пассажиров.

И один следователь.

Следователь на фотографии — человек,
который исчез три месяца назад.

На обороте:

<b>«Следующее расследование проведёт он.»</b>

Ты переворачиваешь фотографию.

На второй стороне написано твоё имя.
""",
        "clues": [
            {
                "key": "photo",
                "title": "📸 Изучить фотографию",
                "text": """
Фотография сделана три месяца назад.

Но ты никогда не был на этой станции.

Кто-то заранее знал, что ты окажешься здесь.
""",
                "reward": 30
            },
            {
                "key": "file",
                "title": "📁 Открыть дело №417",
                "text": """
Дело состоит из двадцати разделов.

Девятнадцать уже закрыты.

Двадцатый подписан:

<b>«ОТКРЫТЬ ПОСЛЕ ПОЯВЛЕНИЯ НАБЛЮДАТЕЛЯ.»</b>
""",
                "reward": 30
            }
        ]
    },

    {
        "id": 5,
        "title": "ГЛАВА 5 — Наблюдатель",
        "story": """
<b>ГЛАВА 5 — НАБЛЮДАТЕЛЬ</b>

Ты понимаешь:

пассажиры не случайные люди.

Каждый из них связан с делом №417.

Но никто не знает всей картины.

Каждому дали только один фрагмент.

И теперь фрагменты находятся у двадцати разных людей.
""",
        "clues": [
            {
                "key": "list",
                "title": "📋 Проверить список",
                "text": """
У каждого пассажира свой код.

01 — свидетель.

07 — архив.

11 — деньги.

14 — доступ.

17 — неизвестен.

20 — наблюдатель.

Последняя строка принадлежит тебе.
""",
                "reward": 30
            },
            {
                "key": "mark",
                "title": "🔐 Проверить свой код",
                "text": """
Твой код:

<b>20</b>.

Рядом написано:

<i>«Наблюдатель становится участником,
когда делает первый выбор.»</i>
""",
                "reward": 35
            }
        ]
    },

    {
        "id": 6,
        "title": "ГЛАВА 6 — Свидетель",
        "story": """
<b>ГЛАВА 6 — СВИДЕТЕЛЬ</b>

Один пассажир просит поговорить с тобой.

Он говорит, что видел машиниста.

Но его история противоречит камерам.

Он либо врёт,

либо камеры подделаны.
""",
        "clues": [
            {
                "key": "witness",
                "title": "👁 Допросить свидетеля",
                "text": """
Свидетель утверждает:

машинист вышел из кабины самостоятельно.

Но на полу кабины найдена кровь.

Следов борьбы нет.
""",
                "reward": 35
            },
            {
                "key": "blood",
                "title": "🩸 Проверить кровь",
                "text": """
Кровь принадлежит не машинисту.

Она принадлежит пассажиру №17.
""",
                "reward": 40
            }
        ]
    },

    {
        "id": 7,
        "title": "ГЛАВА 7 — Сбой",
        "story": """
<b>ГЛАВА 7 — СБОЙ</b>

В 00:13 поезд снова исчезает с системы.

На этот раз всего на семь секунд.

Кто-то внутри всё ещё имеет доступ.

Ты находишь терминал.

На экране один активный пользователь:

<b>R17</b>.
""",
        "clues": [
            {
                "key": "terminal",
                "title": "💻 Проверить терминал",
                "text": """
Последний вход выполнен не снаружи.

Он выполнен из вагона №7.
""",
                "reward": 35
            },
            {
                "key": "login",
                "title": "🔑 Проверить время входа",
                "text": """
Вход выполнен за 30 секунд
до исчезновения поезда.

Пользователь находился рядом с тобой.
""",
                "reward": 40
            }
        ]
    },

    {
        "id": 8,
        "title": "ГЛАВА 8 — Ложный след",
        "story": """
<b>ГЛАВА 8 — ЛОЖНЫЙ СЛЕД</b>

Все улики указывают на пассажира №16.

Слишком идеально.

Настолько идеально, что это начинает выглядеть подозрительно.

Кто-то хочет, чтобы ты обвинил именно его.
""",
        "clues": [
            {
                "key": "false",
                "title": "🧩 Проверить ложную улику",
                "text": """
Отпечаток на контейнере принадлежит №16.

Но контейнер был найден после того,
как он покинул вагон.

Кто-то подложил отпечаток.
""",
                "reward": 40
            }
        ]
    },

    {
        "id": 9,
        "title": "ГЛАВА 9 — Двадцатый",
        "story": """
<b>ГЛАВА 9 — ДВАДЦАТЫЙ</b>

Ты снова пересчитываешь пассажиров.

И понимаешь:

их действительно двадцать.

Но один из них не существует в официальной системе.

Это и есть пассажир №17.
""",
        "clues": [
            {
                "key": "identity",
                "title": "🪪 Установить личность №17",
                "text": """
Документов нет.

Имя отсутствует.

Но система распознавания лица выдаёт:

<b>НЕИЗВЕСТНО</b>.

Затем через секунду меняет результат:

<b>СОВПАДЕНИЕ: 98%</b>.

С кем?

С тобой.
""",
                "reward": 50
            }
        ]
    },

    {
        "id": 10,
        "title": "ГЛАВА 10 — Двойник",
        "story": """
<b>ГЛАВА 10 — ДВОЙНИК</b>

Ты смотришь запись снова.

Пассажир №17 похож на тебя.

Но это невозможно.

Или возможно?

В архиве появляется фотография.

На ней ты.

Рядом человек, которого ты никогда не встречал.
""",
        "clues": [
            {
                "key": "double",
                "title": "🪞 Сравнить лица",
                "text": """
Система считает совпадение почти идеальным.

Разница только в одном:

шраме на лице.

У человека на фотографии его нет.
""",
                "reward": 50
            }
        ]
    },

    {
        "id": 11,
        "title": "ГЛАВА 11 — Человек без имени",
        "story": """
<b>ГЛАВА 11 — ЧЕЛОВЕК БЕЗ ИМЕНИ</b>

Ты находишь запись разговора.

Голос говорит:

<i>«Он должен вспомнить сам.»</i>

Второй голос:

<i>«А если не вспомнит?»</i>

Ответ:

<i>«Тогда поезд уйдёт снова.»</i>
""",
        "clues": [
            {
                "key": "voice",
                "title": "🎙 Анализировать голос",
                "text": """
Один голос принадлежит начальнику станции.

Второй невозможно идентифицировать.

Но спектр голоса совпадает с твоим на 91%.
""",
                "reward": 50
            }
        ]
    },

    {
        "id": 12,
        "title": "ГЛАВА 12 — Второй поезд",
        "story": """
<b>ГЛАВА 12 — ВТОРОЙ ПОЕЗД</b>

В архиве обнаруживается второй поезд №417.

Он существовал семнадцать лет назад.

Его пассажиры исчезли.

Официальная версия — авария.

Но аварии не было.
""",
        "clues": [
            {
                "key": "oldtrain",
                "title": "🚆 Проверить старый поезд",
                "text": """
Поезд остановился на том же километре.

В ту же минуту.

23:47.
""",
                "reward": 55
            }
        ]
    },

    {
        "id": 13,
        "title": "ГЛАВА 13 — Повторение",
        "story": """
<b>ГЛАВА 13 — ПОВТОРЕНИЕ</b>

История повторяется.

Те же часы.

Тот же вагон.

Тот же номер.

Только теперь ты знаешь, что произойдёт.

И у тебя остаётся меньше часа.
""",
        "clues": [
            {
                "key": "cycle",
                "title": "⏱ Сопоставить события",
                "text": """
События происходят с точностью до минуты.

Кто-то заранее написал сценарий.
""",
                "reward": 55
            }
        ]
    },

    {
        "id": 14,
        "title": "ГЛАВА 14 — Предательство",
        "story": """
<b>ГЛАВА 14 — ПРЕДАТЕЛЬСТВО</b>

Игрок, которому ты доверял, передаёт тебе улику.

Она настоящая.

Но часть информации удалена.

Кто-то не хочет, чтобы ты узнал имя.
""",
        "clues": [
            {
                "key": "betrayal",
                "title": "🤝 Проверить союзника",
                "text": """
Твой союзник получил приказ:

не говорить тебе имя пассажира №17.

Но он нарушил приказ.

Теперь он тоже становится целью.
""",
                "reward": 60
            }
        ]
    },

    {
        "id": 15,
        "title": "ГЛАВА 15 — Имя",
        "story": """
<b>ГЛАВА 15 — ИМЯ</b>

Ты наконец находишь имя.

Но это имя принадлежит тебе.

В старом деле ты указан как участник.

Дата рождения совпадает.

Документы настоящие.

Ты ничего не помнишь.
""",
        "clues": [
            {
                "key": "name",
                "title": "📄 Проверить документы",
                "text": """
Все документы настоящие.

Но один файл изменён вчера.

Кто-то следит за расследованием прямо сейчас.
""",
                "reward": 60
            }
        ]
    },

    {
        "id": 16,
        "title": "ГЛАВА 16 — Память",
        "story": """
<b>ГЛАВА 16 — ПАМЯТЬ</b>

Ты вспоминаешь фрагмент.

Ты уже был здесь.

Три месяца назад.

И ты сам оставил запись:

<i>«Если я забуду — не доверяй мне.»</i>
""",
        "clues": [
            {
                "key": "memory",
                "title": "🧠 Восстановить память",
                "text": """
Ты вспомнил человека.

Пассажир №17 не враг.

Он пытался остановить эксперимент.
""",
                "reward": 65
            }
        ]
    },

    {
        "id": 17,
        "title": "ГЛАВА 17 — Эксперимент",
        "story": """
<b>ГЛАВА 17 — ЭКСПЕРИМЕНТ</b>

Поезд был не транспортом.

Он был экспериментом.

Двадцать человек.

Двадцать ролей.

Один наблюдатель.

И один человек, который должен был сломать систему.
""",
        "clues": [
            {
                "key": "experiment",
                "title": "🧪 Найти цель эксперимента",
                "text": """
Цель эксперимента:

выяснить, насколько далеко человек готов зайти,
если ему дать неполную правду.
""",
                "reward": 70
            }
        ]
    },

    {
        "id": 18,
        "title": "ГЛАВА 18 — Выбор",
        "story": """
<b>ГЛАВА 18 — ВЫБОР</b>

У тебя два варианта.

Открыть дверь и выпустить пассажиров.

Или продолжить расследование,
оставив их внутри.

Любой выбор имеет последствия.
""",
        "clues": [
            {
                "key": "choice",
                "title": "🚪 Проверить дверь",
                "text": """
На двери код.

Он состоит из всех найденных тобой номеров.

Но одного номера не хватает:

<b>17</b>.

Значит, последний код должен дать пассажир №17.
""",
                "reward": 75
            }
        ]
    },

    {
        "id": 19,
        "title": "ФИНАЛ — Продолжение следует",
        "story": """
<b>ФИНАЛ ПЕРВОЙ ЧАСТИ</b>

Ты вводишь код.

Дверь открывается.

Но за дверью нет станции.

Там коридор.

Длинный.

Тёмный.

На стене двадцать фотографий.

Девятнадцать пассажиров.

И одна твоя.

Под фотографией надпись:

<b>«НАБЛЮДАТЕЛЬ ЗАВЕРШИЛ ТЕСТ.»</b>

Ты переворачиваешь фотографию.

На обратной стороне:

<i>«Теперь начинается настоящий эксперимент.»</i>

Телефон вибрирует.

Новое сообщение.

Отправитель:

<b>ПАССАЖИР №17</b>

<i>«Ты наконец вспомнил меня.»</i>

Пауза.

<i>«Теперь вспомни, кем был ты.»</i>

━━━━━━━━━━━━━━

<b>ПРОДОЛЖЕНИЕ СЛЕДУЕТ...</b>
""",
        "clues": []
    }
]


def seed_story():
    with closing(db_connect()) as db:

        count = db.execute(
            "SELECT COUNT(*) FROM chapters"
        ).fetchone()[0]

        if count > 0:
            return

        for chapter in STORY_SEED:

            db.execute("""
                INSERT INTO chapters
                (id, title, story, image_file_id, active)
                VALUES (?, ?, ?, NULL, 1)
            """, (
                chapter["id"],
                chapter["title"],
                chapter["story"]
            ))

            for clue in chapter.get("clues", []):

                db.execute("""
                    INSERT INTO clues
                    (chapter_id, clue_key, title, text, reward_xp)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    chapter["id"],
                    clue["key"],
                    clue["title"],
                    clue["text"],
                    clue.get("reward", 15)
                ))

        db.commit()


def get_chapter(chapter_id):
    with closing(db_connect()) as db:
        return db.execute(
            "SELECT * FROM chapters WHERE id=?",
            (chapter_id,)
        ).fetchone()


def get_clues(chapter_id):
    with closing(db_connect()) as db:
        return db.execute("""
            SELECT *
            FROM clues
            WHERE chapter_id=?
            ORDER BY id
        """, (chapter_id,)).fetchall()


def get_clue(clue_id):
    with closing(db_connect()) as db:
        return db.execute(
            "SELECT * FROM clues WHERE id=?",
            (clue_id,)
        ).fetchone()


def has_found_clue(user_id, clue_id):
    with closing(db_connect()) as db:
        row = db.execute("""
            SELECT 1
            FROM found_clues
            WHERE telegram_id=? AND clue_id=?
        """, (
            user_id,
            clue_id
        )).fetchone()

        return bool(row)


def mark_clue_found(user_id, clue_id):
    with closing(db_connect()) as db:
        try:
            db.execute("""
                INSERT INTO found_clues
                (telegram_id, clue_id)
                VALUES (?, ?)
            """, (
                user_id,
                clue_id
            ))

            db.commit()
            return True

        except sqlite3.IntegrityError:
            return False


def found_clue_count(user_id):
    with closing(db_connect()) as db:
        return db.execute("""
            SELECT COUNT(*)
            FROM found_clues
            WHERE telegram_id=?
        """, (user_id,)).fetchone()[0]


# ============================================================
# FSM
# ============================================================

class RegistrationState(StatesGroup):
    nickname = State()


class AdminState(StatesGroup):
    edit_title = State()
    edit_story = State()
    create_title = State()
    create_story = State()
    add_clue_title = State()
    add_clue_text = State()
    image = State()


# ============================================================
# HELPERS
# ============================================================

def admin_only(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def safe(text):
    return html.escape(str(text))


def main_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🔎 НАЧАТЬ ОХОТУ",
        callback_data="game"
    )

    kb.button(
        text="👤 Моё досье",
        callback_data="profile"
    )

    kb.button(
        text="🏆 Рейтинг",
        callback_data="rating"
    )

    kb.button(
        text="📜 Как играть",
        callback_data="rules"
    )

    kb.button(
        text="💬 Поддержка",
        callback_data="support"
    )

    kb.adjust(1)

    return kb.as_markup()


def back_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="⬅️ Назад",
        callback_data="menu"
    )

    return kb.as_markup()


def admin_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🎯 Управление охотой",
        callback_data="admin_hunt"
    )

    kb.button(
        text="📖 История",
        callback_data="admin_story"
    )

    kb.button(
        text="👥 Игроки",
        callback_data="admin_users"
    )

    kb.button(
        text="📊 Статистика",
        callback_data="admin_stats"
    )

    kb.button(
        text="⚙️ Настройки",
        callback_data="admin_settings"
    )

    kb.button(
        text="⬅️ Закрыть",
        callback_data="menu"
    )

    kb.adjust(1)

    return kb.as_markup()


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext):

    await state.clear()

    user = get_user(message.from_user.id)

    if user:
        await message.answer(
            f"""
<b>🕵️ ОХОТА</b>

С возвращением, <b>{safe(user['nickname'])}</b>.

Дело №417 всё ещё открыто.

📖 Глава: <b>{user['chapter'] + 1}/20</b>
⭐ XP: <b>{user['xp']}</b>
❤️ HP: <b>{user['hp']}</b>
🤝 Репутация: <b>{user['reputation']}</b>

<b>Продолжим?</b>
""",
            reply_markup=main_keyboard()
        )

        return

    await message.answer(
        """
<b>🕵️ ОХОТА</b>

<i>Онлайн-детективная игра.</i>

Здесь нет готовых ответов.

Ты собираешь улики, принимаешь решения,
анализируешь людей и пытаешься понять,
кто говорит правду.

<b>Дело №417 уже ждёт тебя.</b>
""",
        reply_markup=main_keyboard()
    )


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery, state: FSMContext):

    await state.clear()

    await callback.message.edit_text(
        """
<b>🕵️ ОХОТА</b>

<b>Дело №417 всё ещё открыто.</b>

Выбери действие.
""",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# GAME START
# ============================================================

@dp.callback_query(F.data == "game")
async def game_start(callback: CallbackQuery, state: FSMContext):

    user = get_user(callback.from_user.id)

    if not user:

        await state.set_state(
            RegistrationState.nickname
        )

        await callback.message.edit_text(
            """
<b>🕵️ ДОПУСК К РАССЛЕДОВАНИЮ</b>

Перед началом придумай себе
игровой псевдоним.

<b>3–20 символов.</b>

Можно использовать:
буквы, цифры, пробел, <code>_</code> и <code>-</code>.

Напиши псевдоним следующим сообщением.
""",
            reply_markup=None
        )

        await callback.answer()
        return

    if not beta_active() and not admin_only(callback.from_user.id):

        await callback.message.edit_text(
            """
<b>⏳ БЕТА ПОКА ЗАКРЫТА</b>

Сейчас проводится техническая подготовка.

Следи за обновлениями.
""",
            reply_markup=back_keyboard()
        )

        await callback.answer()
        return

    await show_current_chapter(
        callback.message,
        callback.from_user.id
    )

    await callback.answer()


# ============================================================
# REGISTRATION
# ============================================================

@dp.message(RegistrationState.nickname)
async def registration(message: Message, state: FSMContext):

    nickname = message.text.strip()

    if not re.fullmatch(
        r"[A-Za-zА-Яа-яЁё0-9 _-]{3,20}",
        nickname
    ):
        await message.answer(
            "❌ Псевдоним должен быть от 3 до 20 символов.\n\n"
            "Попробуй ещё раз."
        )
        return

    bad_words = (
        "хуй",
        "пизд",
        "еба",
        "бляд",
        "бля",
        "сука",
        "дебил",
        "мудак",
    )

    normalized = nickname.lower().replace("ё", "е")

    if any(word in normalized for word in bad_words):

        await message.answer(
            "❌ Такой псевдоним использовать нельзя.\n\n"
            "Придумай другой."
        )

        return

    try:

        number = create_user(
            message.from_user.id,
            nickname
        )

    except sqlite3.IntegrityError:

        await message.answer(
            "❌ Этот псевдоним уже занят.\n\n"
            "Придумай другой."
        )

        return

    await state.clear()

    await message.answer(
        f"""
<b>🟢 ДОПУСК ПОЛУЧЕН</b>

🎫 Игровой номер: <b>#{number}</b>
👤 Псевдоним: <b>{safe(nickname)}</b>

Теперь назад дороги нет.

<b>Дело №417 начинается.</b>
""",
        reply_markup=InlineKeyboardBuilder()
        .button(
            text="🔎 ОТКРЫТЬ ДЕЛО",
            callback_data="game"
        )
        .as_markup()
    )


# ============================================================
# SHOW CHAPTER
# ============================================================

async def show_current_chapter(message, user_id):

    user = get_user(user_id)

    if not user:
        return

    chapter_id = user["chapter"]

    chapter = get_chapter(chapter_id)

    if not chapter:
        return

    text = chapter["story"]

    kb = InlineKeyboardBuilder()

    if chapter["image_file_id"]:
        try:
            await message.answer_photo(
                chapter["image_file_id"],
                caption=text
            )
        except Exception:
            await message.answer(text)

    else:
        await message.answer(text)

    clues = get_clues(chapter_id)

    if clues:

        for clue in clues:

            already = has_found_clue(
                user_id,
                clue["id"]
            )

            prefix = "✅" if already else "🔎"

            kb.button(
                text=f"{prefix} {clue['title']}",
                callback_data=f"clue:{clue['id']}"
            )

        kb.button(
            text="▶️ Завершить этап",
            callback_data=f"finish:{chapter_id}"
        )

    elif chapter_id < 19:

        kb.button(
            text="▶️ Продолжить",
            callback_data=f"finish:{chapter_id}"
        )

    else:

        kb.button(
            text="🏠 В главное меню",
            callback_data="menu"
        )

    kb.adjust(1)

    await message.answer(
        "<b>Твои действия:</b>",
        reply_markup=kb.as_markup()
    )


# ============================================================
# CLUE
# ============================================================

@dp.callback_query(F.data.startswith("clue:"))
async def clue(callback: CallbackQuery):

    clue_id = int(
        callback.data.split(":")[1]
    )

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала нажми НАЧАТЬ ОХОТУ.",
            show_alert=True
        )
        return

    clue_data = get_clue(clue_id)

    if not clue_data:
        await callback.answer(
            "Улика не найдена.",
            show_alert=True
        )
        return

    first = mark_clue_found(
        callback.from_user.id,
        clue_id
    )

    reward = clue_data["reward_xp"]

    if first:
        change_stats(
            callback.from_user.id,
            xp=reward
        )

        reward_text = f"\n\n⭐ <b>+{reward} XP</b>"
    else:
        reward_text = "\n\n<i>Ты уже изучал эту улику.</i>"

    kb = InlineKeyboardBuilder()

    kb.button(
        text="⬅️ Вернуться к расследованию",
        callback_data="game"
    )

    await callback.message.edit_text(
        f"""
<b>{clue_data['title']}</b>

{clue_data['text']}
{reward_text}
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# FINISH CHAPTER
# ============================================================

@dp.callback_query(F.data.startswith("finish:"))
async def finish_chapter(callback: CallbackQuery):

    chapter_id = int(
        callback.data.split(":")[1]
    )

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала зарегистрируйся.",
            show_alert=True
        )
        return

    if user["chapter"] != chapter_id:
        await callback.answer(
            "Этот этап уже завершён или ещё недоступен.",
            show_alert=True
        )
        return

    if chapter_id >= 19:

        update_user(
            callback.from_user.id,
            chapter=19,
            investigations=user["investigations"] + 1,
            wins=user["wins"] + 1,
            xp=user["xp"] + 100
        )

        await callback.message.edit_text(
            """
<b>🏆 ПЕРВАЯ ЧАСТЬ ЗАВЕРШЕНА</b>

Ты прошёл дело №417.

⭐ <b>+100 XP</b>

Но теперь начинается самое интересное.

Ты узнал правду о поезде.

Но не узнал правду о себе.

━━━━━━━━━━━━━━

<b>ПРОДОЛЖЕНИЕ СЛЕДУЕТ...</b>
""",
            reply_markup=back_keyboard()
        )

        await callback.answer(
            "Расследование завершено."
        )

        return

    next_chapter = chapter_id + 1

    update_user(
        callback.from_user.id,
        chapter=next_chapter,
        xp=user["xp"] + 35
    )

    chapter = get_chapter(next_chapter)

    kb = InlineKeyboardBuilder()

    kb.button(
        text=f"▶️ {chapter['title']}",
        callback_data="game"
    )

    await callback.message.edit_text(
        f"""
<b>ЭТАП ЗАВЕРШЁН</b>

⭐ <b>+35 XP</b>

Новая информация открыта.

<b>{chapter['title']}</b>
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):

    user = get_user(callback.from_user.id)

    if not user:

        await callback.message.edit_text(
            """
<b>👤 МОЁ ДОСЬЕ</b>

Ты ещё не зарегистрирован.

Нажми «НАЧАТЬ ОХОТУ».
""",
            reply_markup=main_keyboard()
        )

        await callback.answer()
        return

    clues = found_clue_count(
        callback.from_user.id
    )

    await callback.message.edit_text(
        f"""
<b>👤 МОЁ ДОСЬЕ</b>

🎫 №<b>{user['game_number']}</b>
🕵️ <b>{safe(user['nickname'])}</b>

━━━━━━━━━━━━━━

📖 Этап: <b>{user['chapter'] + 1}/20</b>

⭐ XP: <b>{user['xp']}</b>
❤️ HP: <b>{user['hp']}/100</b>
🤝 Репутация: <b>{user['reputation']}/100</b>

🔎 Найдено улик: <b>{clues}</b>

🏆 Побед: <b>{user['wins']}</b>
🎯 Расследований: <b>{user['investigations']}</b>

🤝 Взаимодействий: <b>{user['interactions']}</b>
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# RATING
# ============================================================

@dp.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery):

    with closing(db_connect()) as db:

        rows = db.execute("""
            SELECT nickname, xp, reputation
            FROM users
            ORDER BY xp DESC, reputation DESC
            LIMIT 20
        """).fetchall()

    if not rows:

        text = """
<b>🏆 РЕЙТИНГ</b>

Пока игроков нет.
"""

    else:

        lines = []

        for index, row in enumerate(rows, 1):

            lines.append(
                f"<b>{index}.</b> "
                f"{safe(row['nickname'])} "
                f"— ⭐ {row['xp']}"
            )

        text = (
            "<b>🏆 РЕЙТИНГ ОХОТНИКОВ</b>\n\n"
            + "\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# RULES
# ============================================================

@dp.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery):

    await callback.message.edit_text(
        """
<b>📜 КАК ИГРАТЬ</b>

🔎 Исследуй дело.

🧩 Собирай улики.

🧠 Сопоставляй информацию.

❤️ Ошибки могут стоить HP.

⭐ За найденные улики получаешь XP.

🤝 Позже можно будет взаимодействовать
с другими охотниками.

🎭 Можно доверять.

🎭 Можно обманывать.

🏆 Репутация показывает, насколько
тебе доверяют другие игроки.

<b>Главное:</b>

Не всякая улика говорит правду.

И не каждый человек говорит правду.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):

    await callback.message.edit_text(
        """
<b>💬 ПОДДЕРЖКА</b>

Нашёл ошибку?

Напиши администратору проекта.

Если ты проходишь бета-тест,
обязательно укажи:

• номер этапа;
• что нажал;
• что произошло;
• что ожидал увидеть.

Так мы быстрее исправим проблему.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):

    if not admin_only(message.from_user.id):

        await message.answer(
            "⛔ Доступ запрещён."
        )

        return

    await message.answer(
        """
<b>🔐 МОЁ ПРОСТРАНСТВО</b>

Только для администратора.

Здесь ты управляешь всей игрой.
""",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN HUNT
# ============================================================

@dp.callback_query(F.data == "admin_hunt")
async def admin_hunt(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    status = "🟢 ЗАПУЩЕНА" if beta_active() else "🔴 ОСТАНОВЛЕНА"

    kb = InlineKeyboardBuilder()

    kb.button(
        text="▶️ Запустить бета-тест",
        callback_data="beta_on"
    )

    kb.button(
        text="⏹ Остановить",
        callback_data="beta_off"
    )

    kb.button(
        text="📖 Управление главами",
        callback_data="admin_story"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="admin"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        f"""
<b>🎯 УПРАВЛЕНИЕ ОХОТОЙ</b>

Статус бета-теста:

<b>{status}</b>

Здесь ты можешь открыть или закрыть
игру для обычных игроков.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data == "beta_on")
async def beta_on(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    set_setting("beta_active", "1")

    await callback.answer(
        "🟢 Бета-тест запущен.",
        show_alert=True
    )


@dp.callback_query(F.data == "beta_off")
async def beta_off(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    set_setting("beta_active", "0")

    await callback.answer(
        "🔴 Бета-тест остановлен.",
        show_alert=True
    )


# ============================================================
# ADMIN STORY
# ============================================================

@dp.callback_query(F.data == "admin_story")
async def admin_story(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    with closing(db_connect()) as db:

        chapters = db.execute("""
            SELECT id, title
            FROM chapters
            ORDER BY id
        """).fetchall()

    kb = InlineKeyboardBuilder()

    for chapter in chapters:

        kb.button(
            text=f"📖 {chapter['id'] + 1}. {chapter['title']}",
            callback_data=f"admin_chapter:{chapter['id']}"
        )

    kb.button(
        text="➕ Создать главу",
        callback_data="admin_create"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="admin"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        """
<b>📖 РЕДАКТОР ИСТОРИИ</b>

Выбери главу.

Здесь можно:

✏️ изменить текст;
🖼 добавить изображение;
🔎 добавить улику;
➕ создать новую главу.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# ADMIN CHAPTER
# ============================================================

@dp.callback_query(F.data.startswith("admin_chapter:"))
async def admin_chapter(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    chapter_id = int(
        callback.data.split(":")[1]
    )

    chapter = get_chapter(chapter_id)
    clues = get_clues(chapter_id)

    kb = InlineKeyboardBuilder()

    kb.button(
        text="✏️ Изменить название",
        callback_data=f"edit_title:{chapter_id}"
    )

    kb.button(
        text="📝 Изменить сюжет",
        callback_data=f"edit_story:{chapter_id}"
    )

    kb.button(
        text="🖼 Добавить картинку",
        callback_data=f"add_image:{chapter_id}"
    )

    kb.button(
        text="🔎 Добавить улику",
        callback_data=f"add_clue:{chapter_id}"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="admin_story"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        f"""
<b>📖 {chapter['title']}</b>

Улик: <b>{len(clues)}</b>

Изображение:
<b>{"есть" if chapter["image_file_id"] else "нет"}</b>

━━━━━━━━━━━━━━

{chapter["story"][:700]}
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# EDIT TITLE
# ============================================================

@dp.callback_query(F.data.startswith("edit_title:"))
async def edit_title_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not admin_only(callback.from_user.id):
        return

    chapter_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        chapter_id=chapter_id
    )

    await state.set_state(
        AdminState.edit_title
    )

    await callback.message.answer(
        "✏️ Напиши новое название главы."
    )

    await callback.answer()


@dp.message(AdminState.edit_title)
async def edit_title_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()
    chapter_id = data["chapter_id"]

    with closing(db_connect()) as db:

        db.execute(
            "UPDATE chapters SET title=? WHERE id=?",
            (
                message.text.strip(),
                chapter_id
            )
        )

        db.commit()

    await state.clear()

    await message.answer(
        "✅ Название главы изменено."
    )

    await message.answer(
        "🔐 Моё пространство",
        reply_markup=admin_keyboard()
    )


# ============================================================
# EDIT STORY
# ============================================================

@dp.callback_query(F.data.startswith("edit_story:"))
async def edit_story_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not admin_only(callback.from_user.id):
        return

    chapter_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        chapter_id=chapter_id
    )

    await state.set_state(
        AdminState.edit_story
    )

    await callback.message.answer(
        """
📝 <b>Изменение сюжета</b>

Отправь новый текст главы.

HTML-разметка Telegram разрешена.
"""
    )

    await callback.answer()


@dp.message(AdminState.edit_story)
async def edit_story_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()

    with closing(db_connect()) as db:

        db.execute(
            "UPDATE chapters SET story=? WHERE id=?",
            (
                message.text,
                data["chapter_id"]
            )
        )

        db.commit()

    await state.clear()

    await message.answer(
        "✅ Сюжет главы сохранён.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADD IMAGE
# ============================================================

@dp.callback_query(F.data.startswith("add_image:"))
async def add_image_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not admin_only(callback.from_user.id):
        return

    chapter_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        chapter_id=chapter_id
    )

    await state.set_state(
        AdminState.image
    )

    await callback.message.answer(
        """
🖼 <b>Добавление изображения</b>

Отправь фотографию следующим сообщением.

Она будет показываться сверху текста выбранной главы.
"""
    )

    await callback.answer()


@dp.message(AdminState.image, F.photo)
async def add_image_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()

    file_id = message.photo[-1].file_id

    with closing(db_connect()) as db:

        db.execute(
            """
            UPDATE chapters
            SET image_file_id=?
            WHERE id=?
            """,
            (
                file_id,
                data["chapter_id"]
            )
        )

        db.commit()

    await state.clear()

    await message.answer(
        "✅ Изображение главы сохранено.",
        reply_markup=admin_keyboard()
    )


@dp.message(AdminState.image)
async def image_wrong(message: Message):

    await message.answer(
        "❌ Нужно отправить именно фотографию."
    )


# ============================================================
# CREATE CHAPTER
# ============================================================

@dp.callback_query(F.data == "admin_create")
async def create_chapter_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not admin_only(callback.from_user.id):
        return

    await state.set_state(
        AdminState.create_title
    )

    await callback.message.answer(
        """
➕ <b>Создание новой главы</b>

Сначала напиши название.
"""
    )

    await callback.answer()


@dp.message(AdminState.create_title)
async def create_title_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    await state.update_data(
        title=message.text.strip()
    )

    await state.set_state(
        AdminState.create_story
    )

    await message.answer(
        """
📝 Теперь отправь текст новой главы.
"""
    )


@dp.message(AdminState.create_story)
async def create_story_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()

    with closing(db_connect()) as db:

        max_id = db.execute(
            "SELECT COALESCE(MAX(id), -1) FROM chapters"
        ).fetchone()[0]

        new_id = max_id + 1

        db.execute("""
            INSERT INTO chapters
            (id, title, story, active)
            VALUES (?, ?, ?, 1)
        """, (
            new_id,
            data["title"],
            message.text
        ))

        db.commit()

    await state.clear()

    await message.answer(
        f"""
✅ <b>Глава создана.</b>

Номер: <b>{new_id + 1}</b>

Теперь её можно открыть в редакторе
и добавить улики/картинку.
""",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADD CLUE
# ============================================================

@dp.callback_query(F.data.startswith("add_clue:"))
async def add_clue_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not admin_only(callback.from_user.id):
        return

    chapter_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        chapter_id=chapter_id
    )

    await state.set_state(
        AdminState.add_clue_title
    )

    await callback.message.answer(
        """
🔎 <b>Новая улика</b>

Напиши название.

Например:

🎫 Проверить билет
"""
    )

    await callback.answer()


@dp.message(AdminState.add_clue_title)
async def add_clue_title_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    await state.update_data(
        clue_title=message.text.strip()
    )

    await state.set_state(
        AdminState.add_clue_text
    )

    await message.answer(
        """
📝 Теперь отправь текст улики.

После этого она появится у игроков.
"""
    )


@dp.message(AdminState.add_clue_text)
async def add_clue_text_finish(
    message: Message,
    state: FSMContext
):

    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()

    clue_key = (
        "custom_"
        + str(int(datetime.now().timestamp()))
    )

    with closing(db_connect()) as db:

        db.execute("""
            INSERT INTO clues
            (chapter_id, clue_key, title, text, reward_xp)
            VALUES (?, ?, ?, ?, 20)
        """, (
            data["chapter_id"],
            clue_key,
            data["clue_title"],
            message.text
        ))

        db.commit()

    await state.clear()

    await message.answer(
        "✅ Улика добавлена.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN USERS
# ============================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    with closing(db_connect()) as db:

        rows = db.execute("""
            SELECT nickname, game_number, xp, hp, chapter
            FROM users
            ORDER BY xp DESC
            LIMIT 20
        """).fetchall()

    if not rows:

        text = "<b>👥 ИГРОКИ</b>\n\nИгроков пока нет."

    else:

        lines = []

        for row in rows:

            lines.append(
                f"🎫 #{row['game_number']} "
                f"<b>{safe(row['nickname'])}</b>\n"
                f"📖 {row['chapter'] + 1}/20 "
                f"⭐ {row['xp']} "
                f"❤️ {row['hp']}"
            )

        text = (
            "<b>👥 ИГРОКИ</b>\n\n"
            + "\n\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN STATS
# ============================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    with closing(db_connect()) as db:

        users = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        clues = db.execute(
            "SELECT COUNT(*) FROM found_clues"
        ).fetchone()[0]

        chapters = db.execute(
            "SELECT COUNT(*) FROM chapters"
        ).fetchone()[0]

        avg_xp = db.execute(
            "SELECT COALESCE(AVG(xp),0) FROM users"
        ).fetchone()[0]

    await callback.message.edit_text(
        f"""
<b>📊 СТАТИСТИКА</b>

👥 Игроков: <b>{users}</b>

📖 Глав: <b>{chapters}</b>

🔎 Найдено улик:
<b>{clues}</b>

⭐ Средний XP:
<b>{avg_xp:.1f}</b>

🧪 Бета:
<b>{"🟢 включена" if beta_active() else "🔴 выключена"}</b>
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN SETTINGS
# ============================================================

@dp.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    await callback.message.edit_text(
        f"""
<b>⚙️ НАСТРОЙКИ</b>

🧪 Бета:
<b>{"🟢 включена" if beta_active() else "🔴 выключена"}</b>

🔐 Admin ID:
<b>{ADMIN_ID}</b>

💾 База:
<b>{DB_PATH}</b>

История хранится отдельно от кода.

Поэтому после изменения главы
при следующем деплое она не исчезнет.
""",
        reply_markup=back_keyboard()
    )

    await callback.answer()


# ============================================================
# ADMIN MENU CALLBACK
# ============================================================

@dp.callback_query(F.data == "admin")
async def admin_menu_callback(callback: CallbackQuery):

    if not admin_only(callback.from_user.id):
        return

    await callback.message.edit_text(
        """
<b>🔐 МОЁ ПРОСТРАНСТВО</b>

Полное управление «ОХОТОЙ».
""",
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# ============================================================
# UNKNOWN TEXT
# ============================================================

@dp.message(F.text)
async def unknown_text(message: Message, state: FSMContext):

    current_state = await state.get_state()

    if current_state:
        return

    user = get_user(message.from_user.id)

    if not user:

        await message.answer(
            """
Я не понял команду.

Нажми большую кнопку:

<b>🔎 НАЧАТЬ ОХОТУ</b>
""",
            reply_markup=main_keyboard()
        )

        return

    await message.answer(
        """
Выбери действие из меню.

<b>🔎 НАЧАТЬ ОХОТУ</b> — продолжить расследование.
""",
        reply_markup=main_keyboard()
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@dp.errors()
async def errors_handler(event):

    logging.exception(
        "Ошибка обработки события: %s",
        event.exception
    )

    return True


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()
    seed_story()

    logging.info("======================================")
    logging.info("🕵️ ОХОТА запускается")
    logging.info("📖 20 этапов расследования")
    logging.info("🧪 Beta: %s", beta_active())
    logging.info("🔐 Admin ID: %s", ADMIN_ID)
    logging.info("======================================")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())