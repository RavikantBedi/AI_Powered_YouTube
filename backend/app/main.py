"""FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents import registry as agent_registry
from app.api.routes import router
from app.config import get_settings
from app.core.dna import sync_channels_to_db
from app.db.base import init_db
from app.logging_config import configure_logging
from app.providers import registry as provider_registry
from app.services.ffmpeg import ffmpeg_available

log = logging.getLogger("agency")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(os.getenv("LOG_LEVEL", "INFO"),
                      json_logs=os.getenv("JSON_LOGS", "1") == "1")
    await init_db()
    await sync_channels_to_db()
    agent_registry.load_builtin_agents()
    await agent_registry.sync_registry_to_db()

    from app.agents.factory import load_factory_agents
    await load_factory_agents()

    report = provider_registry.capability_report()
    log.info("capabilities: %s", {k: v["available"] for k, v in report.items()})
    if not ffmpeg_available():
        log.warning("FFmpeg not found on PATH — rendering will be unavailable")
    if not get_settings().anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY not set — no agent can run")
    yield


app = FastAPI(
    title="AI Video Agency",
    description="Autonomous multi-agent YouTube video production system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health() -> dict:
    """Liveness + capability probe. Unauthenticated by design so orchestrators can poll."""
    return {
        "status": "ok",
        "ffmpeg": ffmpeg_available(),
        "llm_configured": bool(get_settings().anthropic_api_key),
        "agents": len(agent_registry.all_agents()),
    }
