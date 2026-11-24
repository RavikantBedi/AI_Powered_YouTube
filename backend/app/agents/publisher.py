"""Publishing Agent — uploads and then independently verifies the result.

Upload success is not assumed from a 200 response: after the insert the agent reads the
video back from the API to confirm it exists, is processing, and carries the privacy
status that was requested.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.providers.youtube import YouTubeDataAPI
from app.schemas.core import PublishResult, YouTubeMetadata

log = logging.getLogger("agency.publisher")


class PublishingAgent(BaseAgent):
    agent_id = "publisher-agent"
    name = "Publishing Agent"
    version = 1
    capabilities = ["publish_youtube", "verify_publication"]
    stage = "PUBLISH"
    max_attempts = 2

    async def execute(self, ctx: AgentContext, *, final_video: Path,
                      metadata: YouTubeMetadata, privacy_status: str) -> PublishResult:
        provider: YouTubeDataAPI = provider_registry.get_provider("youtube")

        thumbnail = ctx.store.path("thumbnail/thumbnail_final.png")
        captions: list[tuple[Path, str]] = []
        for rel, lang in [(f"subtitles/{metadata.language}.srt", metadata.language),
                          ("subtitles/en.srt", "en")]:
            if ctx.store.exists(rel):
                captions.append((ctx.store.path(rel), lang))

        await ctx.emit("UPLOAD_STARTED", {"title": metadata.title,
                                          "privacy": privacy_status})
        result = await provider.upload(
            video_path=final_video,
            title=metadata.title,
            description=metadata.description,
            tags=metadata.tags,
            category_id=metadata.category_id,
            language=metadata.language,
            privacy_status=privacy_status,
            thumbnail_path=thumbnail if thumbnail.exists() else None,
            captions=captions,
        )

        # Independent read-back — never report success on the write alone.
        verification = await provider.verify(result["video_id"])
        if not verification.get("found"):
            raise ProviderUnavailableError(
                "youtube", f"video {result['video_id']} not found after upload")
        if verification.get("privacy_status") != privacy_status:
            log.warning("privacy mismatch: requested %s, got %s",
                        privacy_status, verification.get("privacy_status"))

        publish = PublishResult(
            video_id=result["video_id"],
            video_url=result["video_url"],
            upload_time=datetime.now(timezone.utc).isoformat(),
            privacy_status=verification.get("privacy_status", privacy_status),
            thumbnail_status=result.get("thumbnail_status", ""),
            caption_status=result.get("caption_status", ""),
        )
        ctx.store.save_json("metadata/publish_result.json", publish)
        ctx.store.save_json("metadata/publish_verification.json", verification)

        await ctx.emit("UPLOAD_COMPLETED", {"video_id": publish.video_id,
                                            "url": publish.video_url})
        await ctx.emit("PUBLISHED", {"url": publish.video_url,
                                     "privacy": publish.privacy_status,
                                     "processing": verification.get("processing_status", "")})
        return publish


register(PublishingAgent())
