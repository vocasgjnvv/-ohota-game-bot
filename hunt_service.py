from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Optional, Dict, Any


class HuntService:
    """
    Управление соревнованием ОХОТА.

    Логика:
    1. Регистрация открыта между registration_start и registration_end.
    2. Играть можно с hunt_start.
    3. Только зарегистрированные до окончания регистрации имеют право на приз.
    4. После prize_end новые призовые места не формируются.
    5. Зарегистрированные игроки могут продолжать игру до hard_close.
    6. После hard_close охота полностью закрыта.
    """

    STATUS_SCHEDULED = "scheduled"
    STATUS_REGISTRATION = "registration"
    STATUS_ACTIVE = "active"
    STATUS_PRIZE_FINISHED = "prize_finished"
    STATUS_CLOSED = "closed"

    def __init__(self, database_path: str = "ohota.db"):
        self.database_path = Path(database_path)

    # =========================================================
    # DATABASE
    # =========================================================

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    # =========================================================
    # TIME
    # =========================================================

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_time(value) -> datetime:
        if isinstance(value, datetime):
            result = value
        else:
            text = str(value).strip()

            if text.endswith("Z"):
                text = text[:-1] + "+00:00"

            result = datetime.fromisoformat(text)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result

    @staticmethod
    def _db_time(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc).isoformat()

    # =========================================================
    # HUNT CREATION
    # =========================================================

    def create_hunt(
        self,
        title: str,
        registration_start: datetime,
        registration_end: datetime,
        hunt_start: datetime,
        prize_end: datetime,
        hard_close: datetime,
    ) -> int:

        times = [
            registration_start,
            registration_end,
            hunt_start,
            prize_end,
            hard_close,
        ]

        parsed = [self._parse_time(value) for value in times]

        (
            registration_start,
            registration_end,
            hunt_start,
            prize_end,
            hard_close,
        ) = parsed

        if not title.strip():
            raise ValueError("Название охоты не может быть пустым.")

        if not (
            registration_start
            < registration_end
            <= hunt_start
            < prize_end
            < hard_close
        ):
            raise ValueError(
                "Неверный порядок времени. "
                "Нужно: начало регистрации < конец регистрации "
                "< начало охоты < конец призового периода "
                "< полное закрытие."
            )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO hunts (
                    title,
                    registration_start,
                    registration_end,
                    hunt_start,
                    prize_end,
                    hard_close,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title.strip(),
                    self._db_time(registration_start),
                    self._db_time(registration_end),
                    self._db_time(hunt_start),
                    self._db_time(prize_end),
                    self._db_time(hard_close),
                    self.STATUS_SCHEDULED,
                ),
            )

            connection.commit()

            return int(cursor.lastrowid)

    # =========================================================
    # GET HUNT
    # =========================================================

    def get_hunt(self, hunt_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunts
                WHERE id = ?
                """,
                (hunt_id,),
            ).fetchone()

    def get_latest_hunt(self) -> Optional[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunts
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

    def get_active_hunt(self) -> Optional[sqlite3.Row]:
        now = self._db_time(self._now())

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunts
                WHERE registration_start <= ?
                  AND hard_close > ?
                  AND status != ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    now,
                    now,
                    self.STATUS_CLOSED,
                ),
            ).fetchone()

    # =========================================================
    # STATUS
    # =========================================================

    def get_status(self, hunt_id: int) -> str:
        hunt = self.get_hunt(hunt_id)

        if hunt is None:
            raise ValueError("Охота не найдена.")

        now = self._now()

        registration_start = self._parse_time(
            hunt["registration_start"]
        )
        registration_end = self._parse_time(
            hunt["registration_end"]
        )
        hunt_start = self._parse_time(
            hunt["hunt_start"]
        )
        prize_end = self._parse_time(
            hunt["prize_end"]
        )
        hard_close = self._parse_time(
            hunt["hard_close"]
        )

        if now < registration_start:
            return self.STATUS_SCHEDULED

        if registration_start <= now < registration_end:
            return self.STATUS_REGISTRATION

        if registration_end <= now < hunt_start:
            return self.STATUS_SCHEDULED

        if hunt_start <= now < prize_end:
            return self.STATUS_ACTIVE

        if prize_end <= now < hard_close:
            return self.STATUS_PRIZE_FINISHED

        return self.STATUS_CLOSED

    def refresh_status(self, hunt_id: int) -> str:
        status = self.get_status(hunt_id)

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hunts
                SET status = ?
                WHERE id = ?
                """,
                (
                    status,
                    hunt_id,
                ),
            )

            connection.commit()

        return status

    # =========================================================
    # REGISTRATION
    # =========================================================

    def can_register(self, hunt_id: int) -> bool:
        return self.get_status(hunt_id) == self.STATUS_REGISTRATION

    def register_player(
        self,
        hunt_id: int,
        telegram_id: int,
    ) -> bool:

        if not self.can_register(hunt_id):
            return False

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

            if existing:
                return True

            connection.execute(
                """
                INSERT INTO hunt_participants (
                    hunt_id,
                    telegram_id,
                    registered_at,
                    prize_eligible
                )
                VALUES (?, ?, ?, 1)
                """,
                (
                    hunt_id,
                    telegram_id,
                    self._db_time(self._now()),
                ),
            )

            connection.commit()

            return True

    def is_registered(
        self,
        hunt_id: int,
        telegram_id: int,
    ) -> bool:

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

            return row is not None

    # =========================================================
    # PRIZE ELIGIBILITY
    # =========================================================

    def is_prize_eligible(
        self,
        hunt_id: int,
        telegram_id: int,
    ) -> bool:

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT prize_eligible
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

            if row is None:
                return False

            return bool(row["prize_eligible"])

    # =========================================================
    # PLAYING
    # =========================================================

    def can_play(self, hunt_id: int) -> bool:
        status = self.get_status(hunt_id)

        return status in (
            self.STATUS_ACTIVE,
            self.STATUS_PRIZE_FINISHED,
        )

    def start_player(
        self,
        hunt_id: int,
        telegram_id: int,
    ) -> Dict[str, Any]:

        if not self.can_play(hunt_id):
            return {
                "success": False,
                "reason": "hunt_not_started",
                "prize_eligible": False,
            }

        registered = self.is_registered(
            hunt_id,
            telegram_id,
        )

        prize_eligible = self.is_prize_eligible(
            hunt_id,
            telegram_id,
        )

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT started_at
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO hunt_participants (
                        hunt_id,
                        telegram_id,
                        started_at,
                        prize_eligible
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        hunt_id,
                        telegram_id,
                        self._db_time(self._now()),
                        int(prize_eligible),
                    ),
                )
            elif existing["started_at"] is None:
                connection.execute(
                    """
                    UPDATE hunt_participants
                    SET started_at = ?
                    WHERE hunt_id = ?
                      AND telegram_id = ?
                    """,
                    (
                        self._db_time(self._now()),
                        hunt_id,
                        telegram_id,
                    ),
                )

            connection.commit()

        return {
            "success": True,
            "registered": registered,
            "prize_eligible": prize_eligible,
            "status": self.get_status(hunt_id),
        }

    # =========================================================
    # FINISH
    # =========================================================

    def finish_player(
        self,
        hunt_id: int,
        telegram_id: int,
        score: int = 0,
    ) -> bool:

        if not self.can_play(hunt_id):
            return False

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

            if row is None:
                connection.execute(
                    """
                    INSERT INTO hunt_participants (
                        hunt_id,
                        telegram_id,
                        started_at,
                        finished_at,
                        completed,
                        score,
                        prize_eligible
                    )
                    VALUES (?, ?, ?, ?, 1, ?, 0)
                    """,
                    (
                        hunt_id,
                        telegram_id,
                        self._db_time(self._now()),
                        self._db_time(self._now()),
                        int(score),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE hunt_participants
                    SET
                        finished_at = ?,
                        completed = 1,
                        score = ?
                    WHERE hunt_id = ?
                      AND telegram_id = ?
                    """,
                    (
                        self._db_time(self._now()),
                        int(score),
                        hunt_id,
                        telegram_id,
                    ),
                )

            connection.commit()

        return True

    # =========================================================
    # PARTICIPANTS
    # =========================================================

    def get_participant(
        self,
        hunt_id: int,
        telegram_id: int,
    ) -> Optional[sqlite3.Row]:

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND telegram_id = ?
                LIMIT 1
                """,
                (
                    hunt_id,
                    telegram_id,
                ),
            ).fetchone()

    def get_participants(
        self,
        hunt_id: int,
    ):

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunt_participants
                WHERE hunt_id = ?
                ORDER BY
                    completed DESC,
                    score DESC,
                    finished_at ASC,
                    telegram_id ASC
                """,
                (hunt_id,),
            ).fetchall()

    def count_registered(self, hunt_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND prize_eligible = 1
                """,
                (hunt_id,),
            ).fetchone()

            return int(row["total"])

    def count_started(self, hunt_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND started_at IS NOT NULL
                """,
                (hunt_id,),
            ).fetchone()

            return int(row["total"])

    def count_completed(self, hunt_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND completed = 1
                """,
                (hunt_id,),
            ).fetchone()

            return int(row["total"])

    # =========================================================
    # PRIZE PLACES
    # =========================================================

    def calculate_prize_places(
        self,
        hunt_id: int,
        prize_places: int = 3,
    ) -> list:

        if prize_places < 1:
            raise ValueError(
                "Количество призовых мест должно быть больше нуля."
            )

        hunt = self.get_hunt(hunt_id)

        if hunt is None:
            raise ValueError("Охота не найдена.")

        # Призовые места можно фиксировать
        # только после окончания призового периода.
        if self._now() < self._parse_time(hunt["prize_end"]):
            raise ValueError(
                "Призовые места ещё нельзя фиксировать. "
                "Призовой период ещё не закончился."
            )

        with self._connect() as connection:

            # Если места уже были зафиксированы —
            # больше их не пересчитываем.
            already_fixed = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND place IS NOT NULL
                """,
                (hunt_id,),
            ).fetchone()

            if already_fixed["total"] > 0:
                return connection.execute(
                    """
                    SELECT *
                    FROM hunt_participants
                    WHERE hunt_id = ?
                      AND place IS NOT NULL
                    ORDER BY place ASC
                    """,
                    (hunt_id,),
                ).fetchall()

            rows = connection.execute(
                """
                SELECT *
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND prize_eligible = 1
                  AND completed = 1
                ORDER BY
                    score DESC,
                    finished_at ASC,
                    telegram_id ASC
                """,
                (hunt_id,),
            ).fetchall()

            winners = rows[:prize_places]

            for index, row in enumerate(winners, start=1):
                connection.execute(
                    """
                    UPDATE hunt_participants
                    SET place = ?
                    WHERE hunt_id = ?
                      AND telegram_id = ?
                    """,
                    (
                        index,
                        hunt_id,
                        row["telegram_id"],
                    ),
                )

            connection.commit()

            return winners

        if prize_places < 1:
            raise ValueError(
                "Количество призовых мест должно быть больше нуля."
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND prize_eligible = 1
                  AND completed = 1
                ORDER BY
                    score DESC,
                    finished_at ASC,
                    telegram_id ASC
                """,
                (hunt_id,),
            ).fetchall()

            winners = rows[:prize_places]

            for index, row in enumerate(winners, start=1):
                connection.execute(
                    """
                    UPDATE hunt_participants
                    SET place = ?
                    WHERE hunt_id = ?
                      AND telegram_id = ?
                    """,
                    (
                        index,
                        hunt_id,
                        row["telegram_id"],
                    ),
                )

            connection.commit()

            return winners

    def get_prize_places(
        self,
        hunt_id: int,
    ):

        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM hunt_participants
                WHERE hunt_id = ?
                  AND place IS NOT NULL
                ORDER BY place ASC
                """
                ,
                (hunt_id,),
            ).fetchall()

    # =========================================================
    # INFORMATION FOR ADMIN PANEL
    # =========================================================

    def get_hunt_statistics(
        self,
        hunt_id: int,
    ) -> Dict[str, Any]:

        hunt = self.get_hunt(hunt_id)

        if hunt is None:
            raise ValueError("Охота не найдена.")

        status = self.refresh_status(hunt_id)

        return {
            "hunt_id": hunt_id,
            "title": hunt["title"],
            "status": status,
            "registered": self.count_registered(hunt_id),
            "started": self.count_started(hunt_id),
            "completed": self.count_completed(hunt_id),
            "prize_places": self.get_prize_places(hunt_id),
        }