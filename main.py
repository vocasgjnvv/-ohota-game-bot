import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# OHOTA GAME
# ДЕЛО №001 — «ПОСЛЕДНИЙ РЕЙС»
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
CHAT_URL = os.getenv("CHAT_URL", "")

DB_PATH = os.getenv("DB_PATH", "ohota_game.db")

CASE_ID = 1
CASE_TITLE = "Последний рейс"
CASE_DIFFICULTY = "★★★★☆☆"
GAME_DURATION = 60 * 60

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ohota")


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    con = db()
    cur = con.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        created_at TEXT NOT NULL,
        total_games INTEGER DEFAULT 0,
        completed_games INTEGER DEFAULT 0,
        total_points INTEGER DEFAULT 0,
        best_time INTEGER,
        achievements TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS game_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        case_id INTEGER NOT NULL,
        current_episode INTEGER DEFAULT 0,
        started_at TEXT,
        finished_at TEXT,
        finished INTEGER DEFAULT 0,
        success INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0,
        mistakes INTEGER DEFAULT 0,
        interaction_mode TEXT DEFAULT 'solo',
        clues_found INTEGER DEFAULT 0,
        correct_answers INTEGER DEFAULT 0,
        UNIQUE(telegram_id, case_id, finished)
    );

    CREATE TABLE IF NOT EXISTS player_clues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        clue_id INTEGER NOT NULL,
        UNIQUE(session_id, clue_id)
    );

    CREATE TABLE IF NOT EXISTS game_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        episode INTEGER NOT NULL,
        answer TEXT,
        correct INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS game_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        case_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        time_seconds INTEGER NOT NULL,
        points INTEGER NOT NULL,
        mistakes INTEGER NOT NULL,
        clues_found INTEGER NOT NULL,
        correct_answers INTEGER NOT NULL,
        finished_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        username TEXT,
        category TEXT,
        message TEXT,
        case_id INTEGER,
        episode INTEGER,
        game_time INTEGER,
        status TEXT DEFAULT 'NEW',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS beta_testers (
        telegram_id INTEGER PRIMARY KEY,
        added_at TEXT NOT NULL,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS player_achievements (
        telegram_id INTEGER NOT NULL,
        achievement_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(telegram_id, achievement_id)
    );

    CREATE TABLE IF NOT EXISTS episode_media (
        episode INTEGER PRIMARY KEY,
        file_id TEXT
    );
    """)

    achievements = [
        ("first_clue", "🔎 Первая улика", "Найдена первая улика."),
        ("first_case", "🕵️ Первое дело", "Раскрыто первое дело."),
        ("no_mistakes", "🎯 Без ошибок", "Дело завершено без ошибок."),
        ("speed", "⚡ Быстрый след", "Дело завершено менее чем за 45 минут."),
        ("perfect", "🏆 Идеальное расследование", "Все ключевые решения приняты правильно."),
        ("beta", "🧪 Бета-тестер", "Пройден тестовый раунд."),
    ]

    for code, title, description in achievements:
        cur.execute(
            """
            INSERT OR IGNORE INTO achievements(code, title, description)
            VALUES (?, ?, ?)
            """,
            (code, title, description),
        )

    con.commit()
    con.close()


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_user(telegram_id):
    con = db()
    row = con.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    con.close()
    return row


def ensure_user(tg_user):
    con = db()
    existing = con.execute(
        "SELECT id FROM users WHERE telegram_id = ?",
        (tg_user.id,),
    ).fetchone()

    if existing:
        con.execute(
            """
            UPDATE users
            SET username = ?, first_name = ?
            WHERE telegram_id = ?
            """,
            (
                tg_user.username,
                tg_user.first_name,
                tg_user.id,
            ),
        )
    else:
        con.execute(
            """
            INSERT INTO users(
                telegram_id, username, first_name, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                tg_user.id,
                tg_user.username,
                tg_user.first_name,
                now_iso(),
            ),
        )

    con.commit()
    con.close()


def is_admin(user_id):
    return user_id in ADMIN_IDS


def is_beta(user_id):
    con = db()
    row = con.execute(
        "SELECT 1 FROM beta_testers WHERE telegram_id = ? AND active = 1",
        (user_id,),
    ).fetchone()
    con.close()
    return bool(row)


