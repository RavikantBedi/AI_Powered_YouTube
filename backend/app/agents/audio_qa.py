"""Audio Quality Agent — measurement-based, not opinion-based.

Runs objective checks on every narration segment (existence, decodability, loudness,
true-peak clipping, speech-rate plausibility) and flags segments for regeneration. A
segment whose duration is wildly out of line with its text length usually means the TTS
engine truncated or looped — that is caught here rather than in the final render.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Storyboard
from app.services import ffmpeg

log = logging.getLogger("agency.audio_qa")

# Plausible speaking-rate band in words/second for narration in any language.
MIN_WPS, MAX_WPS = 1.2, 4.5
TARGET_LUFS = -16.0
LUFS_TOLERANCE = 4.0
TRUE_PEAK_CEILING = -0.5


class AudioQAAgent(BaseAgent):
    agent_id = "audio-qa-agent"
    name = "Audio Quality Agent"
    version = 1
    capabilities = ["evaluate_audio", "detect_audio_defects"]
    stage = "AUDIO_QA"
    quality_threshold = 90.0

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      mixed_audio: Path | None = None) -> dict:
        segments = [s for s in storyboard.scenes if s.audio_path]
        failed: list[dict] = []

        for scene in segments:
            path = Path(scene.audio_path)
            problems: list[str] = []
            if not path.exists() or path.stat().st_size < 1024:
                failed.append({"scene_id": scene.scene_id, "problems": ["missing or empty audio"]})
                continue
            try:
                duration = await ffmpeg.probe_duration(path)
            except ffmpeg.RenderError as e:
                failed.append({"scene_id": scene.scene_id, "problems": [f"undecodable: {e}"]})
                continue
            if duration < 0.4:
                problems.append(f"segment is only {duration:.2f}s — likely truncated")
            words = len(scene.narration_text.split())
            if duration > 0:
                wps = words / duration
                if wps > MAX_WPS:
                    problems.append(f"speech rate {wps:.1f} words/s — narration likely clipped")
                elif wps < MIN_WPS and words > 4:
                    problems.append(f"speech rate {wps:.1f} words/s — audio may contain a loop "
                                    f"or long silence")
            if problems:
                failed.append({"scene_id": scene.scene_id, "problems": problems})

        loudness: dict = {}
        if mixed_audio and Path(mixed_audio).exists():
            try:
                loudness = await ffmpeg.measure_loudness(Path(mixed_audio))
                if abs(loudness["integrated_lufs"] - TARGET_LUFS) > LUFS_TOLERANCE:
                    problem = (f"integrated loudness {loudness['integrated_lufs']:.1f} LUFS "
                               f"(target {TARGET_LUFS})")
                    failed.append({"scene_id": "MIX", "problems": [problem]})
                if loudness["true_peak_db"] > TRUE_PEAK_CEILING:
                    failed.append({"scene_id": "MIX", "problems": [
                        f"true peak {loudness['true_peak_db']:.1f} dBTP — clipping risk"]})
            except ffmpeg.RenderError as e:
                log.warning("loudness measurement failed: %s", e)

        clean = len(segments) - len({f["scene_id"] for f in failed if f["scene_id"] != "MIX"})
        score = 100.0 * clean / len(segments) if segments else 0.0
        report = {"segments": len(segments), "clean": clean, "score": round(score, 1),
                  "failed": failed, "loudness": loudness}
        ctx.store.save_json("qa/audio_qa.json", report)
        await ctx.emit("AUDIO_QA_DONE", {"score": report["score"], "failed": len(failed)})
        return report


register(AudioQAAgent())
