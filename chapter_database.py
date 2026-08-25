import sqlite3
from pathlib import Path
from typing import Optional


class ChapterDatabase:
    def __init__(self, database_path: str = "ohota.db"):
        self.database_path = Path(database_path)
        self._create_tables()

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _create_tables(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_number INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chapter_scenes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter_id INTEGER NOT NULL,
                    scene_key TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    text TEXT NOT NULL,
                    is_start INTEGER DEFAULT 0,
                    is_finish INTEGER DEFAULT 0,
                    UNIQUE(chapter_id, scene_key),
                    FOREIGN KEY(chapter_id) REFERENCES chapters(id)
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scene_choices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scene_id INTEGER NOT NULL,
                    choice_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    next_scene_key TEXT NOT NULL,
                    points INTEGER DEFAULT 0,
                    FOREIGN KEY(scene_id) REFERENCES chapter_scenes(id)
                )
                """
            )

            connection.commit()

    def create_chapter(
        self,
        chapter_number: int,
        title: str,
        description: str = "",
    ):
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chapters
                    (chapter_number, title, description)
                VALUES (?, ?, ?)
                """,
                (
                    chapter_number,
                    title,
                    description,
                ),
            )

            connection.commit()
            return cursor.lastrowid

    def get_chapter(
        self,
        chapter_number: int,
    ) -> Optional[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM chapters
                WHERE chapter_number = ?
                """,
                (chapter_number,),
            ).fetchone()

    def get_all_chapters(self):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM chapters
                ORDER BY chapter_number
                """
            ).fetchall()

    def create_scene(
        self,
        chapter_id: int,
        scene_key: str,
        text: str,
        title: str = "",
        is_start: bool = False,
        is_finish: bool = False,
    ):
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chapter_scenes
                    (
                        chapter_id,
                        scene_key,
                        title,
                        text,
                        is_start,
                        is_finish
                    )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter_id,
                    scene_key,
                    title,
                    text,
                    int(is_start),
                    int(is_finish),
                ),
            )

            connection.commit()
            return cursor.lastrowid

    def create_choice(
        self,
        scene_id: int,
        choice_key: str,
        title: str,
        next_scene_key: str,
        points: int = 0,
    ):
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scene_choices
                    (
                        scene_id,
                        choice_key,
                        title,
                        next_scene_key,
                        points
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    scene_id,
                    choice_key,
                    title,
                    next_scene_key,
                    points,
                ),
            )

            connection.commit()
            return cursor.lastrowid

    def get_scene(
        self,
        chapter_id: int,
        scene_key: str,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM chapter_scenes
                WHERE chapter_id = ?
                  AND scene_key = ?
                """,
                (
                    chapter_id,
                    scene_key,
                ),
            ).fetchone()

    def get_scene_choices(
        self,
        scene_id: int,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM scene_choices
                WHERE scene_id = ?
                ORDER BY id
                """,
                (scene_id,),
            ).fetchall()

    def get_start_scene(
        self,
        chapter_id: int,
    ):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM chapter_scenes
                WHERE chapter_id = ?
                  AND is_start = 1
                LIMIT 1
                """,
                (chapter_id,),
            ).fetchone()