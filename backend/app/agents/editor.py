"""Editor Agent — assembles the finished film programmatically.

Timeline construction is exact rather than approximate: every scene contributes one
clip whose length equals its measured narration audio, so concatenating the clips and
concatenating the narration produces two tracks of identical length that need no
stretching to line up.

Order of operations:
  per-scene clips -> video track
  per-scene narration -> voice track
  voice + music (sidechain ducked, loudness normalized) -> audio master
  video + audio (+ burned subtitles, + watermark) -> final.mp4
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import AgencyError
from app.schemas.core import Storyboard
from app.services import ffmpeg

log = logging.getLogger("agency.editor")


class EditorAgent(BaseAgent):
    agent_id = "editor-agent"
    name = "Editor Agent"
    version = 1
    capabilities = ["assemble_timeline", "render_video"]
    stage = "EDIT"
    quality_threshold = 90.0
    max_attempts = 2

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      music_path: Path | None = None,
                      subtitle_path: Path | None = None,
                      watermark: str = "") -> Path:
        if not ffmpeg.ffmpeg_available():
            raise AgencyError("ffmpeg/ffprobe not found on PATH — cannot render")

        usable = [s for s in storyboard.scenes if s.clip_path and s.audio_path]
        if not usable:
            raise AgencyError("no scene has both a clip and narration audio")
        skipped = len(storyboard.scenes) - len(usable)
        if skipped:
            log.warning("%d scenes excluded from the timeline (missing clip or audio)", skipped)

        await ctx.progress("EDIT", 10, "building video track")
        video_track = await ffmpeg.concat_clips(
            [Path(s.clip_path) for s in usable],
            ctx.store.path("final/video_track.mp4"))

        await ctx.progress("EDIT", 40, "building narration track")
        voice_track = await ffmpeg.concat_audio(
            [Path(s.audio_path) for s in usable],
            ctx.store.path("final/voice_track.wav"))

        await ctx.progress("EDIT", 60, "mixing audio")
        audio_master = await ffmpeg.mix_with_music(
            voice_track, music_path, ctx.store.path("final/audio_master.wav"))

        await ctx.progress("EDIT", 75, "final render")
        final = await ffmpeg.mux(
            video_track, audio_master, ctx.store.path("final/video.mp4"),
            subtitles=subtitle_path, watermark_text=watermark)

        info = await ffmpeg.video_info(final)
        # Guard against a silent render: a video track with no audio ships nothing.
        if not info["has_audio"]:
            raise AgencyError("rendered file has no audio stream")

        video_drift = abs(info["duration"] - storyboard.total_duration)
        if video_drift > 2.0:
            log.warning("render duration %.1fs differs from storyboard %.1fs",
                        info["duration"], storyboard.total_duration)

        ctx.store.save_json("final/render_info.json", {
            **info, "scenes_used": len(usable), "scenes_skipped": skipped,
            "music": str(music_path) if music_path else None,
            "subtitles_burned": bool(subtitle_path),
        })
        await ctx.emit("VIDEO_RENDERED", {
            "path": str(final), "duration": round(info["duration"], 1),
            "resolution": f"{info['width']}x{info['height']}",
            "size_mb": round(info["size_bytes"] / 1e6, 1)})
        return final


register(EditorAgent())
