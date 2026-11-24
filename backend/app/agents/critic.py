"""Script Critic Agent — the gate the first draft never passes by default.

Scores six dimensions and, critically, re-checks every factual assertion in the script
against the verified claim set. Anything the evidence does not support becomes a
required fix, and the Script Writer must revise. Also runs deterministic structural
checks (word count, forbidden markers, repetition) that a model shouldn't be asked to
eyeball.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import CritiqueReport, ResearchDossier, Script

SYSTEM = """You are the Script Critic — a demanding, independent reviewer. Your default
posture is that the script needs work. Praise nothing; find what is wrong.

Score 0-100 on each dimension:
- factual_accuracy: does every assertion trace to the supplied verified evidence? Any
  unsupported claim, invented quotation, wrong date, or speculation stated as fact caps
  this below 70.
- story_quality: hook strength, structure, escalation, curiosity gaps, payoff.
- language: grammar, register, idiom, and fidelity to the style DNA's language rules.
- retention: does a new revelation or escalation land every 90-120 seconds? Flag any flat
  stretch by quoting its opening words.
- brand_alignment: tone, sentence rhythm, transitions, direct-address limits, CTA style.
- clarity: would a first-time listener follow the chronology and the cast?

Then list:
- problems: everything wrong, most severe first.
- required_fixes: specific, actionable rewrite instructions.
- unsupported_claims: quote the exact script phrases that the evidence does not support.

verdict: "approve" only if factual_accuracy >= 95 AND every other dimension >= 85.
Otherwise "revise". Use "reject" only if the script is unsalvageable.

Return ONLY JSON:
{"scores": {"factual_accuracy": 0, "story_quality": 0, "language": 0, "retention": 0,
 "brand_alignment": 0, "clarity": 0},
 "problems": ["..."], "required_fixes": ["..."], "unsupported_claims": ["..."],
 "verdict": "approve|revise|reject"}"""


class ScriptCriticAgent(BaseAgent):
    agent_id = "script-critic-agent"
    name = "Script Critic Agent"
    version = 1
    capabilities = ["critique_script", "score_script"]
    task_kind = "critic"
    stage = "SCRIPT_CRITIQUE"
    quality_threshold = 90.0

    async def execute(self, ctx: AgentContext, *, script: Script, dossier: ResearchDossier,
                      word_target: int) -> CritiqueReport:
        evidence = "\n".join(
            f"[{c.status}] {c.claim}" + (f"  >> phrase as: {c.qualification}" if c.qualification else "")
            for c in dossier.claims if c.status in {"verified", "qualified"})
        rejected = [c.claim for c in dossier.claims if c.status == "rejected"]

        user = (
            f"TOPIC: {dossier.topic}\n"
            f"TARGET LENGTH: {word_target} words. ACTUAL: {script.word_count} words.\n\n"
            f"STYLE DNA:\n{json.dumps(ctx.script_dna, ensure_ascii=False, indent=2)}\n\n"
            f"VERIFIED EVIDENCE (the complete set of what may be stated):\n{evidence}\n\n"
            + ("CLAIMS THAT WERE REJECTED IN FACT-CHECKING — their presence in the script "
               "is a serious defect:\n" + "\n".join(f"- {r}" for r in rejected[:25]) + "\n\n"
               if rejected else "")
            + f"SCRIPT UNDER REVIEW:\n{script.text}\n\nReview it."
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=16000)

        scores = {k: float(v) for k, v in (data.get("scores") or {}).items()}
        problems = list(data.get("problems", []))
        fixes = list(data.get("required_fixes", []))

        structural = self._structural_checks(script, word_target, ctx)
        problems.extend(structural["problems"])
        fixes.extend(structural["fixes"])
        for dim, penalty in structural["penalties"].items():
            scores[dim] = max(0.0, scores.get(dim, 80.0) - penalty)

        # Factual accuracy dominates: a factually weak script is not a good script.
        weights = {"factual_accuracy": 0.35, "story_quality": 0.20, "language": 0.15,
                   "retention": 0.15, "brand_alignment": 0.10, "clarity": 0.05}
        overall = sum(scores.get(k, 0.0) * w for k, w in weights.items())

        verdict = data.get("verdict", "revise")
        if scores.get("factual_accuracy", 0) < 95 or overall < ctx.settings.gate_script:
            verdict = "revise" if verdict != "reject" else "reject"

        report = CritiqueReport(
            scores={**scores, "overall": round(overall, 1)},
            overall=round(overall, 1),
            problems=problems,
            required_fixes=fixes,
            unsupported_claims=list(data.get("unsupported_claims", [])),
            verdict=verdict,
        )
        ctx.store.save_json(f"script/critique_v{script.version}.json", report)
        await ctx.emit("SCRIPT_CRITIQUED", {"version": script.version,
                                            "overall": report.overall,
                                            "verdict": report.verdict})
        return report

    def _structural_checks(self, script: Script, word_target: int, ctx: AgentContext) -> dict:
        """Deterministic checks — cheaper and more reliable than asking a model."""
        problems: list[str] = []
        fixes: list[str] = []
        penalties: dict[str, float] = {}
        text = script.text

        tol = float(ctx.script_dna.get("word_count_target", {}).get("tolerance_pct", 5)) / 100
        drift = abs(script.word_count - word_target) / max(word_target, 1)
        if drift > tol:
            problems.append(f"Length off target: {script.word_count} vs {word_target} words "
                            f"({drift * 100:.0f}% drift).")
            fixes.append(f"Adjust the script to approximately {word_target} words.")
            penalties["brand_alignment"] = penalties.get("brand_alignment", 0) + min(drift * 100, 20)

        forbidden = re.findall(r"\[(?!TENSION PEAK\])[^\]\n]{1,60}\]|^#{1,6}\s|\*\*",
                               text, flags=re.MULTILINE)
        if forbidden:
            problems.append(f"Non-narration markup present ({len(forbidden)} occurrences) — the "
                            f"script must be clean voiceover prose.")
            fixes.append("Remove all headings, markdown, and bracketed tags except [TENSION PEAK].")
            penalties["brand_alignment"] = penalties.get("brand_alignment", 0) + 10

        peaks = text.count("[TENSION PEAK]")
        if peaks == 0:
            problems.append("No tension peaks marked — retention structure is unverifiable.")
            fixes.append("Mark the 3-5 highest-tension moments with [TENSION PEAK].")
            penalties["retention"] = penalties.get("retention", 0) + 8

        # Repetition: a sentence opening reused many times reads as padding.
        openers = Counter()
        for sentence in re.split(r"[।.!?]\s+", text):
            words = sentence.strip().split()
            if len(words) >= 3:
                openers[" ".join(words[:3])] += 1
        repeats = [op for op, n in openers.items() if n >= 4]
        if repeats:
            problems.append(f"Repetitive sentence openings: {', '.join(repeats[:4])}")
            fixes.append("Vary sentence openings; the narration is repeating its cadence.")
            penalties["language"] = penalties.get("language", 0) + 6

        if re.search(r'[""«»]', text) or re.search(r'"[^"\n]{25,}"', text):
            problems.append("Direct quotations detected — each must be traceable to a documented "
                            "public statement or court record, otherwise use indirect narration.")
            fixes.append("Verify every quotation against the evidence or convert it to indirect "
                         "narration.")

        return {"problems": problems, "fixes": fixes, "penalties": penalties}


register(ScriptCriticAgent())
