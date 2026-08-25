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

            connection.commit()

    def create_chapter(
        self,
        chapter_number: int,
        title: str,
        description: str = "",
    ):
        with self._connect() as connection:
            connection.execute(
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