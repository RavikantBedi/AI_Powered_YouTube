"""Video Quality Agent — validates generated motion.

Technical validation is deterministic (duration match, resolution, frame rate, decode
integrity) because those failures are objective. Temporal defects that only a viewer
would notice — flicker, warping faces, unnatural movement — are caught by sampling
frames from each clip and inspecting them with vision.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Storyboard
from app.services import ffmpeg

log = logging.getLogger("agency.video_qa")

DURATION_TOLERANCE = 0.35  # seconds
# Vision-sampling every clip in a 150-scene film is expensive; sample a slice.
VISION_SAMPLE = 12

SYSTEM = """You are the Video Quality Agent. You are shown frames sampled from the START
and END of the same generated clip, in pairs.

For each pair judge whether the motion between them is acceptable for a serious
documentary:
- temporal_stability: has the subject warped, melted, drifted, or changed identity?
- motion_quality: is the camera move subtle and controlled, or does it lurch/overshoot?
- face_integrity: do faces stay coherent between the two frames?
- continuity: is it recognisably the same scene?

Return ONLY JSON:
{"results": [{"clip": 1, "temporal_stability": 0, "motion_quality": 0,
  "face_integrity": 0, "continuity": 0, "overall": 0, "usable": true,
  "problems": ["..."]}]}"""


class VideoQAAgent(BaseAgent):
    agent_id = "video-qa-agent"
    name = "Video Quality Agent"
    version = 1
    capabilities = ["evaluate_video", "detect_temporal_defects"]
    task_kind = "image_qa"
    stage = "VIDEO_QA"
    quality_threshold = 88.0

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard) -> dict:
        clips = [s for s in storyboard.scenes if s.clip_path]
        failed: list[dict] = []

        # 1. Deterministic technical validation of every clip.
        for scene in clips:
            path = Path(scene.clip_path)
            problems: list[str] = []
            try:
                info = await ffmpeg.video_info(path)
            except ffmpeg.RenderError as e:
                failed.append({"scene_id": scene.scene_id, "problems": [f"undecodable: {e}"]})
                continue
            if abs(info["duration"] - scene.duration_seconds) > DURATION_TOLERANCE:
                problems.append(f"duration {info['duration']:.2f}s != narration "
                                f"{scene.duration_seconds:.2f}s")
            if (info["width"], info["height"]) != (ffmpeg.WIDTH, ffmpeg.HEIGHT):
                problems.append(f"resolution {info['width']}x{info['height']}")
            if info["fps"] and abs(info["fps"] - ffmpeg.FPS) > 1:
                problems.append(f"frame rate {info['fps']}")
            if problems:
                failed.append({"scene_id": scene.scene_id, "problems": problems})

        # 2. Vision sampling for temporal defects.
        sample = clips[:: max(1, len(clips) // VISION_SAMPLE)][:VISION_SAMPLE]
        vision_scores: dict[str, float] = {}
        if sample:
            frames: list[Path] = []
            order: list[str] = []
            for scene in sample:
                try:
                    a = await ffmpeg.extract_frame(
                        Path(scene.clip_path), 0.2,
                        ctx.store.path(f"qa/frames/{scene.scene_id}_a.jpg"))
                    b = await ffmpeg.extract_frame(
                        Path(scene.clip_path), max(scene.duration_seconds - 0.3, 0.3),
                        ctx.store.path(f"qa/frames/{scene.scene_id}_b.jpg"))
                    frames.extend([a, b])
                    order.append(scene.scene_id)
                except ffmpeg.RenderError as e:
                    log.warning("frame extraction failed for %s: %s", scene.scene_id, e)
            if frames:
                listing = "\n".join(f"Clip {i + 1} = frames {2 * i + 1} (start) and "
                                    f"{2 * i + 2} (end), scene {sid}"
                                    for i, sid in enumerate(order))
                data = await self.vision_json(
                    ctx, system=SYSTEM,
                    user=f"{len(order)} clips, start/end frame pairs in order.\n\n{listing}",
                    images=frames, max_tokens=6000)
                for r in data.get("results", []):
                    idx = int(r.get("clip", 0)) - 1
                    if 0 <= idx < len(order):
                        vision_scores[order[idx]] = float(r.get("overall", 0))
                        if not r.get("usable", True):
                            failed.append({"scene_id": order[idx],
                                           "problems": r.get("problems", ["temporal defect"])})

        average = (sum(vision_scores.values()) / len(vision_scores)) if vision_scores else 90.0
        report = {
            "clips": len(clips),
            "sampled": len(vision_scores),
            "average_visual_score": round(average, 1),
            "failed": failed,
        }
        ctx.store.save_json("qa/video_qa.json", report)
        await ctx.emit("VIDEO_QA_DONE", {"clips": len(clips), "failed": len(failed)})
        return report


register(VideoQAAgent())
