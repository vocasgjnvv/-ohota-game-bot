from typing import List, Optional

from quest_database import QuestDatabase


class QuestService:
    """
    Связующий слой между игровой логикой и базой данных.

    Telegram и сюжет сюда не помещаем.
    Этот класс отвечает за:
    - создание миссий;
    - создание сцен;
    - создание веток;
    - свободные ответы;
    - запуск миссии;
    - переход игрока;
    - очки;
    - сохранение прогресса.
    """

    def __init__(self, database_path: str = "ohota.db"):
        self.db = QuestDatabase(database_path)

    # =========================================================
    # МИССИИ
    # =========================================================

    def create_mission(
        self,
        mission_id: int,
        title: str,
        description: str = "",
    ):
        existing = self.db.get_mission(mission_id)

        if existing is not None:
            raise ValueError(
                "Такая миссия уже существует."
            )

        self.db.create_mission(
            mission_id=mission_id,
            title=title,
            description=description,
        )

        return self.db.get_mission(mission_id)

    def get_mission(
        self,
        mission_id: int,
    ):
        return self.db.get_mission(mission_id)

    def get_all_missions(self):
        return self.db.get_all_missions()

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
        mission = self.db.get_mission(mission_id)

        if mission is None:
            raise ValueError(
                "Миссия не найдена."
            )

        existing = self.db.get_scene(
            mission_id,
            scene_id,
        )

        if existing is not None:
            raise ValueError(
                "Такая сцена уже существует."
            )

        self.db.add_scene(
            mission_id=mission_id,
            scene_id=scene_id,
            text=text,
            is_start=is_start,
            is_finish=is_finish,
        )

        return self.db.get_scene(
            mission_id,
            scene_id,
        )

    def get_scene(
        self,
        mission_id: int,
        scene_id: str,
    ):
        return self.db.get_scene(
            mission_id,
            scene_id,
        )

    def get_start_scene(
        self,
        mission_id: int,
    ):
        return self.db.get_start_scene(
            mission_id
        )

    # =========================================================
    # ВЕТВЛЕНИЯ
    # =========================================================

    def add_choice(
        self,
        mission_id: int,
        scene_id: str,
        choice_text: str,
        next_scene_id: Optional[str],
        points: int = 0,
    ):
        scene = self.db.get_scene(
            mission_id,
            scene_id,
        )

        if scene is None:
            raise ValueError(
                "Исходная сцена не найдена."
            )

        if next_scene_id is not None:
            next_scene = self.db.get_scene(
                mission_id,
                next_scene_id,
            )

            if next_scene is None:
                raise ValueError(
                    "Следующая сцена не найдена."
                )

        self.db.add_choice(
            mission_id=mission_id,
            scene_id=scene_id,
            choice_text=choice_text,
            next_scene_id=next_scene_id,
            points=points,
        )

    def get_choices(
        self,
        mission_id: int,
        scene_id: str,
    ):
        return self.db.get_choices(
            mission_id,
            scene_id,
        )

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
        scene = self.db.get_scene(
            mission_id,
            scene_id,
        )

        if scene is None:
            raise ValueError(
                "Сцена не найдена."
            )

        if next_scene_id is not None:
            next_scene = self.db.get_scene(
                mission_id,
                next_scene_id,
            )

            if next_scene is None:
                raise ValueError(
                    "Следующая сцена не найдена."
                )

        cleaned_answer = self._normalize_answer(
            answer
        )

        if not cleaned_answer:
            raise ValueError(
                "Ответ не может быть пустым."
            )

        self.db.add_answer(
            mission_id=mission_id,
            scene_id=scene_id,
            answer=cleaned_answer,
            next_scene_id=next_scene_id,
            points=points,
        )

    def get_answers(
        self,
        mission_id: int,
        scene_id: str,
    ):
        return self.db.get_answers(
            mission_id,
            scene_id,
        )

    # =========================================================
    # ЗАПУСК МИССИИ
    # =========================================================

    def start_mission(
        self,
        telegram_id: int,
        mission_id: int,
    ):
        mission = self.db.get_mission(
            mission_id
        )

        if mission is None:
            raise ValueError(
                "Миссия не найдена."
            )

        start_scene = self.db.get_start_scene(
            mission_id
        )

        if start_scene is None:
            raise ValueError(
                "У миссии нет стартовой сцены."
            )

        self.db.start_player(
            telegram_id=telegram_id,
            mission_id=mission_id,
            scene_id=start_scene["scene_id"],
        )

        return start_scene

    # =========================================================
    # ТЕКУЩАЯ СЦЕНА
    # =========================================================

    def get_current_scene(
        self,
        telegram_id: int,
        mission_id: int,
    ):
        progress = self.db.get_progress(
            telegram_id,
            mission_id,
        )

        if progress is None:
            return None

        return self.db.get_scene(
            mission_id,
            progress["scene_id"],
        )

    # =========================================================
    # ВЫБОР ВЕТКИ
    # =========================================================

    def choose(
        self,
        telegram_id: int,
        mission_id: int,
        choice_id: int,
    ):
        progress = self.db.get_progress(
            telegram_id,
            mission_id,
        )

        if progress is None:
            raise ValueError(
                "Игрок ещё не начал эту миссию."
            )

        if progress["completed"]:
            raise ValueError(
                "Миссия уже завершена."
            )

        choices = self.db.get_choices(
            mission_id,
            progress["scene_id"],
        )

        selected_choice = None

        for choice in choices:
            if choice["id"] == choice_id:
                selected_choice = choice
                break

        if selected_choice is None:
            raise ValueError(
                "Такого варианта нет."
            )

        action_id = (
            f"choice:"
            f"{progress['scene_id']}:"
            f"{choice_id}"
        )

        already_completed = self.db.action_completed(
            telegram_id,
            mission_id,
            action_id,
        )

        score = progress["score"]

        if not already_completed:
            self.db.save_action(
                telegram_id,
                mission_id,
                action_id,
            )

            score += selected_choice["points"]

        next_scene_id = selected_choice[
            "next_scene_id"
        ]

        return self._move_player(
            telegram_id=telegram_id,
            mission_id=mission_id,
            next_scene_id=next_scene_id,
            score=score,
        )

    # =========================================================
    # ТЕКСТОВЫЙ ОТВЕТ
    # =========================================================

    def answer(
        self,
        telegram_id: int,
        mission_id: int,
        answer: str,
    ):
        progress = self.db.get_progress(
            telegram_id,
            mission_id,
        )

        if progress is None:
            raise ValueError(
                "Игрок ещё не начал эту миссию."
            )

        if progress["completed"]:
            raise ValueError(
                "Миссия уже завершена."
            )

        normalized_answer = self._normalize_answer(
            answer
        )

        answers = self.db.get_answers(
            mission_id,
            progress["scene_id"],
        )

        selected_answer = None

        for item in answers:
            if item["answer"] == normalized_answer:
                selected_answer = item
                break

        if selected_answer is None:
            raise ValueError(
                "Неверный ответ."
            )

        action_id = (
            f"answer:"
            f"{progress['scene_id']}:"
            f"{normalized_answer}"
        )

        already_completed = self.db.action_completed(
            telegram_id,
            mission_id,
            action_id,
        )

        score = progress["score"]

        if not already_completed:
            self.db.save_action(
                telegram_id,
                mission_id,
                action_id,
            )

            score += selected_answer["points"]

        next_scene_id = selected_answer[
            "next_scene_id"
        ]

        return self._move_player(
            telegram_id=telegram_id,
            mission_id=mission_id,
            next_scene_id=next_scene_id,
            score=score,
        )

    # =========================================================
    # ПРОГРЕСС
    # =========================================================

    def get_progress(
        self,
        telegram_id: int,
        mission_id: int,
    ):
        return self.db.get_progress(
            telegram_id,
            mission_id,
        )

    def get_player_missions(
        self,
        telegram_id: int,
    ):
        return self.db.get_player_missions(
            telegram_id
        )

    def get_score(
        self,
        telegram_id: int,
        mission_id: int,
    ) -> int:

        progress = self.db.get_progress(
            telegram_id,
            mission_id,
        )

        if progress is None:
            return 0

        return progress["score"]

    def is_completed(
        self,
        telegram_id: int,
        mission_id: int,
    ) -> bool:

        progress = self.db.get_progress(
            telegram_id,
            mission_id,
        )

        if progress is None:
            return False

        return bool(progress["completed"])

    # =========================================================
    # ПЕРЕХОД
    # =========================================================

    def _move_player(
        self,
        telegram_id: int,
        mission_id: int,
        next_scene_id: Optional[str],
        score: int,
    ):
        if next_scene_id is None:
            current_progress = self.db.get_progress(
                telegram_id,
                mission_id,
            )

            self.db.update_progress(
                telegram_id=telegram_id,
                mission_id=mission_id,
                scene_id=current_progress["scene_id"],
                score=score,
                completed=True,
            )

            return self.db.get_scene(
                mission_id,
                current_progress["scene_id"],
            )

        next_scene = self.db.get_scene(
            mission_id,
            next_scene_id,
        )

        if next_scene is None:
            raise ValueError(
                "Следующая сцена не найдена."
            )

        self.db.update_progress(
            telegram_id=telegram_id,
            mission_id=mission_id,
            scene_id=next_scene_id,
            score=score,
            completed=bool(next_scene["is_finish"]),
        )

        return next_scene

    # =========================================================
    # НОРМАЛИЗАЦИЯ ОТВЕТА
    # =========================================================

    @staticmethod
    def _normalize_answer(
        answer: str,
    ) -> str:

        return " ".join(
            answer.strip().lower().split()
        )
    
    def get_mission_statuses(self, telegram_id: int):
        missions = self.db.get_all_missions()
        statuses = []

        current_unlocked_found = False

        for mission in missions:
            progress = self.db.get_progress(
                telegram_id,
                mission["id"],
            )

            if progress and progress["completed"]:
                status = "completed"

            elif not current_unlocked_found:
                status = "current"
                current_unlocked_found = True

            else:
                status = "locked"

            statuses.append(
                {
                    "id": mission["id"],
                    "title": mission["title"],
                    "description": mission["description"],
                    "status": status,
                }
            )

        return statuses