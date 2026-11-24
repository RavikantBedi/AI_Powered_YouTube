"""Persistent entities. JSON columns hold the typed Pydantic payloads from
app.schemas.core so every stage is recoverable after a crash."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    channel_id: Mapped[str] = mapped_column(String(64), default="anidoc")
    request_text: Mapped[str] = mapped_column(Text)
    # created | running | paused | blocked | failed | done | cancelled
    status: Mapped[str] = mapped_column(String(24), default="created", index=True)
    current_stage: Mapped[str] = mapped_column(String(48), default="")
    completed_stages: Mapped[list] = mapped_column(JSON, default=list)
    stage_progress: Mapped[dict] = mapped_column(JSON, default=dict)   # {stage: {pct, detail}}
    requirement: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    blocked_on: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    quality: Mapped[dict] = mapped_column(JSON, default=dict)          # final QA report
    publish_result: Mapped[dict] = mapped_column(JSON, default=dict)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    updated_at: Mapped[str] = mapped_column(String(40), default=_now, onupdate=_now)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(48))
    priority: Mapped[int] = mapped_column(Integer, default=5)
    # pending | running | waiting | completed | failed | retrying | blocked | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_artifacts: Mapped[list] = mapped_column(JSON, default=list)
    output_artifacts: Mapped[list] = mapped_column(JSON, default=list)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    started_at: Mapped[str] = mapped_column(String(40), default="")
    completed_at: Mapped[str] = mapped_column(String(40), default="")


class AgentExecution(Base):
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(48))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="running")
    model: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[str] = mapped_column(String(40), default=_now)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class CostRecord(Base):
    __tablename__ = "cost_records"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))  # llm | image | video | tts | storage | render
    provider: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    units: Mapped[float] = mapped_column(Float, default=0.0)   # tokens / images / seconds
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class AgentRegistryRecord(Base):
    __tablename__ = "agent_registry"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(String(16), default="builtin")  # builtin | factory
    # production | sandbox | test | staging | disabled
    status: Mapped[str] = mapped_column(String(16), default="production")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # factory agents: prompt, schema, model tier
    health: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class MemoryRecord(Base):
    __tablename__ = "memory"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=_id)
    scope: Mapped[str] = mapped_column(String(16), index=True)  # project | channel | agent
    scope_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)


class ChannelRecord(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    dna: Mapped[dict] = mapped_column(JSON, default=dict)   # merged channel/script/visual/thumbnail DNA
    youtube_channel_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
