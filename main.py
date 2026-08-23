import os
import sqlite3
import asyncio
import logging
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_PATH = os.getenv("DB_PATH", "ohota.db")

if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавь его в переменные окружения."
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
            nickname TEXT NOT NULL,
            xp INTEGER DEFAULT 0,
            hp INTEGER DEFAULT 100,
            reputation INTEGER DEFAULT 50,
            chapter INTEGER DEFAULT 0,
            investigations INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            interactions INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS clues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            clue_id TEXT NOT NULL,
            UNIQUE(telegram_id, clue_id)
        );

        CREATE TABLE IF NOT EXISTS progress (
            telegram_id INTEGER PRIMARY KEY,
            chapter INTEGER DEFAULT 0,
            solo INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        db.commit()


def get_user(user_id):
    with closing(conn()) as db:
        return db.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (user_id,)
        ).fetchone()


def create_user(user_id, nickname):
    with closing(conn()) as db:
        last = db.execute(
            "SELECT COALESCE(MAX(game_number), 1000) FROM users"
        ).fetchone()[0]

        number = last + 1

        db.execute("""
            INSERT INTO users
            (telegram_id, game_number, nickname)
            VALUES (?, ?, ?)
        """, (user_id, number, nickname))

        db.execute("""
            INSERT INTO progress
            (telegram_id, chapter, solo)
            VALUES (?, 0, 1)
        """, (user_id,))

        db.commit()

        return number


def change_stats(user_id, xp=0, hp=0, reputation=0):
    with closing(conn()) as db:
        db.execute("""
            UPDATE users
            SET xp = MAX(0, xp + ?),
                hp = MAX(0, MIN(100, hp + ?)),
                reputation = MAX(0, MIN(100, reputation + ?))
            WHERE telegram_id=?
        """, (xp, hp, reputation, user_id))
        db.commit()


def set_chapter(user_id, chapter):
    with closing(conn()) as db:
        db.execute(
            "UPDATE users SET chapter=? WHERE telegram_id=?",
            (chapter, user_id)
        )

        db.execute(
            """
            INSERT INTO progress(telegram_id, chapter, solo)
            VALUES (?, ?, 1)
            ON CONFLICT(telegram_id)
            DO UPDATE SET chapter=excluded.chapter
            """,
            (user_id, chapter)
        )

        db.commit()


