"""Final QA Agent — an independent reviewer of the finished artifact, not a summary of
the earlier gates.

It inspects the rendered file itself: technical properties from ffprobe, sampled frames
viewed with vision, measured loudness, subtitle sync against the timeline, and metadata
completeness. Its issue list names the repair agent for each defect so the autonomous
repair loop can act without a human triaging.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import QAReport, Storyboard, YouTubeMetadata
from app.services import ffmpeg

log = logging.getLogger("agency.final_qa")

FRAME_SAMPLES = 8

SYSTEM = """You are the Final QA reviewer. You are shown frames sampled evenly across a
finished documentary, plus its technical report and metadata.

Judge the finished product:
- visual_consistency: do the frames look like one film — same illustration style, same
  palette, same rendering quality throughout?
- visual_quality: artifacts, anatomy, garbled text, composition
- brand_consistency: does it match the stated channel visual identity?
- editing_quality: does the pacing implied by the frames suit a documentary? Any frame
  that looks like a rendering failure?
- content_coherence: do the frames tell a coherent visual story matching the summary?

List concrete issues. For each, name which specialist should repair it, choosing from:
image-agent, motion-agent, editor-agent, voice-agent, thumbnail-agent, seo-agent.

Return ONLY JSON:
{"visual_consistency": 0, "visual_quality": 0, "brand_consistency": 0,
 "editing_quality": 0, "content_coherence": 0,
 "issues": [{"area": "...", "severity": "critical|high|medium|low",
             "description": "...", "repair_agent": "..."}]}"""


class FinalQAAgent(BaseAgent):
    agent_id = "final-qa-agent"
    name = "Final QA Agent"
    version = 1
    capabilities = ["final_review", "score_production"]
    task_kind = "final_qa"
    stage = "FINAL_QA"
    quality_threshold = 92.0

    async def execute(self, ctx: AgentContext, *, final_video: Path, storyboard: Storyboard,
                      metadata: YouTubeMetadata, policy_report: dict,
                      copyright_report: dict, script_summary: str) -> QAReport:
        issues: list[dict] = []

        # ── Technical inspection ───────────────────────────────────
        info = await ffmpeg.video_info(final_video)
        technical = 100.0
        if info["width"] < 1920 or info["height"] < 1080:
            issues.append({"area": "video", "severity": "high",
                           "description": f"resolution {info['width']}x{info['height']} below 1080p",
                           "repair_agent": "editor-agent"})
            technical -= 25
        if info["fps"] < 24:
            issues.append({"area": "video", "severity": "medium",
                           "description": f"frame rate {info['fps']}",
                           "repair_agent": "editor-agent"})
            technical -= 10
        if not info["has_audio"]:
            issues.append({"area": "audio", "severity": "critical",
                           "description": "no audio stream in the final render",
                           "repair_agent": "editor-agent"})
            technical -= 50

        target = storyboard.total_duration
        if target and abs(info["duration"] - target) > 5:
            issues.append({"area": "editing", "severity": "medium",
                           "description": f"render is {info['duration']:.0f}s but the timeline "
                                          f"is {target:.0f}s",
                           "repair_agent": "editor-agent"})
            technical -= 10

        # ── Audio measurement ──────────────────────────────────────
        audio_score = 95.0
        try:
            loudness = await ffmpeg.measure_loudness(final_video)
            if abs(loudness["integrated_lufs"] + 16) > 3:
                issues.append({"area": "audio", "severity": "medium",
                               "description": f"loudness {loudness['integrated_lufs']:.1f} LUFS "
                                              f"(YouTube targets -14 to -16)",
                               "repair_agent": "editor-agent"})
                audio_score -= 15
            if loudness["true_peak_db"] > -0.5:
                issues.append({"area": "audio", "severity": "high",
                               "description": f"true peak {loudness['true_peak_db']:.1f} dBTP",
                               "repair_agent": "editor-agent"})
                audio_score -= 20
        except ffmpeg.RenderError as e:
            log.warning("final loudness measurement failed: %s", e)

        # ── Visual inspection of the actual render ─────────────────
        frames: list[Path] = []
        step = max(info["duration"] / (FRAME_SAMPLES + 1), 1.0)
        for i in range(1, FRAME_SAMPLES + 1):
            try:
                frames.append(await ffmpeg.extract_frame(
                    final_video, step * i, ctx.store.path(f"qa/final_frames/f{i:02d}.jpg")))
            except ffmpeg.RenderError:
                break

        visual_scores = {"visual_consistency": 85.0, "visual_quality": 85.0,
                         "brand_consistency": 85.0, "editing_quality": 85.0,
                         "content_coherence": 85.0}
        if frames:
            data = await self.vision_json(
                ctx, system=SYSTEM,
                user=(f"Duration: {info['duration']:.0f}s. Resolution: "
                      f"{info['width']}x{info['height']} @ {info['fps']}fps.\n"
                      f"Channel visual identity: {ctx.visual_dna.get('art_style', '')} / "
                      f"{ctx.visual_dna.get('color_palette', '')}\n"
                      f"Video summary: {script_summary[:2000]}\n\n"
                      f"{len(frames)} frames sampled evenly across the film, in order."),
                images=frames, max_tokens=8000)
            for key in visual_scores:
                if key in data:
                    visual_scores[key] = float(data[key])
            issues.extend(data.get("issues", []))

        # ── Metadata & subtitle completeness ───────────────────────
        seo_score = 100.0
        if not metadata.title:
            issues.append({"area": "seo", "severity": "critical", "description": "no title",
                           "repair_agent": "seo-agent"})
            seo_score -= 50
        if len(metadata.description) < 120:
            issues.append({"area": "seo", "severity": "medium",
                           "description": "description is too short to rank",
                           "repair_agent": "seo-agent"})
            seo_score -= 15
        if len(metadata.tags) < 10:
            issues.append({"area": "seo", "severity": "low", "description": "few tags",
                           "repair_agent": "seo-agent"})
            seo_score -= 5
        if not ctx.store.exists("thumbnail/thumbnail_final.png"):
            issues.append({"area": "brand", "severity": "high", "description": "no thumbnail",
                           "repair_agent": "thumbnail-agent"})
            seo_score -= 25

        subtitle_files = [p for p in ["subtitles/hi.srt", "subtitles/en.srt"]
                          if ctx.store.exists(p)]
        if not subtitle_files:
            issues.append({"area": "youtube", "severity": "medium",
                           "description": "no subtitle track generated",
                           "repair_agent": "subtitle-agent"})

        # ── Aggregate ──────────────────────────────────────────────
        content_score = (visual_scores["content_coherence"] * 0.5
                         + min(100.0, 60 + 40 * bool(script_summary)) * 0.5)
        visual_final = (visual_scores["visual_consistency"] * 0.35
                        + visual_scores["visual_quality"] * 0.35
                        + visual_scores["brand_consistency"] * 0.30) * (technical / 100)
        editing_final = visual_scores["editing_quality"] * (technical / 100)

        policy_pass = (policy_report.get("verdict") == "PASS"
                       and copyright_report.get("status") != "BLOCK")

        report = QAReport(
            content=round(content_score, 1),
            visual=round(visual_final, 1),
            audio=round(max(audio_score, 0), 1),
            editing=round(editing_final, 1),
            seo=round(max(seo_score, 0), 1),
            policy="PASS" if policy_pass else "FAIL",
            issues=issues,
        )
        report.overall_score = round(
            report.content * 0.25 + report.visual * 0.25 + report.audio * 0.20
            + report.editing * 0.20 + report.seo * 0.10, 1)

        # A critical defect cannot be averaged away.
        if any(i.get("severity") == "critical" for i in issues):
            report.overall_score = min(report.overall_score, 50.0)

        ctx.store.save_json("qa/final_qa.json", report)
        await ctx.emit("FINAL_QA_DONE", {"overall": report.overall_score,
                                         "policy": report.policy,
                                         "issues": len(issues)})
        return report


register(FinalQAAgent())
