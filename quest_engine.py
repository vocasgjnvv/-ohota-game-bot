from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Choice:
    text: str
    next_scene_id: str
    points: int = 0
    correct: bool = False


@dataclass
class Scene:
    scene_id: str
    text: str
    choices: List[Choice] = field(default_factory=list)
    input_answer: Optional[str] = None
    input_next_scene_id: Optional[str] = None
    input_points: int = 0
    finish_mission: bool = False


@dataclass
class Mission:
    mission_id: int
    title: str
    scenes: Dict[str, Scene] = field(default_factory=dict)
    start_scene_id: Optional[str] = None


@dataclass
class PlayerProgress:
    mission_id: int
    scene_id: str
    score: int = 0
    completed: bool = False


class QuestEngine:
    def __init__(self):
        self.missions: Dict[int, Mission] = {}
        self.players: Dict[int, PlayerProgress] = {}

    # -------------------------
    # МИССИИ
    # -------------------------

    def create_mission(self, mission_id: int, title: str) -> Mission:
        mission = Mission(
            mission_id=mission_id,
            title=title,
        )

        self.missions[mission_id] = mission

        return mission

    def get_mission(self, mission_id: int) -> Optional[Mission]:
        return self.missions.get(mission_id)

    # -------------------------
    # СЦЕНЫ
    # -------------------------

    def add_scene(
        self,
        mission_id: int,
        scene_id: str,
        text: str,
        start: bool = False,
        finish_mission: bool = False,
    ) -> Scene:

        mission = self.missions.get(mission_id)

        if mission is None:
            raise ValueError("Миссия не найдена.")

        scene = Scene(
            scene_id=scene_id,
            text=text,
            finish_mission=finish_mission,
        )

        mission.scenes[scene_id] = scene

        if start or mission.start_scene_id is None:
            mission.start_scene_id = scene_id

        return scene

    # -------------------------
    # ВАРИАНТЫ
    # -------------------------

    def add_choice(
        self,
        mission_id: int,
        scene_id: str,
        text: str,
        next_scene_id: str,
        points: int = 0,
        correct: bool = False,
    ):

        scene = self._get_scene(mission_id, scene_id)

        scene.choices.append(
            Choice(
                text=text,
                next_scene_id=next_scene_id,
                points=points,
                correct=correct,
            )
        )

    # -------------------------
    # СВОБОДНЫЙ ОТВЕТ
    # -------------------------

    def set_answer(
        self,
        mission_id: int,
        scene_id: str,
        answer: str,
        next_scene_id: str,
        points: int = 0,
    ):

        scene = self._get_scene(mission_id, scene_id)

        scene.input_answer = answer.strip().lower()
        scene.input_next_scene_id = next_scene_id
        scene.input_points = points

    # -------------------------
    # НАЧАЛО МИССИИ
    # -------------------------

    def start_mission(
        self,
        telegram_id: int,
        mission_id: int,
    ) -> Scene:

        mission = self.missions.get(mission_id)

        if mission is None:
            raise ValueError("Миссия не найдена.")

        if mission.start_scene_id is None:
            raise ValueError("У миссии нет стартовой сцены.")

        progress = PlayerProgress(
            mission_id=mission_id,
            scene_id=mission.start_scene_id,
        )

        self.players[telegram_id] = progress

        return mission.scenes[mission.start_scene_id]

    # -------------------------
    # ТЕКУЩАЯ СЦЕНА
    # -------------------------

    def get_current_scene(
        self,
        telegram_id: int,
    ) -> Optional[Scene]:

        progress = self.players.get(telegram_id)

        if progress is None:
            return None

        mission = self.missions.get(progress.mission_id)

        if mission is None:
            return None

        return mission.scenes.get(progress.scene_id)

    # -------------------------
    # ВЫБОР КНОПКИ
    # -------------------------

    def choose(
        self,
        telegram_id: int,
        choice_index: int,
    ) -> Scene:

        progress = self._get_progress(telegram_id)
        scene = self._get_scene(
            progress.mission_id,
            progress.scene_id,
        )

        if choice_index < 0 or choice_index >= len(scene.choices):
            raise ValueError("Такого варианта нет.")

        choice = scene.choices[choice_index]

        progress.score += choice.points

        return self._move_to_scene(
            progress,
            choice.next_scene_id,
        )

    # -------------------------
    # ТЕКСТОВЫЙ ОТВЕТ
    # -------------------------

    def answer(
        self,
        telegram_id: int,
        answer: str,
    ) -> Scene:

        progress = self._get_progress(telegram_id)

        scene = self._get_scene(
            progress.mission_id,
            progress.scene_id,
        )

        if scene.input_answer is None:
            raise ValueError(
                "На этой сцене нет текстового ответа."
            )

        user_answer = answer.strip().lower()

        if user_answer != scene.input_answer:
            raise ValueError("Неверный ответ.")

        progress.score += scene.input_points

        return self._move_to_scene(
            progress,
            scene.input_next_scene_id,
        )

    # -------------------------
    # ОЧКИ
    # -------------------------

    def get_score(self, telegram_id: int) -> int:

        progress = self.players.get(telegram_id)

        if progress is None:
            return 0

        return progress.score

    # -------------------------
    # ЗАВЕРШЕНИЕ
    # -------------------------

    def is_completed(self, telegram_id: int) -> bool:

        progress = self.players.get(telegram_id)

        if progress is None:
            return False

        return progress.completed

    # -------------------------
    # ВНУТРЕННИЕ МЕТОДЫ
    # -------------------------

    def _move_to_scene(
        self,
        progress: PlayerProgress,
        next_scene_id: Optional[str],
    ) -> Scene:

        if next_scene_id is None:
            progress.completed = True

            mission = self.missions[progress.mission_id]

            return mission.scenes[progress.scene_id]

        mission = self.missions[progress.mission_id]

        next_scene = mission.scenes.get(next_scene_id)

        if next_scene is None:
            raise ValueError(
                f"Сцена '{next_scene_id}' не найдена."
            )

        progress.scene_id = next_scene_id

        if next_scene.finish_mission:
            progress.completed = True

        return next_scene

    def _get_progress(
        self,
        telegram_id: int,
    ) -> PlayerProgress:

        progress = self.players.get(telegram_id)

        if progress is None:
            raise ValueError(
                "Игрок ещё не начал миссию."
            )

        return progress

    def _get_scene(
        self,
        mission_id: int,
        scene_id: str,
    ) -> Scene:

        mission = self.missions.get(mission_id)

        if mission is None:
            raise ValueError("Миссия не найдена.")

        scene = mission.scenes.get(scene_id)

        if scene is None:
            raise ValueError("Сцена не найдена.")

        return scene