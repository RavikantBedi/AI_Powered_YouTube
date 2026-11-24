"""Three-layer memory: project (current production), channel (long-term identity +
performance learnings), agent (per-agent lessons/benchmarks). Backed by the memory table."""
from __future__ import annotations

from sqlalchemy import select

from app.db.base import get_session_factory
from app.db.models import MemoryRecord


class Memory:
    def __init__(self, scope: str, scope_id: str):
        self.scope = scope
        self.scope_id = scope_id

    async def remember(self, key: str, value: dict) -> None:
        async with get_session_factory()() as session:
            session.add(MemoryRecord(scope=self.scope, scope_id=self.scope_id, key=key, value=value))
            await session.commit()

    async def recall(self, key: str | None = None, limit: int = 20) -> list[dict]:
        async with get_session_factory()() as session:
            q = select(MemoryRecord).where(
                MemoryRecord.scope == self.scope, MemoryRecord.scope_id == self.scope_id)
            if key:
                q = q.where(MemoryRecord.key == key)
            q = q.order_by(MemoryRecord.created_at.desc()).limit(limit)
            rows = (await session.execute(q)).scalars().all()
        return [{"key": r.key, "value": r.value, "at": r.created_at} for r in rows]


def project_memory(project_id: str) -> Memory:
    return Memory("project", project_id)


def channel_memory(channel_id: str) -> Memory:
    return Memory("channel", channel_id)


def agent_memory(agent_id: str) -> Memory:
    return Memory("agent", agent_id)
