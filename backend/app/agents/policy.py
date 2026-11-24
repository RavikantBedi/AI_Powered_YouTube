"""YouTube Policy Agent — the last gate before anything leaves the building.

Reviews the script, the metadata, and the chosen thumbnail against platform policy and
advertiser suitability. Auto-fixable problems (an overstated title, a missing
qualification, a hashtag that reads as clickbait) are repaired in place. Anything that
cannot be safely repaired sets BLOCKED — the system never publishes past an unresolved
policy finding.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent, wrap_untrusted
from app.agents.registry import register
from app.schemas.core import YouTubeMetadata

log = logging.getLogger("agency.policy")

SYSTEM = """You are the Platform Policy Agent reviewing a documentary before publication.

Review the script, metadata, and thumbnail against these areas:
- misleading_content: does the title/thumbnail promise something the video does not
  deliver?
- misinformation: are contested claims presented as settled fact? Are qualifications
  present where the evidence is thin?
- harassment: does it target a private individual, or attack rather than document?
- impersonation: does it present itself as an official source, or use a real person's
  likeness deceptively?
- graphic_content: is violence depicted rather than described? Documentaries may
  describe violence factually; they may not dwell on gore.
- dangerous_content: instructions or glorification of harmful acts.
- copyright_risk: assertions of using third-party footage, music, or text.
- metadata_violations: keyword stuffing, unrelated tags, ALL CAPS, excessive punctuation.
- thumbnail_policy: shocking imagery, gore, or content not in the video.
- advertiser_suitability: would this be demonetised? Note the reason if so.

For each finding give severity: "critical" (must not publish), "high" (must fix),
"medium" (should fix), "low" (note only). Provide `auto_fix` where a concrete text change
resolves it.

Return ONLY JSON:
{"findings": [{"area": "...", "severity": "...", "description": "...", "auto_fix": ""}],
 "advertiser_suitability": "suitable|limited|unsuitable",
 "verdict": "PASS|FIX|BLOCK",
 "revised_title": "", "revised_description": ""}

Use BLOCK only when the problem cannot be resolved by changing text or swapping the
thumbnail."""


class PolicyAgent(BaseAgent):
    agent_id = "policy-agent"
    name = "YouTube Policy Agent"
    version = 1
    capabilities = ["check_policy", "repair_metadata"]
    task_kind = "policy"
    stage = "POLICY"

    async def execute(self, ctx: AgentContext, *, script_text: str,
                      metadata: YouTubeMetadata,
                      thumbnail_path: Path | None = None) -> dict:
        user = (
            f"METADATA UNDER REVIEW:\n"
            f"Title: {metadata.title}\n"
            f"Description:\n{metadata.description}\n"
            f"Tags: {', '.join(metadata.tags[:30])}\n"
            f"Hashtags: {' '.join(metadata.hashtags)}\n\n"
            f"CHANNEL FORBIDDEN TOPICS: "
            f"{json.dumps(ctx.channel_dna.get('forbidden_topics', []), ensure_ascii=False)}\n\n"
            f"{wrap_untrusted(script_text[:40000], 'script_under_review')}\n\n"
            f"Review for platform policy compliance."
        )
        if thumbnail_path and Path(thumbnail_path).exists():
            data = await self.vision_json(ctx, system=SYSTEM,
                                          user=f"The attached image is the chosen thumbnail.\n\n{user}",
                                          images=[Path(thumbnail_path)], max_tokens=8000,
                                          task="policy")
        else:
            data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=8000)

        findings = data.get("findings", [])
        critical = [f for f in findings if f.get("severity") == "critical"]
        high = [f for f in findings if f.get("severity") == "high"]

        applied: list[str] = []
        if data.get("revised_title") and (critical or high):
            metadata.title = data["revised_title"][:100]
            applied.append("title")
        if data.get("revised_description") and (critical or high):
            metadata.description = data["revised_description"]
            applied.append("description")
        if applied:
            ctx.store.save_json("metadata/youtube.json", metadata)

        verdict = data.get("verdict", "PASS")
        # A critical finding blocks regardless of the model's own verdict.
        if critical:
            verdict = "BLOCK"
        elif high and not applied:
            verdict = "FIX"
        elif verdict == "FIX" and applied:
            verdict = "PASS"

        report = {
            "verdict": verdict,
            "findings": findings,
            "advertiser_suitability": data.get("advertiser_suitability", "suitable"),
            "auto_fixes_applied": applied,
        }
        ctx.store.save_json("qa/policy.json", report)
        await ctx.emit("POLICY_CHECKED", {"verdict": verdict, "findings": len(findings)})
        return report


register(PolicyAgent())