def get_active_session(user_id):
    con = db()
    row = con.execute(
        """
        SELECT *
        FROM game_sessions
        WHERE telegram_id = ?
          AND case_id = ?
          AND finished = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, CASE_ID),
    ).fetchone()
    con.close()
    return row


def elapsed_seconds(session):
    if not session or not session["started_at"]:
        return 0

    started = datetime.fromisoformat(session["started_at"])
    elapsed = int(
        (datetime.now(timezone.utc) - started).total_seconds()
    )
    return max(0, elapsed)


def time_left(session):
    return max(0, GAME_DURATION - elapsed_seconds(session))


def format_time(seconds):
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


# ============================================================
# CASE / STORY
# ============================================================

EPISODES = [
    {
        "title": "Пустой перрон",
        "text": (
            "23:47.\n\n"
            "Последний поезд уже должен был покинуть станцию. "
            "Но камера наблюдения зафиксировала человека, который "
            "вошёл в вагон за несколько минут до отправления.\n\n"
            "Поезд ушёл.\n"
            "Человек из вагона больше нигде не появился.\n\n"
            "На платформе осталась только одна вещь — старые часы "
            "с остановившейся стрелкой на 23:41.\n\n"
            "Шесть минут до отправления.\n\n"
            "И почему-то именно эти шесть минут никто не может объяснить."
        ),
        "clue": "Старые часы остановились на 23:41.",
        "options": [
            ("A", "Изучить часы", True),
            ("B", "Сразу искать пассажира", False),
            ("C", "Проверить расписание", False),
        ],
    },
    {
        "title": "Камера №7",
        "text": (
            "Ты находишь запись камеры №7.\n\n"
            "На видео человек входит в вагон. Лица почти не видно.\n"
            "Но перед тем как дверь закрывается, он оглядывается "
            "на камеру.\n\n"
            "Странно другое.\n\n"
            "Время на записи отличается от времени станции на шесть минут."
        ),
        "clue": "Камера №7 отставала от времени станции ровно на шесть минут.",
        "options": [
            ("A", "Сверить время камер", True),
            ("B", "Удалить запись", False),
            ("C", "Игнорировать расхождение", False),
        ],
    },
    {
        "title": "Билет",
        "text": (
            "В вагоне найден билет.\n\n"
            "Он пробит на имя, которое не совпадает с пассажиром "
            "из записи.\n\n"
            "Но номер места совпадает с местом, возле которого "
            "человек стоял перед отправлением."
        ),
        "clue": "Билет оформлен на другое имя, но содержит нужный номер места.",
        "options": [
            ("A", "Проверить историю номера места", True),
            ("B", "Считать билет поддельным", False),
            ("C", "Выбросить билет", False),
        ],
    },
    {
        "title": "Номер 18",
        "text": (
            "Место №18 числится свободным.\n\n"
            "Но в системе обслуживания вагона отмечено, что "
            "к нему несколько раз подходил проводник.\n\n"
            "Почему проверяли пустое место?"
        ),
        "clue": "Проводник несколько раз проверял место №18.",
        "options": [
            ("A", "Найти объяснение действиям проводника", True),
            ("B", "Обвинить проводника", False),
            ("C", "Считать это случайностью", False),
        ],
    },
    {
        "title": "Проводник",
        "text": (
            "Проводник утверждает, что просто проверял дверь.\n\n"
            "Но в журнале осмотра двери отмечена другая информация.\n\n"
            "Осмотр двери был произведён на две минуты раньше."
        ),
        "clue": "Показания проводника не совпадают с журналом осмотра.",
        "options": [
            ("A", "Сопоставить все временные отметки", True),
            ("B", "Сразу считать проводника виновным", False),
            ("C", "Не учитывать журнал", False),
        ],
    },
    {
        "title": "Шесть минут",
        "text": (
            "Теперь становится понятно: расхождение камер — не случайность.\n\n"
            "Кто-то мог использовать временной разрыв, чтобы создать "
            "ложную последовательность событий.\n\n"
            "Но пока неизвестно, зачем."
        ),
        "clue": "Разница в шесть минут повторяется в нескольких источниках.",
        "options": [
            ("A", "Искать источник первоначального времени", True),
            ("B", "Остановиться на камерах", False),
            ("C", "Игнорировать шесть минут", False),
        ],
    },
    {
        "title": "Архив",
        "text": (
            "В архиве находится старая схема станции.\n\n"
            "На ней отмечен технический проход, которого нет "
            "на современных планах.\n\n"
            "Проход соединяет платформу с зоной обслуживания."
        ),
        "clue": "На старом плане есть скрытый технический проход.",
        "options": [
            ("A", "Проверить технический проход", True),
            ("B", "Считать план устаревшим", False),
            ("C", "Искать человека только в вагоне", False),
        ],
    },
    {
        "title": "След",
        "text": (
            "В техническом проходе найден свежий след обуви.\n\n"
            "След ведёт не к выходу.\n\n"
            "Он заканчивается возле закрытой служебной двери."
        ),
        "clue": "След обуви заканчивается у служебной двери.",
        "options": [
            ("A", "Проверить служебную дверь", True),
            ("B", "Идти по следу назад", False),
            ("C", "Не обращать внимания на след", False),
        ],
    },
    {
        "title": "Дверь",
        "text": (
            "Дверь закрыта.\n\n"
            "На замке нет следов взлома.\n\n"
            "Значит, тот кто прошёл сюда, либо имел ключ, "
            "либо дверь была открыта изнутри."
        ),
        "clue": "Служебная дверь была открыта без взлома.",
        "options": [
            ("A", "Проверить список ключей", True),
            ("B", "Предположить взлом", False),
            ("C", "Закончить расследование", False),
        ],
    },
    {
        "title": "Ключ",
        "text": (
            "В журнале ключей появляется номер 1842.\n\n"
            "Ключ был выдан в 23:36.\n\n"
            "Получатель — сотрудник, который официально "
            "в тот вечер не работал."
        ),
        "clue": "Ключ №1842 был выдан в 23:36 человеку, которого не должно было быть на смене.",
        "options": [
            ("A", "Проверить личность получателя", True),
            ("B", "Считать журнал ошибочным", False),
            ("C", "Сразу искать ключ", False),
        ],
    },
    {
        "title": "Чужое имя",
        "text": (
            "Имя получателя ключа совпадает с именем из билета.\n\n"
            "Но подпись в журнале отличается.\n\n"
            "Кто-то использовал чужие данные."
        ),
        "clue": "Имя из билета связано с получателем ключа.",
        "options": [
            ("A", "Сопоставить билет и журнал ключей", True),
            ("B", "Считать совпадение случайным", False),
            ("C", "Искать другого пассажира", False),
        ],
    },
    {
        "title": "Запись",
        "text": (
            "В старом терминале находится удалённая запись.\n\n"
            "В ней всего одна фраза:\n\n"
            "«Если поезд уйдёт в 23:47, ищите не внутри.»\n\n"
            "Кто её оставил — неизвестно."
        ),
        "clue": "В старой записи прямо сказано искать не внутри поезда.",
        "options": [
            ("A", "Вернуться к техническому проходу", True),
            ("B", "Продолжить искать в вагоне", False),
            ("C", "Удалить запись", False),
        ],
    },
    {
        "title": "Ложный след",
        "text": (
            "В проходе находится куртка.\n\n"
            "В кармане — билет и чужой телефон.\n\n"
            "Кажется, ты наконец нашёл человека.\n\n"
            "Но телефон включается и показывает входящий звонок "
            "с номера, который принадлежит самому себе."
        ),
        "clue": "Оставленная куртка выглядит как намеренно созданный ложный след.",
        "options": [
            ("A", "Проверить, кому выгодно оставить куртку", True),
            ("B", "Считать куртку доказательством", False),
            ("C", "Забрать телефон и уйти", False),
        ],
    },
    {
        "title": "Телефон",
        "text": (
            "В памяти телефона сохранилась одна фотография.\n\n"
            "На ней — часы станции.\n\n"
            "На фотографии время 23:41.\n\n"
            "Но фотография сделана значительно раньше."
        ),
        "clue": "Изображение часов было подготовлено заранее.",
        "options": [
            ("A", "Искать того, кто мог подготовить изображение", True),
            ("B", "Считать часы реальным временем", False),
            ("C", "Удалить фотографию", False),
        ],
    },
    {
        "title": "Человек вне расписания",
        "text": (
            "В журнале доступа обнаруживается человек, которого "
            "нет в списке сотрудников.\n\n"
            "Он вошёл на станцию за час до последнего рейса.\n\n"
            "После этого его карта больше нигде не использовалась."
        ),
        "clue": "Неизвестный человек вошёл на станцию за час до события.",
        "options": [
            ("A", "Сопоставить его маршрут с техническим проходом", True),
            ("B", "Считать карту неисправной", False),
            ("C", "Искать его только на платформе", False),
        ],
    },
    {
        "title": "Связь",
        "text": (
            "Маршрут неизвестного человека совпадает с маршрутом "
            "получателя ключа №1842.\n\n"
            "Теперь две линии расследования сходятся."
        ),
        "clue": "Неизвестный человек и получатель ключа связаны одним маршрутом.",
        "options": [
            ("A", "Проверить связь между двумя личностями", True),
            ("B", "Считать совпадение случайным", False),
            ("C", "Вернуться к билету", False),
        ],
    },
    {
        "title": "Настоящий пассажир",
        "text": (
            "Имя из билета принадлежит человеку, который официально "
            "не садился на этот поезд.\n\n"
            "Однако его пропуск использовался в служебной зоне.\n\n"
            "Пассажир и сотрудник могли быть одним человеком."
        ),
        "clue": "Один человек мог использовать две личности.",
        "options": [
            ("A", "Сопоставить пропуск, билет и ключ", True),
            ("B", "Выбрать только билет", False),
            ("C", "Выбрать только пропуск", False),
        ],
    },
    {
        "title": "Последние шесть минут",
        "text": (
            "Теперь вся временная цепочка складывается.\n\n"
            "23:36 — ключ.\n"
            "23:41 — подготовленная отметка.\n"
            "23:47 — отправление.\n\n"
            "Шесть минут были не ошибкой.\n\n"
            "Они были частью плана."
        ),
        "clue": "Шесть минут были частью заранее подготовленного плана.",
        "options": [
            ("A", "Восстановить последовательность событий", True),
            ("B", "Игнорировать временную линию", False),
            ("C", "Обвинить первого подозреваемого", False),
        ],
    },
    {
        "title": "Последний след",
        "text": (
            "В техническом помещении найден последний фрагмент.\n\n"
            "Это не предмет.\n\n"
            "Это запись маршрута.\n\n"
            "Она показывает, что человек не покидал станцию через "
            "обычный выход."
        ),
        "clue": "Человек покинул место через неизвестный маршрут.",
        "options": [
            ("A", "Сопоставить маршрут с архивным планом", True),
            ("B", "Искать обычный выход", False),
            ("C", "Считать расследование законченным", False),
        ],
    },
    {
        "title": "Развязка",
        "text": (
            "Последняя часть расследования.\n\n"
            "Теперь у тебя есть вся цепочка:\n\n"
            "ключ → билет → временная подмена → технический проход → "
            "ложный след → человек с двумя личностями.\n\n"
            "Но главный вопрос остаётся:\n\n"
            "кто организовал исчезновение и зачем?"
        ),
        "clue": "Финальная цепочка связывает ключ, билет, время и технический проход.",
        "options": [
            ("A", "Сопоставить всю цепочку", True),
            ("B", "Выбрать самый очевидный вариант", False),
            ("C", "Основываться только на первой улике", False),
        ],
    },
]


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 НАЧАТЬ ОХОТУ", callback_data="start_hunt")],
        [
            InlineKeyboardButton("🗂 ДЕЛО", callback_data="case"),
            InlineKeyboardButton("👤 МОЁ ПРОСТРАНСТВО", callback_data="space"),
        ],
        [
            InlineKeyboardButton("🏆 РЕЙТИНГ", callback_data="rating"),
            InlineKeyboardButton("📜 ПРАВИЛА", callback_data="rules"),
        ],
        [
            InlineKeyboardButton("💬 ЧАТ", url=CHAT_URL)
            if CHAT_URL
            else InlineKeyboardButton("💬 ЧАТ", callback_data="chat")
        ],
        [InlineKeyboardButton("🛟 ПОДДЕРЖКА", callback_data="support")],
    ])


def back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 НАЗАД", callback_data="main")]
    ])


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    text = (
        "╔════════════════════╗\n"
        "        🔎 OHOTA GAME\n"
        "╚════════════════════╝\n\n"
        "Ты входишь в игру, где недостаточно просто найти ответ.\n\n"
        "Нужно заметить то, что другие пропустят.\n"
        "Сопоставить то, что на первый взгляд не связано.\n"
        "И успеть сделать это раньше остальных.\n\n"
        "Первое дело уже ждёт."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# ============================================================
# MAIN CALLBACK ROUTER
# ============================================================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    ensure_user(query.from_user)

    data = query.data

    if data == "main":
        await show_main(query)
    elif data == "start_hunt":
        await start_hunt(query)
    elif data == "case":
        await show_case(query)
    elif data == "space":
        await show_space(query)
    elif data == "profile":
        await show_profile(query)
    elif data == "dossier":
        await show_dossier(query)
    elif data == "results":
        await show_results(query)
    elif data == "achievements":
        await show_achievements(query)
    elif data == "rating":
        await show_rating(query)
    elif data == "rules":
        await show_rules(query)
    elif data == "support":
        await show_support(query)
    elif data.startswith("support_cat:"):
        category = data.split(":", 1)[1]
        context.user_data["support_category"] = category
        context.user_data["awaiting_support"] = True

        await query.edit_message_text(
            "🛟 ОПИШИ ПРОБЛЕМУ\n\n"
            "Напиши одним сообщением, что произошло.\n\n"
            "Я автоматически сохраню:\n"
            "• дело\n"
            "• эпизод\n"
            "• время игры\n"
            "• твой Telegram\n\n"
            "После этого обращение попадёт в поддержку."
        )

    elif data == "beta":
        await show_beta_admin(query)
    elif data == "admin":
        await show_admin(query)
    elif data == "tickets":
        await show_tickets(query)
    elif data.startswith("ticket:"):
        ticket_id = int(data.split(":")[1])
        await show_ticket(query, ticket_id)
    elif data.startswith("ticket_status:"):
        parts = data.split(":")
        ticket_id = int(parts[1])
        status = parts[2]
        await change_ticket_status(query, ticket_id, status)
    elif data.startswith("ticket_reply:"):
        ticket_id = int(data.split(":")[1])
        context.user_data["reply_ticket"] = ticket_id
        await query.edit_message_text(
            "↩️ ОТВЕТ ПОЛЬЗОВАТЕЛЮ\n\n"
            "Напиши сообщение, которое нужно отправить."
        )
    elif data == "add_beta":
        context.user_data["awaiting_beta"] = True
        await query.edit_message_text(
            "🧪 ДОБАВЛЕНИЕ ТЕСТЕРА\n\n"
            "Отправь Telegram ID тестера числом."
        )
    elif data == "beta_list":
        await show_beta_list(query)
    elif data == "beta_stats":
        await show_beta_stats(query)
    elif data == "media":
        await show_media_admin(query)
    elif data == "interaction":
        await interaction_menu(query)
    elif data == "solo":
        await set_interaction_mode(query, "solo")
    elif data == "join_search":
        await find_interaction_partner(query)
    elif data.startswith("choose_partner:"):
        partner_id = int(data.split(":")[1])
        await choose_partner(query, partner_id)
    elif data.startswith("answer:"):
        await process_answer(query, data.split(":")[1])
    elif data == "dossier":
        await show_dossier(query)
    elif data.startswith("continue:"):
        episode = int(data.split(":")[1])
        await show_episode(query, episode)


# ============================================================
# MAIN
# ============================================================

async def show_main(query):
    await query.edit_message_text(
        "╔════════════════════╗\n"
        "        🔎 OHOTA GAME\n"
        "╚════════════════════╝\n\n"
        "Дело ждёт.\n"
        "60 минут.\n"
        "Одна попытка доказать, что ты лучший.\n\n"
        "Выбирай действие.",
        reply_markup=main_keyboard(),
    )


async def show_case(query):
    await query.edit_message_text(
        "╔════════════════════╗\n"
        "        ДЕЛО №001\n"
        "╚════════════════════╝\n\n"
        "«ПОСЛЕДНИЙ РЕЙС»\n\n"
        f"Сложность: {CASE_DIFFICULTY}\n"
        "Время: 60 минут\n"
        "Эпизодов: 20\n\n"
        "На станции исчез человек.\n"
        "Камеры показывают невозможную временную линию.\n"
        "Один билет ведёт к человеку, которого не существовало "
        "в расписании.\n\n"
        "Некоторые следы оставлены специально.\n\n"
        "🔎 Найди правду раньше остальных.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 НАЧАТЬ", callback_data="start_hunt")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="main")],
        ]),
    )


# ============================================================
# START HUNT
# ============================================================

async def start_hunt(query):
    user_id = query.from_user.id

    existing = get_active_session(user_id)

    if existing:
        if time_left(existing) <= 0:
            await finish_game(user_id, existing["id"], query)
            return

        await query.edit_message_text(
            "⚠️ У тебя уже идёт расследование.\n\n"
            f"Дело №001\n"
            f"Эпизод: {existing['current_episode']}/20\n"
            f"Осталось: {format_time(time_left(existing))}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 ПРОДОЛЖИТЬ",
                        callback_data=f"continue:{existing['current_episode']}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗂 ДОСЬЕ",
                        callback_data="dossier",
                    )
                ],
            ]),
        )
        return

    await query.edit_message_text("🔐 ПОДКЛЮЧЕНИЕ К ДЕЛУ...")
    await asyncio.sleep(0.7)

    await query.edit_message_text("🔎 ПОИСК МАТЕРИАЛОВ...")
    await asyncio.sleep(0.7)

    await query.edit_message_text("📁 ДЕЛО НАЙДЕНО...")
    await asyncio.sleep(0.7)

    await query.edit_message_text("⚠️ НЕКОТОРЫЕ ДАННЫЕ ПОВРЕЖДЕНЫ...")
    await asyncio.sleep(0.7)

    await query.edit_message_text("🔓 ДОСТУП ПОЛУЧЕН...")
    await asyncio.sleep(0.5)

    await query.edit_message_text(
        "╔════════════════════╗\n"
        "        ДЕЛО №001\n"
        "╚════════════════════╝\n\n"
        "«ПОСЛЕДНИЙ РЕЙС»\n\n"
        f"Сложность: {CASE_DIFFICULTY}\n"
        "Время: 60 минут\n"
        "Эпизодов: 20\n\n"
        "23:47.\n\n"
        "Последний поезд должен был уйти со станции.\n\n"
        "Он ушёл.\n\n"
        "Но один человек, который вошёл в вагон перед отправлением, "
        "так и не появился ни на одной другой камере.\n\n"
        "На платформе остались старые часы.\n\n"
        "Они остановились на 23:41.\n\n"
        "Шесть минут.\n\n"
        "И почему-то именно эти шесть минут никто не может объяснить.\n\n"
        "Если хочешь узнать, что произошло — начинай охоту.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 НАЧАТЬ ОХОТУ", callback_data="continue:1")],
            [InlineKeyboardButton("📜 ПРАВИЛА", callback_data="rules")],
        ]),
    )


# ============================================================
# CREATE GAME SESSION
# ============================================================

def create_session(user_id):
    con = db()

    con.execute(
        "UPDATE game_sessions SET finished = 1 WHERE telegram_id = ? AND case_id = ? AND finished = 0",
        (user_id, CASE_ID),
    )

    cur = con.execute(
        """
        INSERT INTO game_sessions(
            telegram_id,
            case_id,
            current_episode,
            started_at,
            interaction_mode
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            CASE_ID,
            1,
            now_iso(),
            "solo",
        ),
    )

    session_id = cur.lastrowid

    con.execute(
        "UPDATE users SET total_games = total_games + 1 WHERE telegram_id = ?",
        (user_id,),
    )

    con.commit()
    con.close()

    return session_id


