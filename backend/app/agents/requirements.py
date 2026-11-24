"""Input Agent — turns one plain sentence into a full RequirementSpec.

Infers everything the user did not say from the request plus Channel DNA. Never asks
follow-up questions: missing fields get intelligent, DNA-derived defaults (spec §3).
"""
from __future__ import annotations

import json

from app.agents.base import AgentContext, BaseAgent, wrap_untrusted
from app.agents.registry import register
from app.schemas.core import RequirementSpec

SYSTEM = """You are the Input Agent of an autonomous video production company.

Convert one natural-language production request into a complete requirement
specification. The user will not be asked follow-up questions, so every field must be
filled with either what the request states or the most sensible default derived from
the channel's DNA.

Rules:
- duration_seconds: parse any stated length ("12 minutes" -> 720). If unstated, use the
  channel's preferred duration midpoint.
- topic: a precise, self-contained subject line. Resolve vague requests ("something
  interesting") to the literal string "TOPIC_DISCOVERY_REQUIRED" so a discovery agent
  can take over.
- publication_goal: "publish" if the user mentions publishing/uploading, otherwise
  "draft".
- privacy_status: "private" unless the user explicitly asks for public or unlisted.
- language / visual_style / narration_style / storytelling_style / audience: take from
  channel DNA unless the request overrides them.
- factuality_requirement: "strict" for documentary/history/news topics.

Return ONLY a JSON object with exactly these keys:
topic, language, duration_seconds, genre, audience, storytelling_style, visual_style,
narration_style, platform, aspect_ratio, publication_goal, quality_level,
factuality_requirement, seo_goal, privacy_status."""


class RequirementsAgent(BaseAgent):
    agent_id = "requirements-agent"
    name = "Input / Requirement Agent"
    version = 1
    capabilities = ["parse_requirement", "infer_defaults"]
    task_kind = "requirements"
    stage = "REQUIREMENTS"

    async def execute(self, ctx: AgentContext, *, request_text: str) -> RequirementSpec:
        channel = ctx.channel_dna
        prefs = channel.get("preferred_duration_seconds", {"min": 600, "max": 900})
        default_duration = int((prefs.get("min", 600) + prefs.get("max", 900)) / 2)

        user = (
            f"{wrap_untrusted(request_text, 'user_request')}\n\n"
            f"CHANNEL DNA (trusted operator configuration):\n"
            f"{json.dumps(channel, ensure_ascii=False, indent=2)}\n\n"
            f"Default duration if unstated: {default_duration} seconds.\n"
            f"Produce the requirement specification JSON."
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=4000, effort="medium")

        spec = RequirementSpec(
            topic=str(data.get("topic") or request_text)[:500],
            language=data.get("language") or channel.get("language", "hi-IN"),
            duration_seconds=int(data.get("duration_seconds") or default_duration),
            genre=data.get("genre", "documentary"),
            audience=data.get("audience") or channel.get("audience", ""),
            storytelling_style=data.get("storytelling_style", ""),
            visual_style=data.get("visual_style", ""),
            narration_style=data.get("narration_style", ""),
            platform=data.get("platform", "youtube"),
            aspect_ratio=data.get("aspect_ratio") or channel.get("aspect_ratio", "16:9"),
            publication_goal=data.get("publication_goal", "publish"),
            quality_level=data.get("quality_level", "high"),
            factuality_requirement=data.get("factuality_requirement", "strict"),
            seo_goal=data.get("seo_goal", "search + browse CTR"),
            privacy_status=data.get("privacy_status")
                or channel.get("publishing", {}).get("default_privacy", "private"),
        )
        # Guardrail: keep duration inside a renderable band.
        spec.duration_seconds = max(120, min(spec.duration_seconds, 3600))
        ctx.store.save_json("requirements.json", spec)
        await ctx.emit("REQUIREMENTS_READY", {"topic": spec.topic,
                                              "duration": spec.duration_seconds})
        return spec


register(RequirementsAgent())
