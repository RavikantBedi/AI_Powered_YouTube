"""Story Architect Agent — converts verified facts into a retention-shaped narrative
structure without bending the facts.

Every beat cites the claim IDs it draws on, so the Critic can check that the story never
outruns the evidence. Word budgets per beat are allocated from the plan's total so the
script lands on target length by construction.
"""
from __future__ import annotations

import json

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import ResearchDossier, StoryBeat, StoryStructure

SYSTEM = """You are the Story Architect for a documentary channel.

Turn verified research into a beat-by-beat narrative structure that maximises retention
WITHOUT distorting the facts. Every beat must be grounded in specific claim IDs from the
verified evidence. If a dramatic beat has no evidence, cut it — do not invent it.

Structure sections, in order:
hook, background, inciting_incident, escalation (multiple beats), climax, resolution,
significance, cta

Craft rules:
- The hook opens mid-action with a precise date, a city, and a concrete sensory detail,
  and raises a question it does not answer.
- Escalation beats must each deliver a NEW revelation or raise the stakes; nothing may
  restate an earlier beat.
- Place a curiosity gap early and close it deliberately later.
- Mark the 3-5 highest-tension beats with tension_peak: true.
- Emotional weight comes from specific documented story beats (a name, a decision, a
  loss), never from abstract commentary.
- The CTA beat is a subscribe/like request delivered in the same grave narrator voice.

Return ONLY JSON:
{"logline": "one sentence",
 "beats": [{"section": "hook", "summary": "what this beat covers, in English",
            "claims_used": ["C001"], "weight": 1.0, "tension_peak": false}]}
`weight` is that beat's relative share of the runtime."""


class StoryArchitectAgent(BaseAgent):
    agent_id = "story-architect-agent"
    name = "Story Architect Agent"
    version = 1
    capabilities = ["design_narrative", "structure_beats"]
    task_kind = "story"
    stage = "STORY"

    async def execute(self, ctx: AgentContext, *, dossier: ResearchDossier,
                      word_target: int) -> StoryStructure:
        usable = [c for c in dossier.claims if c.status in {"verified", "qualified"}]
        evidence = "\n".join(
            f"{c.claim_id} [{c.status}]: {c.claim}"
            + (f"  (must be phrased as: {c.qualification})" if c.qualification else "")
            for c in usable)

        user = (
            f"TOPIC: {dossier.topic}\n\n"
            f"SUMMARY:\n{dossier.summary}\n\n"
            f"TIMELINE:\n{json.dumps(dossier.timeline, ensure_ascii=False)[:6000]}\n\n"
            f"VERIFIED EVIDENCE (the only material you may build on):\n{evidence}\n\n"
            f"NARRATIVE DNA:\n{json.dumps(ctx.script_dna, ensure_ascii=False, indent=2)}\n\n"
            f"Target narration length: {word_target} words. Retention rule: a new revelation "
            f"or escalation every 90-120 seconds of narration (~150-200 words).\n"
            f"Produce roughly {max(12, word_target // 220)} beats."
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=16000)

        raw_beats = data.get("beats", [])
        total_weight = sum(float(b.get("weight", 1.0)) for b in raw_beats) or 1.0
        beats = [
            StoryBeat(
                beat_id=f"B{i + 1:03d}",
                section=b.get("section", "escalation"),
                summary=b.get("summary", ""),
                claims_used=b.get("claims_used", []),
                target_words=int(word_target * float(b.get("weight", 1.0)) / total_weight),
                tension_peak=bool(b.get("tension_peak", False)),
            )
            for i, b in enumerate(raw_beats)
        ]

        structure = StoryStructure(logline=data.get("logline", ""), beats=beats)
        ctx.store.save_json("script/story_structure.json", structure)
        await ctx.emit("STORY_READY", {"beats": len(beats), "logline": structure.logline})
        return structure


register(StoryArchitectAgent())