def add_clue(user_id, clue_id):
    with closing(conn()) as db:
        try:
            db.execute(
                "INSERT INTO clues(telegram_id, clue_id) VALUES (?, ?)",
                (user_id, clue_id)
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def has_clue(user_id, clue_id):
    with closing(conn()) as db:
        row = db.execute(
            """
            SELECT 1 FROM clues
            WHERE telegram_id=? AND clue_id=?
            """,
            (user_id, clue_id)
        ).fetchone()

        return bool(row)


# ============================================================
# ИСТОРИЯ
# ============================================================

STORY = {

    0: {
        "title": "ПРОЛОГ — Последний рейс",

        "text": """
<b>ОХОТА: ПОСЛЕДНИЙ РЕЙС</b>

23:47.

Ночной поезд №417 должен был прибыть на конечную станцию
через три минуты.

Но он так и не приехал.

Поезд исчез с радаров на участке длиной всего семь километров.

Через двадцать минут диспетчер получил сообщение с телефона
машиниста:

<i>«Не ищите поезд. Ищите пассажира №17.»</i>

Проблема была в другом.

По списку пассажиров места №17 вообще не существовало.

Утром поезд нашли.

Все пассажиры были внутри.

Все двери были заперты изнутри.

Машинист исчез.

А на стекле последнего вагона кто-то написал:

<b>«ОДИН ИЗ ВАС УЖЕ ЗНАЕТ ПРАВДУ.»</b>

Ты получаешь доступ к делу.

Но с этого момента расследование становится личным.
""",

        "button": "🔎 ПРИСТУПИТЬ К РАССЛЕДОВАНИЮ"
    },

    1: {
        "title": "ГЛАВА 1 — Вагон №7",

        "text": """
<b>ГЛАВА 1 — ВАГОН №7</b>

Ты входишь в последний вагон.

Внутри пахнет мокрым металлом и дешёвым табаком.

Пассажиров двадцать.

Все утверждают, что ничего не видели.

Но есть странность.

На столике лежат часы.

Они остановились ровно в <b>23:51</b>.

На четыре минуты позже момента исчезновения поезда.

Рядом лежит билет.

Номер:

<b>0417-17</b>

На обратной стороне написано:

<i>«Не верь человеку, который первым скажет, что ничего не видел.»</i>

Ты осматриваешь вагон.

Есть три вещи, которые могут оказаться важными.
""",

        "clues": [
            (
                "clue_clock",
                "⌚ Осмотреть часы",
                """
Часы остановились в 23:51.

Но механизм исправен.

Кто-то остановил их вручную.

На задней крышке обнаружена маленькая царапина
в форме буквы <b>R</b>.

Получена улика:

<b>«Остановленные часы»</b>
"""
            ),
            (
                "clue_ticket",
                "🎫 Осмотреть билет",
                """
Билет настоящий.

Но номер 0417-17 отсутствует в системе.

Более того — билет был напечатан <b>после отправления поезда</b>.

Получена улика:

<b>«Билет, которого не должно существовать»</b>
"""
            ),
            (
                "clue_window",
                "🪟 Осмотреть окно",
                """
На внутренней стороне стекла обнаружены следы пальцев.

Следы принадлежат человеку, который сидел
в этом месте до тебя.

Под ними видна короткая надпись:

<b>R-17</b>

Получена улика:

<b>«След R-17»</b>
"""
            )
        ]
    },

    2: {
        "title": "ГЛАВА 2 — Пассажир №17",

        "text": """
<b>ГЛАВА 2 — ПАССАЖИР №17</b>

Ты проверяешь список пассажиров.

№15 — женщина.

№16 — мужчина.

№18 — подросток.

№19 — пожилой человек.

№17 отсутствует.

Но камеры показывают другое.

За две минуты до исчезновения поезда
в вагон вошёл человек.

Он сел на место №17.

Лица камеры не видят.

Человек был в капюшоне.

Ты находишь его отражение в окне.

На руке — часы.

Такие же, как у машиниста.

Значит, пассажир №17 мог быть связан
с исчезновением машиниста.

Но возникает новая проблема.

Один из пассажиров врёт.
""",

        "clues": [
            (
                "clue_passenger",
                "👤 Проверить пассажиров",
                """
Пассажир №16 утверждает:

<i>«Я всю дорогу спал.»</i>

Но его телефон показывает,
что в 23:49 он отправил сообщение
на номер без имени.

Текст:

<b>«Он здесь.»</b>
"""
            ),
            (
                "clue_camera",
                "📹 Изучить запись камеры",
                """
На записи видно, что пассажир №17
не входил через дверь.

Он уже находился в вагоне.

Это означает, что он мог попасть туда
ещё до отправления.

Получена улика:

<b>«Пассажир, которого не видели»</b>
"""
            ),
            (
                "clue_watch",
                "⌚ Сравнить часы",
                """
Часы пассажира №17 и часы машиниста
имеют одинаковую гравировку:

<b>R-17</b>

Это уже не случайность.

Кто-то заранее подготовил эту связь.
"""
            )
        ]
    },

    3: {
        "title": "ГЛАВА 3 — Ложь",

        "text": """
<b>ГЛАВА 3 — ЛОЖЬ</b>

Ты понимаешь главное.

Дело не в исчезнувшем поезде.

Кто-то хотел, чтобы двадцать пассажиров
оказались вместе.

У каждого из них есть причина что-то скрывать.

Ты получаешь доступ к записям телефонов.

В 23:52 каждый телефон получил
одно и то же уведомление.

Но у одного человека уведомления нет.

Именно у пассажира №16.

Ты проверяешь его снова.

Теперь он говорит:

<i>«Я вообще не знаю, о чём вы.»</i>

Но на его руке появляется свежий порез.

В кармане находится бумажка:

<b>«Если он спросит про R-17 — скажи, что не знаешь.»</b>

Внизу подпись:

<b>М.</b>
""",

        "clues": [
            (
                "clue_message",
                "📱 Проверить сообщение",
                """
Сообщение отправлено с телефона,
который официально принадлежит машинисту.

Но телефон найден у пассажира №16.

Получена улика:

<b>«Телефон машиниста»</b>
"""
            ),
            (
                "clue_note",
                "📄 Изучить записку",
                """
Буква «М» написана тем же человеком,
который написал R-17 на окне.

Это означает:

<b>автор записки был внутри вагона.</b>
"""
            ),
            (
                "clue_seat",
                "💺 Осмотреть место №17",
                """
Под сиденьем обнаружена металлическая пластина.

На ней выгравировано:

<b>17 / 20</b>

И ниже:

<i>«Когда останется один — открой дверь.»</i>
"""
            )
        ]
    },

    4: {
        "title": "ГЛАВА 4 — Двадцатый",

        "text": """
<b>ГЛАВА 4 — ДВАДЦАТЫЙ</b>

Ты пересчитываешь пассажиров.

Раз.

Два.

Три.

...

Девятнадцать.

Ты останавливаешься.

Потом понимаешь.

Пассажиров не двадцать.

Их было <b>девятнадцать</b> с самого начала.

Но камеры показывали двадцать человек.

Кто-то был двадцатым.

Кто-то, кого система не считала пассажиром.

Ты возвращаешься к записи камеры.

Останавливаешь её на последнем кадре.

Теперь ты видишь лицо.

И узнаёшь его.

Это человек, который должен был расследовать это дело
до тебя.

Он исчез <b>три месяца назад</b>.

На экране появляется новое сообщение:

<b>«ТЕПЕРЬ ТЫ ПОНЯЛ, ПОЧЕМУ ВЫБРАЛИ ИМЕННО ТЕБЯ.»</b>
""",

        "clues": [
            (
                "clue_twentieth",
                "🔎 Найти двадцатого",
                """
В документах обнаруживается фотография.

На ней двадцать человек.

Девятнадцать пассажиров.

И один следователь.

Следователь — ты.

Фотография сделана <b>три месяца назад</b>.

Хотя ты никогда не был в этом месте.
"""
            ),
            (
                "clue_archive",
                "🗄 Проверить архив",
                """
В архиве есть дело с таким же номером:

<b>417</b>.

Дата открытия:

три месяца назад.

Статус:

<b>НЕ ЗАКРЫТО.</b>
"""
            )
        ]
    },

    5: {
        "title": "ФИНАЛ — Продолжение следует",

        "text": """
<b>ФИНАЛ ПЕРВОЙ ЧАСТИ</b>

Ты собрал все основные улики.

Но ответов стало не больше.

Наоборот.

Теперь ты знаешь:

— кто-то контролировал поезд;

— пассажир №17 существовал;

— машинист исчез не случайно;

— дело №417 началось задолго до сегодняшней ночи;

— а твоё имя появилось в материалах расследования
ещё три месяца назад.

Ты открываешь последний файл.

В нём только одна строка:

<b>«НЕ ИЩИ ПАССАЖИРА №17.»</b>

Пауза.

Следующая строка появляется сама:

<b>«ОН ИЩЕТ ТЕБЯ.»</b>

Экран гаснет.

Через несколько секунд приходит сообщение.

Отправитель:

<b>ПАССАЖИР №17</b>

Текст:

<i>«Если хочешь узнать, кто ты на самом деле,
найди меня до следующего рейса.»</i>

━━━━━━━━━━━━━━

<b>ПРОДОЛЖЕНИЕ СЛЕДУЕТ...</b>
"""
    }
}


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def start_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🔎 НАЧАТЬ",
        callback_data="story"
    )

    return kb.as_markup()


