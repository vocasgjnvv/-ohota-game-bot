import sqlite3
from pathlib import Path
from typing import Optional


class QuestDatabase:
    def __init__(self, database_path: str = "ohota.db"):
        self.database_path = Path(database_path)
        self._create_tables()

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _create_tables(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS quest_missions (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS quest_scenes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    is_start INTEGER DEFAULT 0,
                    is_finish INTEGER DEFAULT 0,

                    UNIQUE(mission_id, scene_id),

                    FOREIGN KEY (mission_id)
                        REFERENCES quest_missions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS quest_choices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    choice_text TEXT NOT NULL,
                    next_scene_id TEXT,
                    points INTEGER DEFAULT 0,

                    FOREIGN KEY (mission_id)
                        REFERENCES quest_missions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS quest_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    next_scene_id TEXT,
                    points INTEGER DEFAULT 0,

                    FOREIGN KEY (mission_id)
                        REFERENCES quest_missions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS quest_progress (
                    telegram_id INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    completed INTEGER DEFAULT 0,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    PRIMARY KEY (telegram_id, mission_id),

                    FOREIGN KEY (mission_id)
                        REFERENCES quest_missions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS quest_actions (
                    telegram_id INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    action_id TEXT NOT NULL,

                    PRIMARY KEY (
                        telegram_id,
                        mission_id,
                        action_id
                    ),

                    FOREIGN KEY (mission_id)
                        REFERENCES quest_missions(id)
                        ON DELETE CASCADE
                );
                                 

                CREATE TABLE IF NOT EXISTS hunts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    registration_start TIMESTAMP NOT NULL,
                    registration_end TIMESTAMP NOT NULL,
                    hunt_start TIMESTAMP NOT NULL,
                    prize_end TIMESTAMP NOT NULL,
                    hard_close TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'scheduled',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS hunt_participants (
                    hunt_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    registered_at TIMESTAMP,
                    prize_eligible INTEGER DEFAULT 0,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    completed INTEGER DEFAULT 0,
                    score INTEGER DEFAULT 0,
                    place INTEGER,

                    PRIMARY KEY (hunt_id, telegram_id),

                    FOREIGN KEY (hunt_id)
                        REFERENCES hunts(id)
                        ON DELETE CASCADE
                );
                """
            )

            connection.commit()

    # =========================================================
    # МИССИИ
    # =========================================================

    def create_mission(
        self,
        mission_id: int,
        title: str,
        description: str = "",
    ):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quest_missions
                    (id, title, description)
                VALUES (?, ?, ?)
                """,
                (
                    mission_id,
                    title,
                    description,
                ),
            )

            connection.commit()

    def get_mission(
        self,
        mission_id: int,
    ) -> Optional[sqlite3.Row]:

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_missions
                WHERE id = ?
                """,
                (mission_id,),
            ).fetchone()

    # =========================================================
    # СЦЕНЫ
    # =========================================================

    def add_scene(
        self,
        mission_id: int,
        scene_id: str,
        text: str,
        is_start: bool = False,
        is_finish: bool = False,
    ):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quest_scenes
                    (
                        mission_id,
                        scene_id,
                        text,
                        is_start,
                        is_finish
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    scene_id,
                    text,
                    int(is_start),
                    int(is_finish),
                ),
            )

            connection.commit()

    def get_scene(
        self,
        mission_id: int,
        scene_id: str,
    ) -> Optional[sqlite3.Row]:

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_scenes
                WHERE mission_id = ?
                  AND scene_id = ?
                """,
                (
                    mission_id,
                    scene_id,
                ),
            ).fetchone()

    def get_start_scene(
        self,
        mission_id: int,
    ) -> Optional[sqlite3.Row]:

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_scenes
                WHERE mission_id = ?
                  AND is_start = 1
                LIMIT 1
                """,
                (mission_id,),
            ).fetchone()

    # =========================================================
    # ВЫБОРЫ
    # =========================================================

    def add_choice(
        self,
        mission_id: int,
        scene_id: str,
        choice_text: str,
        next_scene_id: Optional[str],
        points: int = 0,
    ):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quest_choices
                    (
                        mission_id,
                        scene_id,
                        choice_text,
                        next_scene_id,
                        points
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    scene_id,
                    choice_text,
                    next_scene_id,
                    points,
                ),
            )

            connection.commit()

    def get_choices(
        self,
        mission_id: int,
        scene_id: str,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_choices
                WHERE mission_id = ?
                  AND scene_id = ?
                ORDER BY id
                """,
                (
                    mission_id,
                    scene_id,
                ),
            ).fetchall()

    # =========================================================
    # СВОБОДНЫЕ ОТВЕТЫ
    # =========================================================

    def add_answer(
        self,
        mission_id: int,
        scene_id: str,
        answer: str,
        next_scene_id: Optional[str],
        points: int = 0,
    ):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quest_answers
                    (
                        mission_id,
                        scene_id,
                        answer,
                        next_scene_id,
                        points
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    scene_id,
                    answer.strip().lower(),
                    next_scene_id,
                    points,
                ),
            )

            connection.commit()

    def get_answers(
        self,
        mission_id: int,
        scene_id: str,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_answers
                WHERE mission_id = ?
                  AND scene_id = ?
                ORDER BY id
                """,
                (
                    mission_id,
                    scene_id,
                ),
            ).fetchall()

    # =========================================================
    # ПРОГРЕСС ИГРОКА
    # =========================================================

    def start_player(
        self,
        telegram_id: int,
        mission_id: int,
        scene_id: str,
    ):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO quest_progress
                    (
                        telegram_id,
                        mission_id,
                        scene_id,
                        score,
                        completed
                    )
                VALUES (?, ?, ?, 0, 0)
                ON CONFLICT(telegram_id, mission_id)
                DO UPDATE SET
                    scene_id = excluded.scene_id,
                    score = 0,
                    completed = 0,
                    started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    telegram_id,
                    mission_id,
                    scene_id,
                ),
            )

            connection.commit()

    def get_progress(
        self,
        telegram_id: int,
        mission_id: int,
    ) -> Optional[sqlite3.Row]:

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_progress
                WHERE telegram_id = ?
                  AND mission_id = ?
                """,
                (
                    telegram_id,
                    mission_id,
                ),
            ).fetchone()

    def update_progress(
        self,
        telegram_id: int,
        mission_id: int,
        scene_id: str,
        score: int,
        completed: bool = False,
    ):
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE quest_progress
                SET
                    scene_id = ?,
                    score = ?,
                    completed = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                  AND mission_id = ?
                """,
                (
                    scene_id,
                    score,
                    int(completed),
                    telegram_id,
                    mission_id,
                ),
            )

            connection.commit()

    # =========================================================
    # ЗАЩИТА ОТ ПОВТОРНОГО ПОЛУЧЕНИЯ ОЧКОВ
    # =========================================================

    def action_completed(
        self,
        telegram_id: int,
        mission_id: int,
        action_id: str,
    ) -> bool:

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM quest_actions
                WHERE telegram_id = ?
                  AND mission_id = ?
                  AND action_id = ?
                LIMIT 1
                """,
                (
                    telegram_id,
                    mission_id,
                    action_id,
                ),
            ).fetchone()

            return row is not None

    def save_action(
        self,
        telegram_id: int,
        mission_id: int,
        action_id: str,
    ) -> bool:

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO quest_actions
                    (
                        telegram_id,
                        mission_id,
                        action_id
                    )
                VALUES (?, ?, ?)
                """,
                (
                    telegram_id,
                    mission_id,
                    action_id,
                ),
            )

            connection.commit()

            return cursor.rowcount > 0

    # =========================================================
    # СПИСОК МИССИЙ
    # =========================================================

    def get_all_missions(self):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM quest_missions
                ORDER BY id
                """
            ).fetchall()

    def get_player_missions(
        self,
        telegram_id: int,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT
                    m.id,
                    m.title,
                    m.description,
                    m.is_active,
                    p.scene_id,
                    p.score,
                    p.completed
                FROM quest_missions m
                LEFT JOIN quest_progress p
                    ON p.mission_id = m.id
                   AND p.telegram_id = ?
                ORDER BY m.id
                """,
                (telegram_id,),
            ).fetchall()