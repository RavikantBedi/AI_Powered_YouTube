"""Deep Research Agent — builds the evidence base the whole production rests on.

Uses Anthropic's server-side web search so retrieval and reasoning share one context.
Output is a RESEARCH_DOSSIER of atomic claims, each with sources, confidence, and
corroboration count, plus a documentation-strength score that gates the whole project:
a topic that cannot be documented does not become a video.
"""
from __future__ import annotations

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import QualityGateFailure
from app.schemas.core import Claim, ResearchDossier, SourceRef

SYSTEM = """You are the Deep Research Agent for a documentary production company.

Investigate the assigned topic using web search. Prioritise, in order:
1. Primary/official records — court judgments, government archives, commission reports,
   declassified documents, parliamentary records.
2. Established scholarship and published books by named authors.
3. Credible journalism from named outlets with datelines.
Treat blogs, forums, social posts, and AI-generated content as leads only, never as
evidence.

Decompose your findings into ATOMIC CLAIMS. One claim = one checkable factual statement
(a date, a place, an action, an outcome). For each claim record:
- the exact sources supporting it (url + outlet/title + type + publication date)
- the evidence: what the source actually says
- confidence 0.0-1.0
- corroboration: how many INDEPENDENT sources support it

Discipline:
- Distinguish established fact from allegation, from disputed claim, from rumour. Label
  contested material in `open_questions` and lower its confidence.
- Never invent a quotation, a document, a URL, or a date. If you cannot source it, omit it.
- Where accounts conflict, record both versions and say who says what.
- Note whether each named person is deceased/historical or a currently serving official —
  the visual pipeline treats them differently.

Also score `documentation_strength` 0-100: how well this specific topic is documented in
the public record. Below 60 means the topic should not be produced as a factual
documentary.

Return ONLY JSON:
{"summary": "300-500 word factual overview",
 "documentation_strength": 0,
 "timeline": [{"date": "YYYY-MM-DD or YYYY", "event": "...", "source_urls": ["..."]}],
 "people": [{"name": "...", "role": "...", "notes": "...", "deceased": true,
             "currently_serving_official": false}],
 "places": ["..."],
 "claims": [{"claim": "...", "sources": [{"url": "...", "title": "...",
             "source_type": "court_record|government|book|journalism|web",
             "publication_date": "..."}],
             "evidence": "...", "confidence": 0.0, "corroboration": 0}],
 "open_questions": ["contested or unresolved points"]}"""


class ResearchAgent(BaseAgent):
    agent_id = "research-agent"
    name = "Deep Research Agent"
    version = 1
    capabilities = ["research_topic", "build_dossier", "extract_timeline"]
    task_kind = "research"
    stage = "RESEARCH"
    quality_threshold = 90.0

    async def execute(self, ctx: AgentContext, *, topic: str, depth: str = "deep") -> ResearchDossier:
        await ctx.progress("RESEARCH", 5, "searching sources")
        target_claims = {"deep": "35-60", "standard": "20-35", "shallow": "12-20"}.get(depth, "25-40")

        user = (
            f"TOPIC: {topic}\n\n"
            f"Language of the final video: {ctx.channel_dna.get('language', 'hi-IN')} "
            f"(research in any language, report in English).\n"
            f"Research depth: {depth}. Produce {target_claims} atomic claims covering the full "
            f"arc of the story: background, the central events, the people involved, the "
            f"aftermath, and the documented consequences.\n\n"
            f"Search thoroughly before answering. Cover contradictions explicitly."
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=32000,
                                   web_search=True)
        await ctx.progress("RESEARCH", 70, "structuring dossier")

        claims: list[Claim] = []
        for i, raw in enumerate(data.get("claims", [])):
            sources = [SourceRef(
                url=s.get("url", ""), title=s.get("title", ""),
                source_type=s.get("source_type", "web"),
                publication_date=s.get("publication_date", ""),
            ) for s in raw.get("sources", [])]
            claims.append(Claim(
                claim_id=f"C{i + 1:03d}",
                claim=raw.get("claim", ""),
                sources=sources,
                evidence=raw.get("evidence", ""),
                confidence=float(raw.get("confidence", 0.0)),
                corroboration=int(raw.get("corroboration", len(sources))),
            ))

        dossier = ResearchDossier(
            topic=topic,
            summary=data.get("summary", ""),
            timeline=data.get("timeline", []),
            people=data.get("people", []),
            places=data.get("places", []),
            claims=claims,
            open_questions=data.get("open_questions", []),
            documentation_strength=float(data.get("documentation_strength", 0)),
        )

        ctx.store.save_json("research/dossier.json", dossier)
        ctx.store.save_json("research/sources.json",
                            [s.model_dump() for c in claims for s in c.sources])
        ctx.store.save_json("research/timeline.json", dossier.timeline)

        threshold = float(ctx.settings.gate_research)
        if dossier.documentation_strength < 60:
            raise QualityGateFailure("RESEARCH", dossier.documentation_strength, 60, {
                "reason": "topic lacks sufficient public documentation for a factual documentary",
                "open_questions": dossier.open_questions,
            })
        if not claims:
            raise QualityGateFailure("RESEARCH", 0, threshold,
                                     {"reason": "no verifiable claims extracted"})

        await ctx.emit("RESEARCH_COMPLETED", {
            "claims": len(claims),
            "sources": sum(len(c.sources) for c in claims),
            "documentation_strength": dossier.documentation_strength,
        })
        return dossier


register(ResearchAgent())