# ============================================================
# EPISODES
# ============================================================

async def show_episode(query, episode_number):
    user_id = query.from_user.id

    session = get_active_session(user_id)

    if not session:
        create_session(user_id)
        session = get_active_session(user_id)

    if time_left(session) <= 0:
        await finish_game(user_id, session["id"], query)
        return

    if episode_number < 1:
        episode_number = 1

    if episode_number > 20:
        await final_decision(query, session)
        return

    con = db()
    con.execute(
        """
        UPDATE game_sessions
        SET current_episode = ?
        WHERE id = ?
        """,
        (episode_number, session["id"]),
    )
    con.commit()
    con.close()

    episode = EPISODES[episode_number - 1]

    media = get_episode_media(episode_number)

    text = (
        f"╔════════════════════╗\n"
        f"      ЭПИЗОД {episode_number:02d}/20\n"
        f"      {CASE_DIFFICULTY}\n"
        f"╚════════════════════╝\n\n"
        f"⏱ Осталось: {format_time(time_left(session))}\n\n"
        f"🕵️ {episode['title']}\n\n"
        f"{episode['text']}\n\n"
        "Что будешь делать?"
    )

    buttons = []

    for value, label, correct in episode["options"]:
        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"answer:{episode_number}:{value}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("👥 ВЗАИМОДЕЙСТВОВАТЬ", callback_data="interaction"),
        InlineKeyboardButton("🗂 ДОСЬЕ", callback_data="dossier"),
    ])

    buttons.append([
        InlineKeyboardButton("🛟 ПОДДЕРЖКА", callback_data="support")
    ])

    if media:
        try:
            await query.message.reply_photo(
                photo=media,
                caption=text,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            await query.edit_message_text(
                f"📷 Материал эпизода {episode_number:02d}/20 отправлен выше."
            )
            return
        except Exception:
            logger.exception("Не удалось отправить изображение.")

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# ANSWERS
# ============================================================

async def process_answer(query, payload):
    parts = payload.split(":")

    if len(parts) != 2:
        return

    episode_number = int(parts[0])
    selected = parts[1]

    user_id = query.from_user.id
    session = get_active_session(user_id)

    if not session:
        await query.edit_message_text(
            "⚠️ Активного расследования нет.",
            reply_markup=main_keyboard(),
        )
        return

    if time_left(session) <= 0:
        await finish_game(user_id, session["id"], query)
        return

    episode = EPISODES[episode_number - 1]

    correct = False

    for value, _, is_correct in episode["options"]:
        if value == selected:
            correct = is_correct
            break

    con = db()

    con.execute(
        """
        INSERT INTO game_answers(
            session_id,
            episode,
            answer,
            correct,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session["id"],
            episode_number,
            selected,
            1 if correct else 0,
            now_iso(),
        ),
    )

    if correct:
        points = 100
        con.execute(
            """
            UPDATE game_sessions
            SET points = points + ?,
                correct_answers = correct_answers + 1,
                clues_found = clues_found + 1
            WHERE id = ?
            """,
            (points, session["id"]),
        )

        clue_id = episode_number

        con.execute(
            """
            INSERT OR IGNORE INTO player_clues(session_id, clue_id)
            VALUES (?, ?)
            """,
            (session["id"], clue_id),
        )

        con.commit()
        con.close()

        await query.edit_message_text(
            "✅ СЛЕД ПОДТВЕРЖДЁН\n\n"
            f"Ты нашёл новую зацепку:\n\n"
            f"🔎 {episode['clue']}\n\n"
            "Но пока невозможно понять, куда она ведёт.\n\n"
            f"⏱ Осталось: {format_time(time_left(session))}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➡️ ПРОДОЛЖИТЬ",
                        callback_data=f"continue:{episode_number + 1}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗂 ДОСЬЕ",
                        callback_data="dossier"
                    )
                ],
            ]),
        )
    else:
        con.execute(
            """
            UPDATE game_sessions
            SET mistakes = mistakes + 1,
                points = CASE
                    WHEN points >= 25 THEN points - 25
                    ELSE 0
                END
            WHERE id = ?
            """,
            (session["id"],),
        )

        con.commit()
        con.close()

        await query.edit_message_text(
            "⚠️ НЕПРАВИЛЬНЫЙ СЛЕД\n\n"
            "Ты выбрал неверное направление.\n\n"
            "Это не конец расследования, но ошибка будет учтена "
            "в итоговом результате.\n\n"
            f"⏱ Осталось: {format_time(time_left(session))}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➡️ ПРОДОЛЖИТЬ",
                        callback_data=f"continue:{episode_number + 1}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗂 ДОСЬЕ",
                        callback_data="dossier"
                    )
                ],
            ]),
        )


# ============================================================
# DOSSIER
# ============================================================

async def show_dossier(query):
    user_id = query.from_user.id
    session = get_active_session(user_id)

    if not session:
        await query.edit_message_text(
            "🗂 ДОСЬЕ\n\n"
            "У тебя пока нет активного расследования.",
            reply_markup=back_button(),
        )
        return

    con = db()

    clues = con.execute(
        """
        SELECT clue_id
        FROM player_clues
        WHERE session_id = ?
        ORDER BY clue_id
        """,
        (session["id"],),
    ).fetchall()

    con.close()

    if not clues:
        clue_text = "Пока нет найденных улик."
    else:
        clue_text = "\n".join(
            f"🔎 {EPISODES[row['clue_id'] - 1]['clue']}"
            for row in clues
            if 1 <= row["clue_id"] <= len(EPISODES)
        )

    text = (
        "╔════════════════════╗\n"
        "          🗂 ДОСЬЕ\n"
        "╚════════════════════╝\n\n"
        f"Дело №001\n"
        f"Эпизод: {session['current_episode']}/20\n"
        f"⏱ Осталось: {format_time(time_left(session))}\n\n"
        "🔎 НАЙДЕННЫЕ УЛИКИ\n\n"
        f"{clue_text}"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➡️ К РАССЛЕДОВАНИЮ",
                    callback_data=f"continue:{session['current_episode']}",
                )
            ],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="space")],
        ]),
    )


# ============================================================
# INTERACTION
# ============================================================

async def interaction_menu(query):
    await query.edit_message_text(
        "👥 ВЗАИМОДЕЙСТВИЕ\n\n"
        "Ты можешь продолжить расследование самостоятельно "
        "или попробовать найти другого игрока.\n\n"
        "Взаимодействие может помочь сопоставить версии и улики.\n\n"
        "Но окончательное решение всегда остаётся за тобой.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔎 ПРОЙТИ САМОМУ",
                    callback_data="solo",
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 НАЙТИ ИГРОКА",
                    callback_data="join_search",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 НАЗАД",
                    callback_data="main",
                )
            ],
        ]),
    )


async def set_interaction_mode(query, mode):
    user_id = query.from_user.id
    session = get_active_session(user_id)

    if not session:
        await query.edit_message_text(
            "Нет активного расследования.",
            reply_markup=main_keyboard(),
        )
        return

    con = db()
    con.execute(
        """
        UPDATE game_sessions
        SET interaction_mode = ?
        WHERE id = ?
        """,
        (mode, session["id"]),
    )
    con.commit()
    con.close()

    await query.edit_message_text(
        "🔎 Ты выбрал одиночное расследование.\n\n"
        "Продолжай искать улики самостоятельно.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➡️ ПРОДОЛЖИТЬ",
                    callback_data=f"continue:{session['current_episode']}",
                )
            ]
        ]),
    )


async def find_interaction_partner(query):
    user_id = query.from_user.id

    con = db()

    players = con.execute(
        """
        SELECT u.telegram_id, u.first_name, u.username
        FROM users u
        JOIN game_sessions s
          ON s.telegram_id = u.telegram_id
        WHERE s.case_id = ?
          AND s.finished = 0
          AND s.telegram_id != ?
        ORDER BY s.id DESC
        LIMIT 10
        """,
        (CASE_ID, user_id),
    ).fetchall()

    con.close()

    if not players:
        await query.edit_message_text(
            "👥 ПОКА НИКТО НЕ ДОСТУПЕН\n\n"
            "Сейчас нет другого активного игрока.\n\n"
            "Можешь продолжить самостоятельно.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 ПРОЙТИ САМОМУ",
                        callback_data="solo",
                    )
                ]
            ]),
        )
        return

    buttons = []

    for player in players:
        name = player["first_name"] or player["username"] or "Игрок"

        buttons.append([
            InlineKeyboardButton(
                f"👤 {name}",
                callback_data=f"choose_partner:{player['telegram_id']}",
            )
        ])

    await query.edit_message_text(
        "👥 АКТИВНЫЕ ИГРОКИ\n\n"
        "Выбери игрока для взаимодействия.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def choose_partner(query, partner_id):
    user_id = query.from_user.id

    if partner_id == user_id:
        return

    con = db()

    session = con.execute(
        """
        SELECT id
        FROM game_sessions
        WHERE telegram_id = ?
          AND case_id = ?
          AND finished = 0
        LIMIT 1
        """,
        (user_id, CASE_ID),
    ).fetchone()

    if not session:
        con.close()
        await query.edit_message_text(
            "Нет активного расследования.",
            reply_markup=main_keyboard(),
        )
        return

    con.execute(
        """
        UPDATE game_sessions
        SET interaction_mode = ?
        WHERE id = ?
        """,
        (f"partner:{partner_id}", session["id"]),
    )

    con.commit()
    con.close()

    await query.edit_message_text(
        "🤝 ЗАПРОС НА ВЗАИМОДЕЙСТВИЕ\n\n"
        "Игрок выбран.\n\n"
        "Ты можешь продолжить расследование, "
        "а взаимодействие будет использоваться как социальный слой игры.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➡️ ПРОДОЛЖИТЬ",
                    callback_data=f"continue:{get_active_session(user_id)['current_episode']}",
                )
            ]
        ]),
    )


# ============================================================
# FINAL
# ============================================================

async def final_decision(query, session):
    await query.edit_message_text(
        "╔════════════════════╗\n"
        "       ФИНАЛЬНОЕ РЕШЕНИЕ\n"
        "╚════════════════════╝\n\n"
        "Ты собрал достаточно информации.\n\n"
        "Теперь нужно соединить все найденные следы.\n\n"
        "Ключ.\n"
        "Билет.\n"
        "Шесть минут.\n"
        "Технический проход.\n"
        "Ложный след.\n"
        "Человек с двумя личностями.\n\n"
        "Выбери финальный вывод.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🧩 СОПОСТАВИТЬ ВСЮ ЦЕПОЧКУ",
                    callback_data="answer:20:A",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔎 ПРОВЕРИТЬ УЛИКИ",
                    callback_data="dossier",
                )
            ],
        ]),
    )


async def finish_game(user_id, session_id, query):
    con = db()

    session = con.execute(
        "SELECT * FROM game_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()

    if not session:
        con.close()
        return

    if session["finished"]:
        con.close()
        return

    elapsed = elapsed_seconds(session)

    success = (
        session["correct_answers"] >= 16
        and session["mistakes"] <= 4
    )

    points = session["points"]

    if success:
        points += max(0, 3600 - elapsed) // 10

    con.execute(
        """
        UPDATE game_sessions
        SET finished = 1,
            success = ?,
            finished_at = ?,
            points = ?
        WHERE id = ?
        """,
        (
            1 if success else 0,
            now_iso(),
            points,
            session_id,
        ),
    )

    con.execute(
        """
        INSERT INTO game_results(
            telegram_id,
            case_id,
            session_id,
            time_seconds,
            points,
            mistakes,
            clues_found,
            correct_answers,
            finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            CASE_ID,
            session_id,
            elapsed,
            points,
            session["mistakes"],
            session["clues_found"],
            session["correct_answers"],
            now_iso(),
        ),
    )

    con.execute(
        """
        UPDATE users
        SET completed_games = completed_games + 1,
            total_points = total_points + ?,
            best_time =
                CASE
                    WHEN best_time IS NULL THEN ?
                    WHEN ? < best_time THEN ?
                    ELSE best_time
                END
        WHERE telegram_id = ?
        """,
        (
            points,
            elapsed,
            elapsed,
            elapsed,
            user_id,
        ),
    )

    con.commit()
    con.close()

    await give_achievements(user_id, session, elapsed, success)

    result = "ДЕЛО РАСКРЫТО" if success else "РАССЛЕДОВАНИЕ НЕ ЗАВЕРШЕНО"

    await query.edit_message_text(
        "╔════════════════════╗\n"
        "       🏁 РЕЗУЛЬТАТ\n"
        "╚════════════════════╝\n\n"
        f"{result}\n\n"
        f"⏱ Время: {format_time(elapsed)}\n"
        f"🔎 Улик: {session['clues_found']}/20\n"
        f"🎯 Правильных решений: {session['correct_answers']}/20\n"
        f"❌ Ошибок: {session['mistakes']}\n"
        f"⭐ Очки: {points}\n\n"
        "Результат сохранён.\n"
        "Следующий раунд начнётся как новая игра.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏆 РЕЙТИНГ", callback_data="rating")],
            [InlineKeyboardButton("👤 МОЁ ПРОСТРАНСТВО", callback_data="space")],
            [InlineKeyboardButton("🔙 МЕНЮ", callback_data="main")],
        ]),
    )