def main_menu():
    kb = InlineKeyboardBuilder()

    kb.button(text="🔎 Продолжить расследование", callback_data="story")
    kb.button(text="👤 Мой профиль", callback_data="profile")
    kb.button(text="🏆 Рейтинг", callback_data="rating")
    kb.button(text="📜 Правила", callback_data="rules")
    kb.button(text="💬 Чат", callback_data="chat")

    kb.adjust(1)

    return kb.as_markup()


def back_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Главное меню", callback_data="menu")
    return kb.as_markup()


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start(message: Message):

    user = get_user(message.from_user.id)

    if not user:
        await message.answer(
            """
<b>🕵️ ОХОТА</b>

Добро пожаловать.

Это не обычная игра с загадками.

Здесь ты расследуешь дело, собираешь улики,
принимаешь решения и решаешь, кому доверять.

Каждое действие может изменить ход расследования.

<b>Готов начать?</b>
""",
            reply_markup=start_keyboard()
        )

        return

    await message.answer(
        f"""
<b>🕵️ С возвращением, {user['nickname']}.</b>

Дело №417 всё ещё не закрыто.

Текущий прогресс:
📖 Глава: <b>{user['chapter']}</b>
⭐ XP: <b>{user['xp']}</b>
❤️ HP: <b>{user['hp']}</b>
🤝 Репутация: <b>{user['reputation']}</b>

<b>Продолжить расследование?</b>
""",
        reply_markup=main_menu()
    )


# ============================================================
# НАЧАЛО ИСТОРИИ
# ============================================================

