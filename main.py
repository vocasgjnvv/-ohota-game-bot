from pathlib import Path
import zipfile, textwrap

root = Path("/mnt/data/ohota_v2_foundation")
(root / "database" / "repositories").mkdir(parents=True, exist_ok=True)

files = {
"config.py": r'''import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    database_url: str
    beta_mode: bool


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    raw_admin_id = os.getenv("ADMIN_ID", "0").strip()
    try:
        admin_id = int(raw_admin_id)
    except ValueError as exc:
        raise RuntimeError("ADMIN_ID must be an integer") from exc

    database_url = os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./data/ohota_v2.db",
    ).strip()

    beta_mode = os.getenv("BETA_MODE", "false").lower() in {
        "1", "true", "yes", "on"
    }

    return Settings(
        bot_token=token,
        admin_id=admin_id,
        database_url=database_url,
        beta_mode=beta_mode,
    )


settings = load_settings()
''',
"database/__init__.py": "",
"database/db.py": r'''from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_path(database_url: str) -> None:
    if database_url.startswith("sqlite"):
        Path("data").mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.database_url)

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    # Import models before create_all so SQLAlchemy knows every table.
    from database import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
''',
"database/models.py": r'''from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db import Base


class PlayerStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    FINISHED = "finished"


class MissionStatus(str, Enum):
    DRAFT = "draft"
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"
    ARCHIVED = "archived"


class InteractionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ClueType(str, Enum):
    TEXT = "text"
    TARGET_PLAYER = "target_player"
    CHOICE = "choice"


class Player(Base):
    tablename = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), default=PlayerStatus.ACTIVE.value, nullable=False
    )
    current_mission_id: Mapped[int | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL"), nullable=True
    )
    current_stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("stages.id", ondelete="SET NULL"), nullable=True
    )
    current_branch: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
DateTime, default=datetime.utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    mission_links: Mapped[list["MissionPlayer"]] = relationship(
        back_populates="player", foreign_keys="MissionPlayer.player_id"
    )


class Mission(Base):
    tablename = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), default=MissionStatus.DRAFT.value, nullable=False, index=True
    )
    max_players: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    stages: Mapped[list["Stage"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    players: Mapped[list["MissionPlayer"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )


class Stage(Base):
    tablename = "stages"
    table_args = (
        UniqueConstraint("mission_id", "number", name="uq_stage_mission_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    mission: Mapped["Mission"] = relationship(back_populates="stages")
    clues: Mapped[list["Clue"]] = relationship(
        back_populates="stage", cascade="all, delete-orphan"
    )


class MissionPlayer(Base):
    tablename = "mission_players"
    table_args = (
        UniqueConstraint("mission_id", "player_id", name="uq_mission_player"),
        Index("ix_mission_players_mission_status", "mission_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    mission: Mapped["Mission"] = relationship(back_populates="players")
    player: Mapped["Player"] = relationship(back_populates="mission_links")


class Clue(Base):
    tablename = "clues"
    table_args = (
        Index("ix_clues_owner_stage", "owner_player_id", "stage_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("stages.id", ondelete="CASCADE"), nullable=False
    )
    owner_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    target_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )

    clue_type: Mapped[str] = mapped_column(
        String(32), default=ClueType.TEXT.value, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
text: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    stage: Mapped["Stage"] = relationship(back_populates="clues")


class Interaction(Base):
    tablename = "interactions"
    table_args = (
        Index("ix_interactions_mission_stage", "mission_id", "stage_id"),
        Index("ix_interactions_players", "initiator_id", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("stages.id", ondelete="CASCADE"), nullable=False
    )
    initiator_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )

    interaction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=InteractionStatus.PENDING.value, nullable=False
    )
    branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InteractionAction(Base):
    tablename = "interaction_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interaction_id: Mapped[int] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class ScoreEvent(Base):
    tablename = "score_events"
    table_args = (
        Index("ix_score_events_player_created", "player_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    mission_id: Mapped[int | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL"), nullable=True
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("stages.id", ondelete="SET NULL"), nullable=True
    )
    interaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("interactions.id", ondelete="SET NULL"), nullable=True
    )

    xp_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class GameState(Base):
    tablename = "game_states"
    table_args = (
        UniqueConstraint(
            "mission_id", "player_id", name="uq_game_state_mission_player"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("stages.id", ondelete="SET NULL"), nullable=True
    )
branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(64), default="active", nullable=False)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
''',
"database/repositories/__init__.py": "",
"database/repositories/players.py": r'''from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Player


async def get_or_create_player(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> Player:
    result = await session.execute(
        select(Player).where(Player.telegram_id == telegram_id)
    )
    player = result.scalar_one_or_none()

    now = datetime.utcnow()

    if player is None:
        player = Player(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            last_seen_at=now,
        )
        session.add(player)
    else:
        player.username = username
        player.first_name = first_name
        player.last_name = last_name
        player.last_seen_at = now

    await session.flush()
    return player
''',
"database/repositories/missions.py": r'''from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Mission, MissionPlayer, Stage


async def get_mission(
    session: AsyncSession, mission_id: int
) -> Mission | None:
    result = await session.execute(
        select(Mission).where(Mission.id == mission_id)
    )
    return result.scalar_one_or_none()


async def get_stage(
    session: AsyncSession, stage_id: int
) -> Stage | None:
    result = await session.execute(
        select(Stage).where(Stage.id == stage_id)
    )
    return result.scalar_one_or_none()


async def join_mission(
    session: AsyncSession, mission_id: int, player_id: int
) -> MissionPlayer:
    result = await session.execute(
        select(MissionPlayer).where(
            MissionPlayer.mission_id == mission_id,
            MissionPlayer.player_id == player_id,
        )
    )
    link = result.scalar_one_or_none()

    if link is None:
        link = MissionPlayer(
            mission_id=mission_id,
            player_id=player_id,
        )
        session.add(link)
        await session.flush()

    return link
''',
"database/repositories/clues.py": r'''from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Clue


async def get_player_clues(
    session: AsyncSession,
    player_id: int,
    stage_id: int | None = None,
) -> list[Clue]:
    query = select(Clue).where(Clue.owner_player_id == player_id)

    if stage_id is not None:
        query = query.where(Clue.stage_id == stage_id)

    query = query.order_by(Clue.id)

    result = await session.execute(query)
    return list(result.scalars().all())
''',
"database/repositories/interactions.py": r'''from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Interaction,
    InteractionAction,
    InteractionStatus,
)


async def create_interaction(
    session: AsyncSession,
    mission_id: int,
    stage_id: int,
    initiator_id: int,
    target_id: int,
    interaction_type: str,
) -> Interaction:
    interaction = Interaction(
        mission_id=mission_id,
        stage_id=stage_id,
        initiator_id=initiator_id,
        target_id=target_id,
        interaction_type=interaction_type,
        status=InteractionStatus.PENDING.value,
    )
    session.add(interaction)
    await session.flush()
    return interaction


async def add_action(
    session: AsyncSession,
    interaction_id: int,
    player_id: int,
    action_type: str,
    payload: str | None = None,
) -> InteractionAction:
action = InteractionAction(
        interaction_id=interaction_id,
        player_id=player_id,
        action_type=action_type,
        payload=payload,
    )
    session.add(action)
    await session.flush()
    return action


async def complete_interaction(
    session: AsyncSession,
    interaction_id: int,
    result_text: str,
    branch: str | None = None,
) -> Interaction | None:
    result = await session.execute(
        select(Interaction).where(Interaction.id == interaction_id)
    )
    interaction = result.scalar_one_or_none()

    if interaction is None:
        return None

    interaction.status = InteractionStatus.COMPLETED.value
    interaction.result = result_text
    interaction.branch = branch
    interaction.finished_at = datetime.utcnow()

    await session.flush()
    return interaction
''',
".env.example": r'''BOT_TOKEN=
ADMIN_ID=
DATABASE_URL=sqlite+aiosqlite:///./data/ohota_v2.db
BETA_MODE=false
''',
"requirements-v2.txt": r'''aiogram>=3.30,<4.0
aiohttp>=3.8,<4.0
SQLAlchemy>=2.0,<3.0
aiosqlite>=0.20,<1.0

# For PostgreSQL on the future VPS:
# asyncpg>=0.29,<1.0
''',
"README_V2_FOUNDATION.md": r'''# OhotaGameBot V2 — Foundation

This folder is a new foundation layer. The existing bot is intentionally not replaced.

## What is included

- environment-based configuration
- async SQLAlchemy database layer
- SQLite for free/local development
- PostgreSQL-compatible architecture
- players
- missions
- stages
- mission participants
- personal clues
- player-to-player interactions
- interaction actions
- score/XP event history
- persistent game state

## Run locally

1. Copy .env.example to .env.
2. Set BOT_TOKEN and ADMIN_ID.
3. Install requirements-v2.txt.
4. Import settings only after environment variables are available.
5. Call await init_db() during application startup.

The old main.py is not replaced by this foundation.
'''
}

for rel, content in files.items():
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")

zip_path = Path("/mnt/data/ohota_v2_foundation.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob("*"):
        if p.is_file():
            z.write(p, p.relative_to(root))

print(f"Создан пакет: {zip_path}")
print("Файлов:", len(files))

