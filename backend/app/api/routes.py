"""REST API for the dashboard and any external caller."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.agents import registry as agent_registry
from app.agents.base import AgentContext
from app.agents.chief_producer import chief_producer
from app.api.deps import require_api_key
from app.core.artifacts import ArtifactStore
from app.core.costs import project_costs
from app.core.dna import load_channel_dna
from app.core.errors import ProviderUnavailableError
from app.db.base import get_session_factory
from app.db.models import AgentExecution, AgentRegistryRecord, ChannelRecord, EventRecord, Project
from app.providers import registry as provider_registry
from app.providers.youtube import YouTubeOAuth
from app.schemas.core import QAReport

log = logging.getLogger("agency.api")

router = APIRouter(prefix="/api")
secure = [Depends(require_api_key)]


# ── payloads ────────────────────────────────────────────────────────────

class CreateProject(BaseModel):
    request: str = Field(min_length=8, max_length=4000)
    channel: str = "anidoc"
    auto_start: bool = False


class TopicRequest(BaseModel):
    channel: str = "anidoc"
    count: int = Field(default=10, ge=1, le=20)
    hint: str = ""


class YouTubeConnect(BaseModel):
    redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob"


class YouTubeCallback(BaseModel):
    code: str
    redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob"


def _project_view(p: Project) -> dict:
    return {
        "id": p.id, "channel_id": p.channel_id, "request": p.request_text,
        "status": p.status, "current_stage": p.current_stage,
        "completed_stages": p.completed_stages or [],
        "requirement": p.requirement or {}, "plan": p.plan or {},
        "quality": p.quality or {}, "publish_result": p.publish_result or {},
        "blocked_on": p.blocked_on, "error": p.error,
        "total_cost_usd": round(p.total_cost_usd or 0, 4),
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


# ── projects ────────────────────────────────────────────────────────────

@router.post("/projects", dependencies=secure)
async def create_project(payload: CreateProject) -> dict:
    async with get_session_factory()() as session:
        channel = await session.get(ChannelRecord, payload.channel)
        if not channel:
            raise HTTPException(404, f"unknown channel '{payload.channel}'")
        project = Project(channel_id=payload.channel, request_text=payload.request)
        session.add(project)
        await session.commit()
        project_id = project.id

    if payload.auto_start:
        await chief_producer.start(project_id, payload.channel, payload.request)
    return {"project_id": project_id, "status": "running" if payload.auto_start else "created"}


@router.get("/projects", dependencies=secure)
async def list_projects(limit: int = Query(50, le=200), status: str = "") -> dict:
    async with get_session_factory()() as session:
        q = select(Project).order_by(desc(Project.created_at)).limit(limit)
        if status:
            q = q.where(Project.status == status)
        rows = (await session.execute(q)).scalars().all()
        counts = dict((await session.execute(
            select(Project.status, func.count(Project.id)).group_by(Project.status))).all())
    return {"projects": [_project_view(p) for p in rows], "counts": counts}


@router.get("/projects/{project_id}", dependencies=secure)
async def get_project(project_id: str) -> dict:
    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    qa = QAReport.model_validate(project.quality) if project.quality else None
    return {**_project_view(project),
            "costs": await project_costs(project_id),
            "definition_of_done": chief_producer.definition_of_done(project, qa),
            "is_running": chief_producer.is_running(project_id)}


@router.post("/projects/{project_id}/start", dependencies=secure)
async def start_project(project_id: str) -> dict:
    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    if project.status == "done":
        raise HTTPException(409, "project is already complete")
    await chief_producer.start(project_id, project.channel_id, project.request_text)
    return {"project_id": project_id, "status": "running"}


@router.post("/projects/{project_id}/retry", dependencies=secure)
async def retry_project(project_id: str) -> dict:
    """Resume a blocked or failed project from its last completed checkpoint."""
    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise HTTPException(404, "project not found")
        project.status = "created"
        project.error = ""
        project.blocked_on = ""
        await session.commit()
        channel_id, request_text = project.channel_id, project.request_text
    provider_registry.reset()  # pick up any credentials added since the block
    await chief_producer.start(project_id, channel_id, request_text)
    return {"project_id": project_id, "status": "running", "resumed": True}


@router.post("/projects/{project_id}/pause", dependencies=secure)
async def pause_project(project_id: str) -> dict:
    await chief_producer.pause(project_id)
    return {"project_id": project_id, "status": "paused"}


@router.post("/projects/{project_id}/cancel", dependencies=secure)
async def cancel_project(project_id: str) -> dict:
    await chief_producer.cancel(project_id)
    return {"project_id": project_id, "status": "cancelled"}


@router.get("/projects/{project_id}/status", dependencies=secure)
async def project_status(project_id: str) -> dict:
    """Compact live view for the production monitor."""
    async with get_session_factory()() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise HTTPException(404, "project not found")
        recent = (await session.execute(
            select(EventRecord).where(EventRecord.project_id == project_id)
            .order_by(desc(EventRecord.created_at)).limit(40))).scalars().all()

    from app.core.orchestrator import STAGES
    completed = set(project.completed_stages or [])
    progress = {}
    for event in recent:
        if event.event_type == "STAGE_PROGRESS":
            stage = event.payload.get("stage", "")
            if stage not in progress:
                progress[stage] = event.payload
    stages = [{
        "stage": s,
        "state": ("done" if s in completed
                  else "active" if s == project.current_stage
                  else "waiting"),
        "pct": 100 if s in completed else progress.get(s, {}).get("pct", 0),
        "detail": progress.get(s, {}).get("detail", ""),
    } for s in STAGES]

    return {"id": project.id, "status": project.status,
            "current_stage": project.current_stage, "stages": stages,
            "blocked_on": project.blocked_on, "error": project.error,
            "cost_usd": round(project.total_cost_usd or 0, 4),
            "quality": project.quality or {},
            "publish_result": project.publish_result or {},
            "events": [{"type": e.event_type, "payload": e.payload, "at": e.created_at}
                       for e in recent]}


@router.get("/projects/{project_id}/agents", dependencies=secure)
async def project_agents(project_id: str) -> dict:
    async with get_session_factory()() as session:
        rows = (await session.execute(
            select(AgentExecution).where(AgentExecution.project_id == project_id)
            .order_by(AgentExecution.started_at))).scalars().all()
    return {"executions": [{
        "agent_id": r.agent_id, "stage": r.stage, "attempt": r.attempt,
        "status": r.status, "model": r.model, "cost_usd": round(r.cost_usd, 4),
        "duration_seconds": r.duration_seconds, "error": r.error[:300],
        "started_at": r.started_at} for r in rows]}


@router.get("/projects/{project_id}/artifacts", dependencies=secure)
async def project_artifacts(project_id: str) -> dict:
    return {"artifacts": ArtifactStore(project_id).list_files()}


@router.get("/projects/{project_id}/artifacts/download", dependencies=secure)
async def download_artifact(project_id: str, path: str):
    store = ArtifactStore(project_id)
    try:
        target = store.path(path)
    except ValueError:
        raise HTTPException(400, "invalid artifact path")
    if not target.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(target, filename=Path(path).name)


@router.get("/projects/{project_id}/logs", dependencies=secure)
async def project_logs(project_id: str, limit: int = Query(200, le=1000)) -> dict:
    async with get_session_factory()() as session:
        rows = (await session.execute(
            select(EventRecord).where(EventRecord.project_id == project_id)
            .order_by(EventRecord.created_at).limit(limit))).scalars().all()
    return {"events": [{"type": r.event_type, "payload": r.payload, "at": r.created_at}
                       for r in rows]}


# ── topics, agents, channels ────────────────────────────────────────────

@router.post("/topics/discover", dependencies=secure)
async def discover_topics(payload: TopicRequest) -> dict:
    ctx = AgentContext(project_id="discovery", channel_id=payload.channel,
                       dna=load_channel_dna(payload.channel),
                       store=ArtifactStore("_discovery"))
    agent = agent_registry.get("topic-discovery-agent")
    topics = await agent.run(ctx, count=payload.count, hint=payload.hint)
    return {"topics": topics}


@router.get("/agents", dependencies=secure)
async def list_agents() -> dict:
    async with get_session_factory()() as session:
        rows = (await session.execute(select(AgentRegistryRecord))).scalars().all()
    return {"agents": [{
        "agent_id": r.agent_id, "name": r.name, "version": r.version,
        "capabilities": r.capabilities, "origin": r.origin, "status": r.status,
        "health": r.health, "created_at": r.created_at} for r in rows]}


@router.post("/agents/factory/create", dependencies=secure)
async def factory_create(capability: str, context: str = "") -> dict:
    ctx = AgentContext(project_id="factory", channel_id="anidoc",
                       dna=load_channel_dna("anidoc"), store=ArtifactStore("_factory"))
    spec = await agent_registry.get("agent-factory").run(ctx, capability=capability,
                                                         context=context)
    if spec is None:
        raise HTTPException(422, "the factory could not produce a safe, passing agent for "
                                 "this capability")
    return {"agent": spec.model_dump(exclude={"test_cases"})}


@router.get("/channels", dependencies=secure)
async def list_channels() -> dict:
    async with get_session_factory()() as session:
        rows = (await session.execute(select(ChannelRecord))).scalars().all()
    return {"channels": [{"id": r.id, "name": r.name, "dna": r.dna,
                          "youtube_channel_id": r.youtube_channel_id} for r in rows]}


@router.get("/capabilities", dependencies=secure)
async def capabilities() -> dict:
    from app.services.ffmpeg import ffmpeg_available
    return {"providers": provider_registry.capability_report(),
            "ffmpeg": ffmpeg_available(),
            "agents": len(agent_registry.all_agents())}


# ── youtube ─────────────────────────────────────────────────────────────

@router.post("/youtube/connect", dependencies=secure)
async def youtube_connect(payload: YouTubeConnect) -> dict:
    try:
        auth_url, state = YouTubeOAuth().start_flow(payload.redirect_uri)
    except ProviderUnavailableError as e:
        raise HTTPException(400, str(e))
    return {"authorization_url": auth_url, "state": state,
            "next": "Open the URL, grant access, then POST the code to /api/youtube/callback"}


@router.post("/youtube/callback", dependencies=secure)
async def youtube_callback(payload: YouTubeCallback) -> dict:
    try:
        result = YouTubeOAuth().finish_flow(payload.code, payload.redirect_uri)
    except ProviderUnavailableError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"OAuth exchange failed: {e}")
    provider_registry.reset()
    async with get_session_factory()() as session:
        for channel in (await session.execute(select(ChannelRecord))).scalars().all():
            if not channel.youtube_channel_id:
                channel.youtube_channel_id = result.get("channel_id", "")
        await session.commit()
    return result


@router.get("/youtube/status", dependencies=secure)
async def youtube_status() -> dict:
    oauth = YouTubeOAuth()
    return {"connected": oauth.is_connected(),
            "client_secret_present": Path(oauth.client_secret_file).exists()}


# ── dashboard summary ───────────────────────────────────────────────────

@router.get("/dashboard", dependencies=secure)
async def dashboard() -> dict:
    async with get_session_factory()() as session:
        projects = (await session.execute(
            select(Project).order_by(desc(Project.created_at)).limit(200))).scalars().all()
    total = len(projects)
    done = [p for p in projects if p.status == "done"]
    failed = [p for p in projects if p.status in {"failed", "blocked"}]
    return {
        "totals": {
            "projects": total,
            "active": sum(1 for p in projects if p.status == "running"),
            "completed": len(done),
            "failed": len(failed),
            "success_rate": round(100 * len(done) / total, 1) if total else 0.0,
            "total_cost_usd": round(sum(p.total_cost_usd or 0 for p in projects), 2),
            "average_quality": round(
                sum((p.quality or {}).get("overall_score", 0) for p in done) / len(done), 1)
            if done else 0.0,
        },
        "recent": [_project_view(p) for p in projects[:10]],
        "capabilities": provider_registry.capability_report(),
    }