# ============================================================
# SPACE
# ============================================================

async def show_space(query):
    await query.edit_message_text(
        "╔════════════════════╗\n"
        "      👤 МОЁ ПРОСТРАНСТВО\n"
        "╚════════════════════╝\n\n"
        "Твоё личное пространство в OHOTA GAME.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 ПРОФИЛЬ", callback_data="profile")],
            [InlineKeyboardButton("🔎 РАССЛЕДОВАНИЕ", callback_data="dossier")],
            [InlineKeyboardButton("🏆 МОИ РЕЗУЛЬТАТЫ", callback_data="results")],
            [InlineKeyboardButton("🎖 ДОСТИЖЕНИЯ", callback_data="achievements")],
            [InlineKeyboardButton("🛟 ПОДДЕРЖКА", callback_data="support")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="main")],
        ]),
    )


async def show_profile(query):
    user = get_user(query.from_user.id)

    if not user:
        await query.edit_message_text(
            "Профиль не найден.",
            reply_markup=back_button(),
        )
        return

    rank = get_rank(query.from_user.id)

    name = user["first_name"] or "Игрок"
    username = (
        f"@{user['username']}"
        if user["username"]
        else "не указан"
    )

    best = (
        format_time(user["best_time"])
        if user["best_time"]
        else "—"
    )

    await query.edit_message_text(
        "╔════════════════════╗\n"
        "          👤 ПРОФИЛЬ\n"
        "╚════════════════════╝\n\n"
        f"Имя: {name}\n"
        f"Username: {username}\n\n"
        f"🎮 Игр: {user['total_games']}\n"
        f"🏁 Завершено: {user['completed_games']}\n"
        f"⭐ Очков: {user['total_points']}\n"
        f"⚡ Лучшее время: {best}\n"
        f"🏆 Место: {rank}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="space")]
        ]),
    )


