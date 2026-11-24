"""Production Planner Agent — turns a RequirementSpec into an executable ProductionPlan
with resource and cost estimates, then runs capability-gap detection.

Scene count and word count are derived arithmetically (not guessed by a model) so the
storyboard, narration length, and render duration stay consistent with each other.
"""
from __future__ import annotations

import logging

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.costs import IMAGE_PRICE_DEFAULT, TTS_PRICE_PER_1K_CHARS
from app.providers import registry as provider_registry
from app.schemas.core import ProductionPlan, RequirementSpec

log = logging.getLogger("agency.planner")

# Measured narration rates for a grave documentary delivery, by language family.
# The requested DURATION is the source of truth for script length — a word-count band
# in Channel DNA is treated as advisory, because DNA bands are authored for a nominal
# runtime and silently conflict with an explicit user request.
NARRATION_WPM = {"hi": 130, "ur": 130, "en": 150, "default": 140}
AVG_SCENE_SECONDS = 5.0
# Rough per-project LLM spend: research + script + critics + QA passes.
LLM_BUDGET_ESTIMATE_USD = 2.50


def words_for_duration(duration_seconds: int, language: str) -> int:
    wpm = NARRATION_WPM.get(language.split("-")[0].lower(), NARRATION_WPM["default"])
    return int(duration_seconds / 60 * wpm)


class PlannerAgent(BaseAgent):
    agent_id = "planner-agent"
    name = "Production Planner Agent"
    version = 1
    capabilities = ["plan_production", "estimate_cost", "detect_capability_gaps"]
    task_kind = "planning"
    stage = "PLANNING"

    async def execute(self, ctx: AgentContext, *, requirement: RequirementSpec) -> ProductionPlan:
        word_target = words_for_duration(requirement.duration_seconds, requirement.language)

        # The DNA band is advisory. Record the conflict rather than silently overriding
        # the duration the user actually asked for.
        band = ctx.script_dna.get("word_count_target", {})
        band_note = ""
        if band:
            lo, hi = int(band.get("min", 0)), int(band.get("max", 10**9))
            if not (lo <= word_target <= hi):
                band_note = (f"requested {requirement.duration_seconds}s implies {word_target} words, "
                             f"outside the channel's advisory band {lo}-{hi}; honoring the "
                             f"requested duration")

        scenes = max(20, int(requirement.duration_seconds / AVG_SCENE_SECONDS))

        images_ready = provider_registry.capability_report()["image"]["ready"]
        motion_ready = provider_registry.capability_report()["video"]["ready"]

        plan = ProductionPlan(
            project_id=ctx.project_id,
            video_type=requirement.genre,
            duration_target=requirement.duration_seconds,
            language=requirement.language,
            estimated_scenes=scenes,
            research_depth="deep" if requirement.factuality_requirement == "strict" else "standard",
            voice_required=True,
            images_required=images_ready,
            motion_required=motion_ready,
            thumbnail_variants=int(ctx.thumbnail_dna.get("variants_per_video", 5)),
            subtitles_required=True,
            youtube_upload=requirement.publication_goal == "publish",
            word_count_target=word_target,
            estimated_llm_calls=18 + scenes // 20,
            estimated_image_calls=scenes + int(ctx.thumbnail_dna.get("variants_per_video", 5)),
        )

        # Cost estimate: LLM + images + thumbnails + TTS characters.
        # Hindi averages ~5.5 characters per word.
        tts_chars = word_target * 5.5
        plan.estimated_cost_usd = round(
            LLM_BUDGET_ESTIMATE_USD
            + plan.estimated_image_calls * IMAGE_PRICE_DEFAULT
            + (tts_chars / 1000) * TTS_PRICE_PER_1K_CHARS, 2)

        gaps = self._capability_gaps(plan)
        notes = gaps + ([band_note] if band_note else [])
        plan.notes = "; ".join(notes) if notes else "all required capabilities available"

        ctx.store.save_json("plan.json", plan)
        await ctx.emit("PLAN_READY", {
            "scenes": plan.estimated_scenes,
            "word_target": plan.word_count_target,
            "estimated_cost_usd": plan.estimated_cost_usd,
            "gaps": gaps,
        })
        return plan

    def _capability_gaps(self, plan: ProductionPlan) -> list[str]:
        """Report — don't raise. The Chief Producer decides whether a gap is fatal,
        degradable, or routable to the Agent Factory."""
        report = provider_registry.capability_report()
        gaps: list[str] = []
        if not report["image"]["ready"]:
            gaps.append("no image provider: " + ", ".join(report["image"]["missing_credentials"]))
        if not report["tts"]["ready"]:
            gaps.append("no TTS provider: " + ", ".join(report["tts"]["missing_credentials"]))
        if not report["video"]["ready"]:
            gaps.append("no motion engine (ffmpeg missing?)")
        if plan.youtube_upload and not report["youtube"]["ready"]:
            gaps.append("youtube not connected: " + ", ".join(report["youtube"]["missing_credentials"]))
        return gaps


register(PlannerAgent())
