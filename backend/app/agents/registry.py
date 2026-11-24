"""Agent registry: capability lookup, versioning, and the capability-gap signal that
triggers the Agent Factory.

Built-in agents register at import. Factory-created agents register at runtime with
status sandbox → test → staging → production; only `production` agents are dispatchable
by the orchestrator.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.core.errors import CapabilityGapError
from app.db.base import get_session_factory
from app.db.models import AgentRegistryRecord

log = logging.getLogger("agency.registry.agents")

_agents: dict[str, BaseAgent] = {}


def register(agent: BaseAgent) -> BaseAgent:
    if agent.agent_id in _agents:
        existing = _agents[agent.agent_id]
        if agent.version <= existing.version:
            return existing
    _agents[agent.agent_id] = agent
    return agent


def get(agent_id: str) -> BaseAgent:
    if agent_id not in _agents:
        raise CapabilityGapError(agent_id, "no agent registered under this id")
    return _agents[agent_id]


def find_by_capability(capability: str) -> BaseAgent:
    for agent in _agents.values():
        if capability in agent.capabilities:
            return agent
    raise CapabilityGapError(capability, "no registered agent provides this capability")


def has_capability(capability: str) -> bool:
    return any(capability in a.capabilities for a in _agents.values())


def all_agents() -> list[BaseAgent]:
    return list(_agents.values())


def load_builtin_agents() -> None:
    """Import every agent module so its `register(...)` call runs."""
    from app.agents import (  # noqa: F401
        audio_qa,
        chief_producer,
        copyright_agent,
        critic,
        editor,
        fact_check,
        factory,
        image_agent,
        image_qa,
        motion,
        music,
        notification,
        planner,
        policy,
        publisher,
        qa_final,
        requirements,
        research,
        script,
        seo,
        story,
        storyboard,
        subtitles,
        thumbnail,
        topic_discovery,
        video_qa,
        voice,
    )
    log.info("registered %d built-in agents", len(_agents))


async def sync_registry_to_db() -> None:
    async with get_session_factory()() as session:
        existing = {r.agent_id for r in
                    (await session.execute(select(AgentRegistryRecord))).scalars().all()}
        for agent in _agents.values():
            if agent.agent_id in existing:
                row = await session.get(AgentRegistryRecord, agent.agent_id)
                row.version = agent.version
                row.capabilities = agent.capabilities
                row.spec = agent.describe()
            else:
                session.add(AgentRegistryRecord(
                    agent_id=agent.agent_id, name=agent.name, version=agent.version,
                    capabilities=agent.capabilities,
                    origin=agent.describe().get("origin", "builtin"),
                    status="production", spec=agent.describe()))
        await session.commit()