async def show_results(query):
    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM game_results
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (query.from_user.id,),
    ).fetchall()

    con.close()

    if not rows:
        text = (
            "🏆 МОИ РЕЗУЛЬТАТЫ\n\n"
            "Пока нет завершённых раундов."
        )
    else:
        lines = ["🏆 МОИ РЕЗУЛЬТАТЫ\n"]

        for index, row in enumerate(rows, 1):
            status = "✅" if row["correct_answers"] >= 16 else "❌"

            lines.append(
                f"{index}. {status} Дело №{row['case_id']:03d}\n"
                f"   ⏱ {format_time(row['time_seconds'])}\n"
                f"   ⭐ {row['points']} очков\n"
                f"   🎯 {row['correct_answers']}/20\n"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="space")]
        ]),
    )


async def show_achievements(query):
    con = db()

    rows = con.execute(
        """
        SELECT a.title, a.description
        FROM player_achievements pa
        JOIN achievements a
          ON a.id = pa.achievement_id
        WHERE pa.telegram_id = ?
        ORDER BY pa.created_at DESC
        """,
        (query.from_user.id,),
    ).fetchall()

    con.close()

    if not rows:
        text = (
            "🎖 ДОСТИЖЕНИЯ\n\n"
            "Пока нет полученных достижений.\n\n"
            "Первое дело уже ждёт."
        )
    else:
        text = "🎖 ДОСТИЖЕНИЯ\n\n"

        for row in rows:
            text += (
                f"{row['title']}\n"
                f"{row['description']}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="space")]
        ]),
    )


