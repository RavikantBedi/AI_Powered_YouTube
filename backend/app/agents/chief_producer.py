"""Chief Producer Agent — the top-level decision maker and lifecycle owner.

It does not do the work. It creates projects, launches and supervises runs, holds the
cancel/pause controls, answers "is this actually done?", and routes capability gaps to
the Agent Factory.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core import events
from app.db.base import get_session_factory
from app.db.models import Project
from app.schemas.core import QAReport

log = logging.getLogger("agency.chief")

# Live runs, keyed by project id.
_runs: dict[str, Any] = {}
_tasks: dict[str, asyncio.Task] = {}


class ChiefProducerAgent(BaseAgent):
    agent_id = "chief-producer"
    name = "Chief Producer Agent"
    version = 1
    capabilities = ["orchestrate_production", "approve_publication", "handle_capability_gap"]
    stage = "ORCHESTRATION"
    max_attempts = 1

    async def execute(self, ctx: AgentContext, **inputs) -> Any:
        """The Chief Producer is driven through its lifecycle methods, not a single call."""
        return await self.start(inputs["project_id"], inputs["channel_id"],
                                inputs["request_text"])

    # ── lifecycle ──────────────────────────────────────────────────
    async def start(self, project_id: str, channel_id: str, request_text: str) -> None:
        from app.core.orchestrator import ProductionRun

        if project_id in _tasks and not _tasks[project_id].done():
            log.info("project %s is already running", project_id)
            return
        run = ProductionRun(project_id, channel_id, request_text)
        _runs[project_id] = run
        task = asyncio.create_task(run.run(), name=f"production-{project_id}")
        _tasks[project_id] = task
        task.add_done_callback(lambda t: _tasks.pop(project_id, None))

    async def cancel(self, project_id: str) -> bool:
        run = _runs.get(project_id)
        if run:
            run.cancelled.set()
        task = _tasks.get(project_id)
        if task and not task.done():
            task.cancel()
        async with get_session_factory()() as session:
            project = await session.get(Project, project_id)
            if project and project.status in {"running", "created", "paused"}:
                project.status = "cancelled"
                await session.commit()
        await events.emit(project_id, "PROJECT_CANCELLED", {})
        return True

    async def pause(self, project_id: str) -> bool:
        """Cooperative pause: the run stops at its next stage boundary with all
        completed stages checkpointed, so resuming loses no work."""
        run = _runs.get(project_id)
        if run:
            run.cancelled.set()
        async with get_session_factory()() as session:
            project = await session.get(Project, project_id)
            if project:
                project.status = "paused"
                await session.commit()
        await events.emit(project_id, "PROJECT_PAUSED", {})
        return True

    def is_running(self, project_id: str) -> bool:
        task = _tasks.get(project_id)
        return bool(task and not task.done())

    # ── decisions ──────────────────────────────────────────────────
    @staticmethod
    def definition_of_done(project: Project, qa: QAReport | None) -> dict:
        """Spec §66 — a project is DONE only when every box is genuinely ticked."""
        completed = set(project.completed_stages or [])
        publish = project.publish_result or {}
        requirement = project.requirement or {}
        wants_publish = requirement.get("publication_goal") == "publish"

        checks = {
            "requirement_captured": "REQUIREMENTS" in completed,
            "research_completed": "RESEARCH" in completed,
            "claims_verified": "FACT_CHECK" in completed,
            "script_approved": "SCRIPT" in completed,
            "storyboard_complete": "STORYBOARD" in completed,
            "visuals_generated": "PRODUCTION_FANOUT" in completed,
            "visual_qa_passed": "IMAGE_QA" in completed,
            "voice_generated": "PRODUCTION_FANOUT" in completed,
            "audio_qa_passed": "AUDIO_QA" in completed,
            "video_rendered": "EDIT" in completed,
            "subtitles_generated": "SUBTITLES" in completed,
            "thumbnail_generated": "PACKAGING" in completed,
            "metadata_generated": "PACKAGING" in completed,
            "rights_checked": "COPYRIGHT" in completed,
            "policy_checked": "POLICY" in completed,
            "final_qa_passed": bool(qa and qa.overall_score > 0 and qa.policy == "PASS"),
            "upload_succeeded": bool(publish.get("video_id")) if wants_publish else True,
            "video_url_confirmed": bool(publish.get("video_url")) if wants_publish else True,
            "user_notified": "NOTIFY" in completed,
        }
        return {"done": all(checks.values()), "checks": checks,
                "missing": [k for k, v in checks.items() if not v]}

    async def handle_capability_gap(self, ctx: AgentContext, capability: str,
                                    context: str = "") -> bool:
        """Escalation rung 5 (spec §54): ask the factory to build what's missing."""
        from app.agents.registry import get as get_agent
        from app.agents.registry import has_capability

        if has_capability(capability):
            return True
        spec = await get_agent("agent-factory").run(ctx, capability=capability,
                                                    context=context)
        return spec is not None


chief_producer = ChiefProducerAgent()
register(chief_producer)
