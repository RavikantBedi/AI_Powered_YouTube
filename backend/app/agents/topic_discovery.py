"""Topic Discovery Agent — used when the user says "make something interesting" and
when the dashboard asks for ranked ideas.

Requires documentary-grade evidence before a topic is proposed: each idea carries an
evidence note and a documentation-strength score, mirroring the channel's topic-scope
rule (only events with substantial public documentation).
"""
from __future__ import annotations

import json

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register

SYSTEM = """You are the Topic Discovery Agent for a documentary channel.

Propose video topics that are simultaneously high-interest AND well documented. Use web
search to check what is currently drawing attention and to confirm each topic has real
source material.

Hard requirements for every topic:
- Substantial public documentation must exist: court records, published books,
  newspaper investigations, official government records, or established scholarship.
- Recent-news topics must be verified events, not developing rumours or allegations
  about serving officials.
- Respect the channel's forbidden-topic list.

Score each idea:
- interest (0-100): audience pull, curiosity, emotional stakes
- documentation (0-100): how much verifiable primary/secondary material exists
- novelty (0-100): how under-covered it is on YouTube in this language

Return ONLY JSON:
{"topics": [{"title": "...", "category": "...", "hook": "one-line hook",
  "why_now": "...", "evidence": "the specific documented sources that exist",
  "interest": 0, "documentation": 0, "novelty": 0}]}"""


class TopicDiscoveryAgent(BaseAgent):
    agent_id = "topic-discovery-agent"
    name = "Topic Discovery Agent"
    version = 1
    capabilities = ["discover_topics", "rank_topics"]
    task_kind = "research"
    stage = "TOPIC_DISCOVERY"

    async def execute(self, ctx: AgentContext, *, count: int = 10, hint: str = "") -> list[dict]:
        channel = ctx.channel_dna
        user = (
            f"Channel: {channel.get('channel_name')} — {channel.get('niche')}\n"
            f"Language: {channel.get('language')}\n"
            f"Audience: {channel.get('audience')}\n"
            f"Preferred topic areas: {json.dumps(channel.get('preferred_topics', []), ensure_ascii=False)}\n"
            f"Forbidden: {json.dumps(channel.get('forbidden_topics', []), ensure_ascii=False)}\n"
            f"Evidence rule: {channel.get('topic_evidence_rule', '')}\n\n"
            f"Propose {count} topics. Include at least 2 drawn from verified news events of the "
            f"last 60 days that already have documented reporting.\n"
            + (f"Extra direction from the operator: {hint}\n" if hint else "")
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=12000,
                                   web_search=True)
        topics = data.get("topics", []) if isinstance(data, dict) else list(data)

        for t in topics:
            t["rank_score"] = round(
                0.4 * float(t.get("interest", 0))
                + 0.4 * float(t.get("documentation", 0))
                + 0.2 * float(t.get("novelty", 0)), 1)
        # A topic we cannot document is not a topic we can produce.
        topics = [t for t in topics if float(t.get("documentation", 0)) >= 60]
        topics.sort(key=lambda t: t["rank_score"], reverse=True)

        ctx.store.save_json("research/topic_ideas.json", topics)
        await ctx.emit("TOPICS_DISCOVERED", {"count": len(topics)})
        return topics


register(TopicDiscoveryAgent())