# ============================================================
# RATING
# ============================================================

def get_rank(user_id):
    con = db()

    rows = con.execute(
        """
        SELECT telegram_id, total_points
        FROM users
        ORDER BY total_points DESC, completed_games DESC
        """
    ).fetchall()

    con.close()

    for index, row in enumerate(rows, 1):
        if row["telegram_id"] == user_id:
            return index

    return "—"


async def show_rating(query):
    con = db()

    rows = con.execute(
        """
        SELECT first_name, username, total_points
        FROM users
        ORDER BY total_points DESC, completed_games DESC
        LIMIT 10
        """
    ).fetchall()

    con.close()

    text = "╔════════════════════╗\n"
    text += "          🏆 РЕЙТИНГ\n"
    text += "╚════════════════════╝\n\n"

    if not rows:
        text += "Пока здесь никого нет."
    else:
        medals = ["🥇", "🥈", "🥉"]

        for index, row in enumerate(rows, 1):
            name = (
                f"@{row['username']}"
                if row["username"]
                else row["first_name"] or "Игрок"
            )

            medal = medals[index - 1] if index <= 3 else f"{index}."

            text += (
                f"{medal} {name} — "
                f"{row['total_points']} ⭐\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="main")]
        ]),
    )


# ============================================================
# RULES
# ============================================================

