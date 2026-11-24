"""Music / Sound Design Agent.

Selects a rights-cleared bed from the local library and records its provenance. If the
library is empty the agent returns None and the Editor renders narration-only — an
unscored documentary is publishable; one with unknown-rights music is not.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.providers.music import LocalMusicLibrary

log = logging.getLogger("agency.music")


class MusicAgent(BaseAgent):
    agent_id = "music-agent"
    name = "Music / Sound Design Agent"
    version = 1
    capabilities = ["select_music", "sound_design"]
    stage = "MUSIC"

    async def execute(self, ctx: AgentContext, *, duration_seconds: float,
                      mood: str = "grave") -> Path | None:
        library = LocalMusicLibrary()
        track = await library.select(mood=mood, duration_seconds=duration_seconds)
        if track is None:
            await ctx.emit("MUSIC_SKIPPED", {
                "reason": "no rights-cleared tracks in the local library",
                "library": str(library.root)})
            return None

        license_info = library.license_for(track)
        if license_info.get("license", "UNKNOWN").upper() == "UNKNOWN":
            # Unknown rights status is a publishing risk — refuse the track (spec §30).
            log.warning("track %s has no license entry; rendering narration-only", track.name)
            await ctx.emit("MUSIC_SKIPPED", {"reason": f"no license record for {track.name}"})
            return None

        ctx.store.record_provenance(
            str(track), provider="local-library",
            method="selected from operator-supplied rights-cleared library",
            source=license_info.get("source", str(track)),
            license_=license_info.get("license", "UNKNOWN"))
        await ctx.emit("MUSIC_READY", {"track": track.name,
                                       "license": license_info.get("license")})
        return track


register(MusicAgent())
