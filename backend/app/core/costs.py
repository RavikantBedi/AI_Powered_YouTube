"""Cost ledger. Every LLM token, image, TTS request, and render is recorded per
project + agent, and rolled up onto the project row."""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.base import get_session_factory
from app.db.models import CostRecord, Project

log = logging.getLogger("agency.costs")

# USD per 1M tokens (input, output) — keep in sync with Anthropic pricing.
LLM_PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
IMAGE_PRICE_DEFAULT = 0.04     # per image, provider-dependent approximation
TTS_PRICE_PER_1K_CHARS = 0.11  # ElevenLabs approximation; edge-tts is free


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, outp = LLM_PRICES.get(model, (5.0, 25.0))
    return input_tokens / 1e6 * inp + output_tokens / 1e6 * outp


async def record_cost(project_id: str, agent_id: str, kind: str, *, provider: str = "",
                      model: str = "", units: float = 0.0, cost_usd: float = 0.0) -> None:
    try:
        async with get_session_factory()() as session:
            session.add(CostRecord(project_id=project_id, agent_id=agent_id, kind=kind,
                                   provider=provider, model=model, units=units, cost_usd=cost_usd))
            project = await session.get(Project, project_id)
            if project:
                project.total_cost_usd = (project.total_cost_usd or 0.0) + cost_usd
            await session.commit()
    except Exception:
        log.exception("failed to record cost")


async def project_costs(project_id: str) -> dict:
    async with get_session_factory()() as session:
        rows = (await session.execute(
            select(CostRecord).where(CostRecord.project_id == project_id))).scalars().all()
    by_kind: dict[str, float] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0.0) + r.cost_usd
    return {"total_usd": round(sum(by_kind.values()), 4),
            "by_kind": {k: round(v, 4) for k, v in by_kind.items()}}