async def show_rules(query):
    await query.edit_message_text(
        "📜 ПРАВИЛА OHOTA GAME\n\n"
        "1. Каждый раунд длится максимум 60 минут.\n\n"
        "2. Игрок проходит последовательный сюжет из эпизодов.\n\n"
        "3. Улики необходимо сопоставлять между собой.\n\n"
        "4. Некоторые следы могут оказаться ложными.\n\n"
        "5. Можно проходить расследование одному или взаимодействовать "
        "с другими игроками.\n\n"
        "6. Правильность решений влияет на результат.\n\n"
        "7. Скорость также имеет значение.\n\n"
        "8. После завершения раунда результат сохраняется в истории.\n\n"
        "9. После окончания 60 минут продолжить текущий раунд нельзя.\n\n"
        "10. Использование багов для получения преимущества запрещено.",
        reply_markup=back_button(),
    )


# ============================================================
# SUPPORT
# ============================================================

async def show_support(query):
    await query.edit_message_text(
        "╔════════════════════╗\n"
        "         🛟 ПОДДЕРЖКА\n"
        "╚════════════════════╝\n\n"
        "Что произошло?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔴 Кнопка не работает",
                    callback_data="support_cat:button",
                )
            ],
            [
                InlineKeyboardButton(
                    "🐞 Нашёл ошибку",
                    callback_data="support_cat:bug",
                )
            ],
            [
                InlineKeyboardButton(
                    "⏱ Проблема с таймером",
                    callback_data="support_cat:timer",
                )
            ],
            [
                InlineKeyboardButton(
                    "🧩 Проблема с уликой",
                    callback_data="support_cat:clue",
                )
            ],
            [
                InlineKeyboardButton(
                    "💬 Связаться с поддержкой",
                    callback_data="support_cat:contact",
                )
            ],
            [
                InlineKeyboardButton(
                    "✍️ Другое",
                    callback_data="support_cat:other",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 НАЗАД",
                    callback_data="main",
                )
            ],
        ]),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)

    text = update.message.text.strip()

    # Ответ администратора пользователю
    if is_admin(user.id) and context.user_data.get("reply_ticket"):
        ticket_id = context.user_data.pop("reply_ticket")

        con = db()

        ticket = con.execute(
            "SELECT telegram_id FROM support_tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()

        if not ticket:
            con.close()
            await update.message.reply_text("Обращение не найдено.")
            return

        con.execute(
            """
            INSERT INTO support_messages(
                ticket_id, sender_id, message, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (ticket_id, user.id, text, now_iso()),
        )

        con.execute(
            """
            UPDATE support_tickets
            SET status = 'IN_PROGRESS'
            WHERE id = ?
            """,
            (ticket_id,),
        )

        con.commit()
        con.close()

        await context.bot.send_message(
            chat_id=ticket["telegram_id"],
            text=(
                "🛟 ПОДДЕРЖКА\n\n"
                f"Ответ по обращению №{ticket_id}:\n\n"
                f"{text}"
            ),
        )

        await update.message.reply_text(
            "✅ Ответ отправлен пользователю."
        )
        return

    # Добавление beta tester
    if is_admin(user.id) and context.user_data.get("awaiting_beta"):
        context.user_data.pop("awaiting_beta")

        if not text.isdigit():
            await update.message.reply_text(
                "❌ Telegram ID должен быть числом."
            )
            return

        tester_id = int(text)

        con = db()
        con.execute(
            """
            INSERT INTO beta_testers(
                telegram_id,
                added_at,
                active
            )
            VALUES (?, ?, 1)
            ON CONFLICT(telegram_id)
            DO UPDATE SET active = 1
            """,
            (tester_id, now_iso()),
        )
        con.commit()
        con.close()

        await update.message.reply_text(
            f"🧪 Тестер {tester_id} добавлен."
        )
        return

    # Support
    if context.user_data.get("awaiting_support"):
        category = context.user_data.pop(
            "support_category",
            "other",
        )
        context.user_data.pop("awaiting_support", None)

        session = get_active_session(user.id)

        episode = session["current_episode"] if session else None
        game_time = elapsed_seconds(session) if session else 0

        con = db()

        cur = con.execute(
            """
            INSERT INTO support_tickets(
                telegram_id,
                username,
                category,
                message,
                case_id,
                episode,
                game_time,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
            """,
            (
                user.id,
                user.username,
                category,
                text,
                CASE_ID if session else None,
                episode,
                game_time,
                now_iso(),
            ),
        )

        ticket_id = cur.lastrowid

        con.commit()
        con.close()

        # Уведомление администраторам
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "🛟 НОВОЕ ОБРАЩЕНИЕ\n\n"
                        f"№{ticket_id}\n"
                        f"Игрок: @{user.username or 'без username'}\n"
                        f"ID: {user.id}\n"
                        f"Категория: {category}\n"
                        f"Эпизод: {episode or '—'}\n"
                        f"Время игры: {format_time(game_time)}\n\n"
                        f"{text}"
                    ),
                )
            except Exception:
                logger.exception(
                    "Не удалось уведомить администратора %s",
                    admin_id,
                )

        await update.message.reply_text(
            "✅ ОБРАЩЕНИЕ ПРИНЯТО\n\n"
            f"Номер обращения: #{ticket_id}\n\n"
            "Сообщение сохранено.\n"
            "Поддержка ответит тебе в Telegram."
        )
        return

    await update.message.reply_text(
        "Используй кнопки меню.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# SUPPORT ADMIN
# ============================================================

async def show_admin(query):
    if not is_admin(query.from_user.id):
        await query.edit_message_text(
            "⛔ Доступ запрещён.",
            reply_markup=back_button(),
        )
        return

    await query.edit_message_text(
        "╔════════════════════╗\n"
        "      🔐 АДМИН-ПРОСТРАНСТВО\n"
        "╚════════════════════╝\n\n"
        "Управление OHOTA GAME.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛟 ПОДДЕРЖКА", callback_data="tickets")],
            [InlineKeyboardButton("🧪 БЕТА-ТЕСТ", callback_data="beta")],
            [InlineKeyboardButton("📷 МЕДИА", callback_data="media")],
            [InlineKeyboardButton("🏆 РЕЙТИНГ", callback_data="rating")],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data="main")],
        ]),
    )


async def show_tickets(query):
    if not is_admin(query.from_user.id):
        return

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM support_tickets
        ORDER BY
            CASE status
                WHEN 'NEW' THEN 1
                WHEN 'IN_PROGRESS' THEN 2
                ELSE 3
            END,
            id DESC
        LIMIT 30
        """
    ).fetchall()

    con.close()

    buttons = []

    for row in rows:
        icon = {
            "NEW": "🔴",
            "IN_PROGRESS": "🟡",
            "RESOLVED": "🟢",
        }.get(row["status"], "⚪")

        name = row["username"] or str(row["telegram_id"])

        buttons.append([
            InlineKeyboardButton(
                f"{icon} #{row['id']} @{name}",
                callback_data=f"ticket:{row['id']}",
            )
        ])

    if not buttons:
        text = "🛟 ПОДДЕРЖКА\n\nОбращений пока нет."
    else:
        text = "🛟 ПОДДЕРЖКА\n\nВыбери обращение."

    buttons.append([
        InlineKeyboardButton("🔙 АДМИН-ПАНЕЛЬ", callback_data="admin")
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_ticket(query, ticket_id):
    if not is_admin(query.from_user.id):
        return

    con = db()

    ticket = con.execute(
        "SELECT * FROM support_tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()

    messages = con.execute(
        """
        SELECT *
        FROM support_messages
        WHERE ticket_id = ?
        ORDER BY id
        """,
        (ticket_id,),
    ).fetchall()

    con.close()

    if not ticket:
        await query.edit_message_text(
            "Обращение не найдено.",
            reply_markup=back_button(),
        )
        return

    text = (
        f"🛟 ОБРАЩЕНИЕ #{ticket['id']}\n\n"
        f"Игрок: @{ticket['username'] or 'без username'}\n"
        f"ID: {ticket['telegram_id']}\n"
        f"Категория: {ticket['category']}\n"
        f"Дело: {ticket['case_id'] or '—'}\n"
        f"Эпизод: {ticket['episode'] or '—'}\n"
        f"Время игры: {format_time(ticket['game_time'] or 0)}\n"
        f"Статус: {ticket['status']}\n\n"
        f"Сообщение:\n{ticket['message']}\n"
    )

    if messages:
        text += "\nИстория:\n"

        for message in messages[-5:]:
            text += (
                f"\n— {message['message']}\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩️ ОТВЕТИТЬ",
                    callback_data=f"ticket_reply:{ticket_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🟡 В РАБОТУ",
                    callback_data=f"ticket_status:{ticket_id}:IN_PROGRESS",
                ),
                InlineKeyboardButton(
                    "🟢 РЕШЕНО",
                    callback_data=f"ticket_status:{ticket_id}:RESOLVED",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔙 НАЗАД",
                    callback_data="tickets",
                )
            ],
        ]),
    )


async def change_ticket_status(query, ticket_id, status):
    if not is_admin(query.from_user.id):
        return

    con = db()

    con.execute(
        """
        UPDATE support_tickets
        SET status = ?
        WHERE id = ?
        """,
        (status, ticket_id),
    )

    con.commit()
    con.close()

    await show_ticket(query, ticket_id)


# ============================================================
# BETA ADMIN
# ============================================================

async def show_beta_admin(query):
    if not is_admin(query.from_user.id):
        return

    await query.edit_message_text(
        "🧪 БЕТА-ТЕСТ\n\n"
        "Управление тестерами и тестовыми результатами.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ ДОБАВИТЬ ТЕСТЕРА",
                    callback_data="add_beta",
                )
            ],
            [
                InlineKeyboardButton(
                    "👥 ТЕСТЕРЫ",
                    callback_data="beta_list",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 РЕЗУЛЬТАТЫ",
                    callback_data="beta_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 АДМИН-ПАНЕЛЬ",
                    callback_data="admin",
                )
            ],
        ]),
    )