@dp.callback_query(F.data == "story")
async def story(callback: CallbackQuery):

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала отправь /start.",
            show_alert=True
        )
        return

    chapter = user["chapter"]

    if chapter > 5:
        chapter = 5

    data = STORY[chapter]

    kb = InlineKeyboardBuilder()

    if chapter == 0:
        kb.button(
            text="🔎 Приступить к расследованию",
            callback_data="chapter_1"
        )
    elif chapter == 5:
        kb.button(
            text="⬅️ В меню",
            callback_data="menu"
        )
    else:
        kb.button(
            text="▶️ Продолжить",
            callback_data=f"chapter_{chapter}"
        )

    kb.adjust(1)

    await callback.message.edit_text(
        data["text"],
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# ГЛАВЫ
# ============================================================

async def show_chapter(callback: CallbackQuery, chapter: int):

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала зарегистрируйся.",
            show_alert=True
        )
        return

    set_chapter(callback.from_user.id, chapter)

    data = STORY[chapter]

    kb = InlineKeyboardBuilder()

    if "clues" in data:

        for clue_id, title, text in data["clues"]:
            kb.button(
                text=title,
                callback_data=f"clue:{chapter}:{clue_id}"
            )

        kb.adjust(1)

        kb.button(
            text="▶️ Завершить главу",
            callback_data=f"finish:{chapter}"
        )

    else:
        kb.button(
            text="⬅️ Главное меню",
            callback_data="menu"
        )

    await callback.message.edit_text(
        data["text"],
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("chapter_"))
async def chapter_callback(callback: CallbackQuery):

    chapter = int(callback.data.split("_")[1])

    await show_chapter(callback, chapter)


# ============================================================
# УЛИКИ
# ============================================================

