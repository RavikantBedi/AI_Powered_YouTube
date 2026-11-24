"""Fact Verification Agent — an independent adversarial pass over the dossier.

Deliberately does NOT trust the Research Agent's own confidence scores: it re-searches
the significant claims and returns its own verdict. Claims it cannot substantiate are
rejected or qualified before a single word of script is written.
"""
from __future__ import annotations

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import QualityGateFailure
from app.schemas.core import ResearchDossier

SYSTEM = """You are the Fact Verification Agent. You are adversarial and independent —
another agent produced these claims and you must assume nothing.

For each claim, search independently and decide:
- "verified": multiple credible independent sources agree; safe to state as fact.
- "qualified": partly supported, contested, or attributable to one source only. Provide
  `qualification`: the exact hedged phrasing the script must use (e.g. "according to the
  CBI chargesheet...", "reportedly", "one account holds that...").
- "rejected": unsupported, contradicted by better sources, or untraceable. It must not
  appear in the script in any form.

Be strict about:
- Quotations — reject any quotation not traceable to a documented public statement,
  court record, or published transcript.
- Numbers and dates — verify precisely, not approximately.
- Attribution of motive or private conversation — almost always "qualified" at best.
- Claims about currently serving officials — verified public record only.

Return ONLY JSON:
{"results": [{"claim_id": "C001", "status": "verified|qualified|rejected",
  "confidence": 0.0, "corroboration": 0, "qualification": "",
  "reason": "why this verdict", "sources": [{"url": "...", "title": "...",
  "source_type": "...", "publication_date": "..."}]}],
 "overall_factual_confidence": 0,
 "notes": ["anything the script writer must be careful about"]}"""


class FactCheckAgent(BaseAgent):
    agent_id = "fact-check-agent"
    name = "Fact Verification Agent"
    version = 1
    capabilities = ["verify_claims", "detect_conflicts"]
    task_kind = "fact_check"
    stage = "FACT_CHECK"
    quality_threshold = 95.0

    # Verify in batches so each request stays focused and searchable.
    BATCH_SIZE = 12

    async def execute(self, ctx: AgentContext, *, dossier: ResearchDossier) -> ResearchDossier:
        claims = dossier.claims
        results: dict[str, dict] = {}
        notes: list[str] = []

        batches = [claims[i:i + self.BATCH_SIZE] for i in range(0, len(claims), self.BATCH_SIZE)]
        for bi, batch in enumerate(batches):
            await ctx.progress("FACT_CHECK", 100 * bi / max(len(batches), 1),
                               f"verifying batch {bi + 1}/{len(batches)}")
            listing = "\n".join(
                f"{c.claim_id}: {c.claim}\n   [research agent cited: "
                f"{', '.join(s.url or s.title for s in c.sources) or 'nothing'}]"
                for c in batch)
            user = (
                f"TOPIC: {dossier.topic}\n\n"
                f"Verify each of these claims independently. Search for each one.\n\n{listing}"
            )
            data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=20000,
                                       web_search=True)
            for r in data.get("results", []):
                results[r.get("claim_id", "")] = r
            notes.extend(data.get("notes", []))

        verified = qualified = rejected = 0
        for claim in claims:
            r = results.get(claim.claim_id)
            if not r:
                # No verdict returned — fail closed rather than assuming it's fine.
                claim.status = "rejected"
                rejected += 1
                continue
            status = r.get("status", "rejected")
            claim.status = status if status in {"verified", "qualified", "rejected"} else "rejected"
            claim.confidence = float(r.get("confidence", claim.confidence))
            claim.corroboration = int(r.get("corroboration", claim.corroboration))
            claim.qualification = r.get("qualification", "")
            if claim.status == "verified":
                verified += 1
            elif claim.status == "qualified":
                qualified += 1
            else:
                rejected += 1

        usable = verified + qualified
        # Gate: the proportion of the evidence base that survived independent scrutiny.
        score = 100.0 * usable / len(claims) if claims else 0.0

        dossier.open_questions.extend(notes)
        ctx.store.save_json("research/facts.json", dossier)
        ctx.store.save_json("research/fact_check_report.json", {
            "verified": verified, "qualified": qualified, "rejected": rejected,
            "usable_ratio": round(score, 1), "notes": notes,
        })

        await ctx.emit("FACTS_VERIFIED", {"verified": verified, "qualified": qualified,
                                          "rejected": rejected, "score": round(score, 1)})

        if verified < 8:
            raise QualityGateFailure("FACT_CHECK", score, float(ctx.settings.gate_fact_check), {
                "reason": f"only {verified} claims independently verified — too thin for a "
                          f"factual documentary",
                "rejected": rejected,
            })
        return dossier


register(FactCheckAgent())