async def show_beta_list(query):
    if not is_admin(query.from_user.id):
        return

    con = db()

    rows = con.execute(
        """
        SELECT telegram_id, added_at
        FROM beta_testers
        WHERE active = 1
        ORDER BY added_at DESC
        """
    ).fetchall()

    con.close()

    if not rows:
        text = "🧪 ТЕСТЕРЫ\n\nСписок пуст."
    else:
        text = "🧪 ТЕСТЕРЫ\n\n"

        for row in rows:
            text += (
                f"👤 {row['telegram_id']}\n"
                f"Добавлен: {row['added_at']}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 НАЗАД",
                    callback_data="beta",
                )
            ]
        ]),
    )


async def show_beta_stats(query):
    if not is_admin(query.from_user.id):
        return

    con = db()

    testers = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM beta_testers
        WHERE active = 1
        """
    ).fetchone()["count"]

    results = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM game_results gr
        JOIN beta_testers bt
          ON bt.telegram_id = gr.telegram_id
        WHERE bt.active = 1
        """
    ).fetchone()["count"]

    con.close()

    await query.edit_message_text(
        "📊 БЕТА-СТАТИСТИКА\n\n"
        f"Активных тестеров: {testers}\n"
        f"Тестовых результатов: {results}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 НАЗАД",
                    callback_data="beta",
                )
            ]
        ]),
    )


# ============================================================
# MEDIA
# ============================================================

def get_episode_media(episode):
    con = db()

    row = con.execute(
        """
        SELECT file_id
        FROM episode_media
        WHERE episode = ?
        """,
        (episode,),
    ).fetchone()

    con.close()

    return row["file_id"] if row else None


async def show_media_admin(query):
    if not is_admin(query.from_user.id):
        return

    await query.edit_message_text(
        "📷 МЕДИА\n\n"
        "Для эпизодов можно сохранять Telegram file_id "
        "и использовать изображения повторно.\n\n"
        "Текущий код хранит привязку:\n"
        "эпизод → file_id\n\n"
        "Это позволяет добавлять атмосферные материалы "
        "к каждому эпизоду без изменения логики игры.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 АДМИН-ПАНЕЛЬ",
                    callback_data="admin",
                )
            ]
        ]),
    )


# ============================================================
# ACHIEVEMENTS
# ============================================================

async def give_achievements(user_id, session, elapsed, success):
    achievements = []

    if session["clues_found"] >= 1:
        achievements.append("first_clue")

    if success:
        achievements.append("first_case")

    if session["mistakes"] == 0 and success:
        achievements.append("no_mistakes")

    if elapsed < 45 * 60 and success:
        achievements.append("speed")

    if (
        session["correct_answers"] == 20
        and session["mistakes"] == 0
        and success
    ):
        achievements.append("perfect")

    if is_beta(user_id):
        achievements.append("beta")

    con = db()

    for code in achievements:
        achievement = con.execute(
            """
            SELECT id
            FROM achievements
            WHERE code = ?
            """,
            (code,),
        ).fetchone()

        if achievement:
            con.execute(
                """
                INSERT OR IGNORE INTO player_achievements(
                    telegram_id,
                    achievement_id,
                    created_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    achievement["id"],
                    now_iso(),
                ),
            )

    con.commit()
    con.close()


# ============================================================
# /ADMIN
# ============================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Доступ запрещён."
        )
        return

    await update.message.reply_text(
        "🔐 АДМИН-ПАНЕЛЬ",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛟 ПОДДЕРЖКА", callback_data="tickets")],
            [InlineKeyboardButton("🧪 БЕТА-ТЕСТ", callback_data="beta")],
            [InlineKeyboardButton("📷 МЕДИА", callback_data="media")],
            [InlineKeyboardButton("🏆 РЕЙТИНГ", callback_data="rating")],
        ]),
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):
    logger.error(
        "Exception while handling update:",
        exc_info=context.error,
    )


# ============================================================
# APPLICATION
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден. Добавь BOT_TOKEN в Secrets/Environment Variables."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("OHOTA GAME started.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()