@dp.callback_query(F.data.startswith("clue:"))
async def clue_callback(callback: CallbackQuery):

    _, chapter, clue_id = callback.data.split(":")

    chapter = int(chapter)

    data = STORY[chapter]

    selected = None

    for cid, title, text in data.get("clues", []):
        if cid == clue_id:
            selected = (cid, title, text)
            break

    if not selected:
        await callback.answer(
            "Улика не найдена.",
            show_alert=True
        )
        return

    _, title, text = selected

    first_time = add_clue(
        callback.from_user.id,
        clue_id
    )

    if first_time:
        change_stats(
            callback.from_user.id,
            xp=15
        )

    kb = InlineKeyboardBuilder()

    kb.button(
        text="⬅️ К уликам",
        callback_data=f"chapter_{chapter}"
    )

    await callback.message.edit_text(
        f"""
<b>{title}</b>

{text}

{"⭐ +15 XP" if first_time else "Улика уже найдена."}
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# ЗАВЕРШЕНИЕ ГЛАВЫ
# ============================================================

@dp.callback_query(F.data.startswith("finish:"))
async def finish_chapter(callback: CallbackQuery):

    chapter = int(callback.data.split(":")[1])

    user = get_user(callback.from_user.id)

    if chapter >= 4:

        set_chapter(callback.from_user.id, 5)

        change_stats(
            callback.from_user.id,
            xp=100
        )

        with closing(conn()) as db:
            db.execute("""
                UPDATE users
                SET investigations=investigations+1,
                    wins=wins+1
                WHERE telegram_id=?
            """, (callback.from_user.id,))
            db.commit()

        await callback.message.edit_text(
            STORY[5]["text"],
            reply_markup=back_menu()
        )

        await callback.answer(
            "Расследование завершено."
        )

        return

    next_chapter = chapter + 1

    set_chapter(
        callback.from_user.id,
        next_chapter
    )

    change_stats(
        callback.from_user.id,
        xp=50
    )

    kb = InlineKeyboardBuilder()

    kb.button(
        text=f"▶️ ГЛАВА {next_chapter}",
        callback_data=f"chapter_{next_chapter}"
    )

    await callback.message.edit_text(
        f"""
<b>ГЛАВА {chapter} ЗАВЕРШЕНА</b>

⭐ Ты получил <b>+50 XP</b>.

Но расследование только начинается.

Следующая часть дела уже ждёт тебя.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# ПРОФИЛЬ
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Сначала зарегистрируйся.",
            show_alert=True
        )
        return

    with closing(conn()) as db:
        clues = db.execute(
            "SELECT COUNT(*) FROM clues WHERE telegram_id=?",
            (callback.from_user.id,)
        ).fetchone()[0]

    await callback.message.edit_text(
        f"""
<b>👤 МОЁ ДОСЬЕ</b>

🎫 Игровой номер: <b>#{user['game_number']}</b>
🕵️ Псевдоним: <b>{user['nickname']}</b>

━━━━━━━━━━━━━━

📖 Глава: <b>{user['chapter']}</b>
⭐ XP: <b>{user['xp']}</b>
❤️ HP: <b>{user['hp']}</b>
🤝 Репутация: <b>{user['reputation']}</b>

🔎 Найдено улик: <b>{clues}</b>
🏆 Расследований: <b>{user['investigations']}</b>
🥇 Побед: <b>{user['wins']}</b>
🤝 Взаимодействий: <b>{user['interactions']}</b>
""",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# РЕЙТИНГ
# ============================================================

@dp.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery):

    with closing(conn()) as db:
        rows = db.execute("""
            SELECT nickname, xp, reputation
            FROM users
            ORDER BY xp DESC, reputation DESC
            LIMIT 10
        """).fetchall()

    if not rows:
        text = "🏆 Пока игроков нет."
    else:

        lines = []

        for i, row in enumerate(rows, 1):
            lines.append(
                f"<b>{i}.</b> {row['nickname']} — "
                f"⭐ {row['xp']} XP"
            )

        text = (
            "<b>🏆 РЕЙТИНГ ОХОТНИКОВ</b>\n\n"
            + "\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# ПРАВИЛА
# ============================================================

@dp.callback_query(F.data == "rules")
async def rules(callback: CallbackQuery):

    await callback.message.edit_text(
        """
<b>📜 ПРАВИЛА «ОХОТЫ»</b>

🔎 Собирай улики.

🧠 Анализируй информацию.

🤝 В следующих версиях расследований
можно будет взаимодействовать с другими
игроками.

🎭 Можно будет доверять игроку или
попытаться его обмануть.

⭐ За успешные действия начисляется XP.

❤️ Ошибки могут уменьшать HP.

🤝 Репутация показывает, насколько другим
игрокам можно тебе доверять.

🏆 Чем лучше расследование — тем выше
позиция в рейтинге.

<b>Главное правило:</b>

Не доверяй никому полностью.
""",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# ЧАТ
# ============================================================

@dp.callback_query(F.data == "chat")
async def chat(callback: CallbackQuery):

    kb = InlineKeyboardBuilder()

    kb.button(
        text="💬 Открыть чат",
        url="https://t.me/ohota_online_chat"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="menu"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        """
<b>💬 СООБЩЕСТВО «ОХОТЫ»</b>

Здесь игроки смогут обсуждать расследования,
но настоящие улики лучше не раскрывать.

Скоро появится отдельная система
взаимодействия между игроками.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

@dp.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery):

    await callback.message.edit_text(
        """
<b>🕵️ ОХОТА</b>

<b>Дело №417 всё ещё открыто.</b>

Выбери действие:
""",
        reply_markup=main_menu()
    )

    await callback.answer()


# ============================================================
# АДМИНКА
# ============================================================

def admin_keyboard():
    kb = InlineKeyboardBuilder()

    kb.button(
        text="🎯 Управление охотой",
        callback_data="admin_hunt"
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
        text="📝 История",
        callback_data="admin_story"
    )

    kb.button(
        text="⬅️ Выйти",
        callback_data="menu"
    )

    kb.adjust(1)

    return kb.as_markup()


def is_admin(user_id):
    return user_id == ADMIN_ID and ADMIN_ID != 0


@dp.message(Command("admin"))
async def admin(message: Message):

    if not is_admin(message.from_user.id):
        await message.answer(
            "⛔ Доступ запрещён."
        )
        return

    await message.answer(
        """
<b>🔐 МОЁ ПРОСТРАНСТВО</b>

Добро пожаловать, администратор.

Здесь находится управление «ОХОТОЙ».

Ты можешь управлять историей,
игроками и статистикой.
""",
        reply_markup=admin_keyboard()
    )


# ============================================================
# АДМИН — ОХОТА
# ============================================================

@dp.callback_query(F.data == "admin_hunt")
async def admin_hunt(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        await callback.answer(
            "Доступ запрещён.",
            show_alert=True
        )
        return

    kb = InlineKeyboardBuilder()

    kb.button(
        text="▶️ Запустить тест",
        callback_data="admin_start"
    )

    kb.button(
        text="⏹ Остановить",
        callback_data="admin_stop"
    )

    kb.button(
        text="✏️ Редактирование",
        callback_data="admin_edit"
    )

    kb.button(
        text="➕ Создание главы",
        callback_data="admin_create"
    )

    kb.button(
        text="🖼 Добавить картинку",
        callback_data="admin_image"
    )

    kb.button(
        text="⬅️ Назад",
        callback_data="admin"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        """
<b>🎯 УПРАВЛЕНИЕ ОХОТОЙ</b>

Здесь будут основные действия с расследованием.

▶️ Запустить — открыть тестовую охоту.

⏹ Остановить — остановить тест.

✏️ Редактирование — изменить содержание.

➕ Создание главы — добавить новую главу.

🖼 Добавить картинку — прикрепить изображение
к выбранной главе.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data == "admin")
async def admin_back(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.message.edit_text(
        "<b>🔐 МОЁ ПРОСТРАНСТВО</b>\n\nВыбери раздел:",
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# ============================================================
# АДМИН — СТАТИСТИКА
# ============================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    with closing(conn()) as db:

        users = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        clues = db.execute(
            "SELECT COUNT(*) FROM clues"
        ).fetchone()[0]

        avg_xp = db.execute(
            "SELECT COALESCE(AVG(xp),0) FROM users"
        ).fetchone()[0]

    await callback.message.edit_text(
        f"""
<b>📊 СТАТИСТИКА «ОХОТЫ»</b>

👥 Игроков: <b>{users}</b>
🔎 Найдено улик: <b>{clues}</b>
⭐ Средний XP: <b>{avg_xp:.1f}</b>

🎯 Активная история:

<b>«Последний рейс»</b>
""",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# АДМИН — ИСТОРИЯ
# ============================================================

@dp.callback_query(F.data == "admin_story")
async def admin_story(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    kb = InlineKeyboardBuilder()

    for chapter, data in STORY.items():

        kb.button(
            text=f"📖 {data['title']}",
            callback_data=f"admin_chapter:{chapter}"
        )

    kb.button(
        text="⬅️ Назад",
        callback_data="admin"
    )

    kb.adjust(1)

    await callback.message.edit_text(
        """
<b>📝 РЕДАКТОР ИСТОРИИ</b>

Выбери главу.

В дальнейшем здесь можно будет
редактировать текст, задания, улики
и изображения без изменения основного кода.
""",
        reply_markup=kb.as_markup()
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("admin_chapter:"))
async def admin_chapter(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    chapter = int(callback.data.split(":")[1])

    data = STORY[chapter]

    await callback.message.edit_text(
        f"""
<b>{data['title']}</b>

Улик в главе:
<b>{len(data.get('clues', []))}</b>

Сейчас содержание истории находится
в коде.

Следующий этап — вынести редактор полностью
в базу данных, чтобы ты мог менять главы
прямо из Telegram.
""",
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# АДМИН — ИГРОКИ
# ============================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    with closing(conn()) as db:
        rows = db.execute("""
            SELECT nickname, game_number, xp, chapter
            FROM users
            ORDER BY xp DESC
            LIMIT 15
        """).fetchall()

    if not rows:
        text = "<b>👥 ИГРОКИ</b>\n\nПока игроков нет."
    else:

        lines = []

        for row in rows:
            lines.append(
                f"🎫 #{row['game_number']} "
                f"<b>{row['nickname']}</b>\n"
                f"   Глава: {row['chapter']} | XP: {row['xp']}"
            )

        text = (
            "<b>👥 ИГРОКИ</b>\n\n"
            + "\n\n".join(lines)
        )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# АДМИН — ЗАГЛУШКИ УПРАВЛЕНИЯ
# ============================================================

@dp.callback_query(F.data == "admin_start")
async def admin_start(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.answer(
        "🟢 Тестовая охота запущена.",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_stop")
async def admin_stop(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.answer(
        "🔴 Охота остановлена.",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_edit")
async def admin_edit(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.answer(
        "✏️ Редактор будет использоваться для изменения глав.",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_create")
async def admin_create(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.answer(
        "➕ Здесь будет создание новой главы.",
        show_alert=True
    )


@dp.callback_query(F.data == "admin_image")
async def admin_image(callback: CallbackQuery):

    if not is_admin(callback.from_user.id):
        return

    await callback.answer(
        "🖼️ Здесь будет добавление изображения к главе.",
        show_alert=True
    )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    init_db()

    logging.info("=================================")
    logging.info("🕵️ ОХОТА запускается")
    logging.info("История: Последний рейс")
    logging.info("=================================")

    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())