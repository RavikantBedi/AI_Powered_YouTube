"""Script Writer Agent — produces the final voiceover-ready narration.

Written in sections (groups of beats) rather than one giant generation: long scripts
drift off target length and lose the style register near the end. Each section receives
the previous section's tail for continuity, its own word budget, and the verified
evidence for exactly its beats. A length-correction pass then brings the total inside
the DNA's ±5% band.
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import ResearchDossier, Script, StoryStructure

log = logging.getLogger("agency.script")

BEATS_PER_SECTION = 4

SYSTEM = """You are the Script Writer for a documentary channel. You write the finished
narration that goes straight into a text-to-speech voiceover.

ABSOLUTE OUTPUT RULES:
- Output ONLY the narration prose. No headings, no scene labels, no speaker names, no
  stage directions, no [MUSIC] tags, no markdown, no English translation, no commentary
  about what you wrote.
- The single permitted inline marker is [TENSION PEAK] on its own, and only where the
  brief says a beat is a tension peak.
- Write in the target language specified in the brief, in that language's native script.

FACTUAL RULES (non-negotiable):
- You may only state what the supplied verified evidence supports.
- A claim marked "qualified" must be delivered with its supplied hedging phrase.
- Never invent a quotation. For undocumented private conversation use indirect narration
  ("unhone kaha ki...") rather than direct speech.
- No speculation presented as fact. No invented dates, numbers, names, or places.

CRAFT RULES: follow the supplied STYLE DNA exactly — sentence rhythm, register, tone,
transitions, curiosity gaps, direct address limits, and the ending pattern."""


def count_words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def clean_script(text: str) -> str:
    """Strip anything that would be read aloud by mistake or break the voiceover."""
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE)      # headings
    text = re.sub(r"^\s*\*{1,2}[^*\n]+\*{1,2}\s*$", "", text, flags=re.MULTILINE)  # bold-only lines
    text = re.sub(r"\[(?!TENSION PEAK\])[^\]\n]{0,80}\]", "", text)        # stray [tags]
    text = re.sub(r"^\s*(SCENE|SECTION|PART|BEAT|NARRATOR|VO)\b.*$", "", text,
                  flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ScriptWriterAgent(BaseAgent):
    agent_id = "script-agent"
    name = "Script Writer Agent"
    version = 1
    capabilities = ["write_script", "revise_script"]
    task_kind = "script"
    stage = "SCRIPT"

    async def execute(self, ctx: AgentContext, *, dossier: ResearchDossier,
                      structure: StoryStructure, word_target: int, language: str,
                      previous: Script | None = None,
                      critique: dict | None = None) -> Script:
        if previous is not None and critique:
            return await self._revise(ctx, previous, critique, dossier, word_target, language)
        return await self._write(ctx, dossier, structure, word_target, language)

    # ── first draft ────────────────────────────────────────────────
    async def _write(self, ctx: AgentContext, dossier: ResearchDossier,
                     structure: StoryStructure, word_target: int, language: str) -> Script:
        claims = {c.claim_id: c for c in dossier.claims}
        dna = json.dumps(ctx.script_dna, ensure_ascii=False, indent=2)
        sections = [structure.beats[i:i + BEATS_PER_SECTION]
                    for i in range(0, len(structure.beats), BEATS_PER_SECTION)]

        parts: list[str] = []
        for si, beats in enumerate(sections):
            await ctx.progress("SCRIPT", 90 * si / max(len(sections), 1),
                               f"writing section {si + 1}/{len(sections)}")
            budget = sum(b.target_words for b in beats)
            evidence_lines = []
            for b in beats:
                for cid in b.claims_used:
                    c = claims.get(cid)
                    if c and c.status in {"verified", "qualified"}:
                        line = f"- [{c.status}] {c.claim}"
                        if c.qualification:
                            line += f"   >> must be phrased as: {c.qualification}"
                        evidence_lines.append(line)
            beat_brief = "\n".join(
                f"{b.beat_id} ({b.section}{', TENSION PEAK' if b.tension_peak else ''}), "
                f"~{b.target_words} words: {b.summary}" for b in beats)

            tail = parts[-1][-900:] if parts else ""
            position = ("This is the OPENING of the video — the first sentence must hook."
                        if si == 0 else
                        "This is the FINAL section — it must resolve, state the wider "
                        "significance, and close with the channel's CTA."
                        if si == len(sections) - 1 else
                        "This is a middle section — continue seamlessly, escalate.")

            user = (
                f"LANGUAGE: {language}\n"
                f"TOPIC: {dossier.topic}\n"
                f"LOGLINE: {structure.logline}\n\n"
                f"STYLE DNA:\n{dna}\n\n"
                f"{position}\n\n"
                f"BEATS TO WRITE NOW:\n{beat_brief}\n\n"
                f"VERIFIED EVIDENCE FOR THESE BEATS:\n"
                + ("\n".join(dict.fromkeys(evidence_lines)) or "(none — keep this section "
                   "purely transitional and factually neutral)")
                + (f"\n\nPREVIOUS SECTION ENDED WITH:\n...{tail}\n\nContinue seamlessly — do "
                   f"not repeat it, do not recap.\n" if tail else "\n")
                + f"\nWrite approximately {budget} words. Output narration only."
            )
            text = await self.llm_text(ctx, system=SYSTEM, user=user,
                                       max_tokens=max(4000, budget * 6))
            parts.append(clean_script(text))

        script_text = "\n\n".join(parts)
        script_text = await self._fit_length(ctx, script_text, word_target, language)
        return self._finalize(ctx, script_text, language, version=1)

    # ── revision after critique ────────────────────────────────────
    async def _revise(self, ctx: AgentContext, previous: Script, critique: dict,
                      dossier: ResearchDossier, word_target: int, language: str) -> Script:
        await ctx.progress("SCRIPT", 20, "revising against critique")
        fixes = critique.get("required_fixes", [])
        unsupported = critique.get("unsupported_claims", [])
        usable = "\n".join(
            f"- [{c.status}] {c.claim}" + (f" >> phrase as: {c.qualification}" if c.qualification else "")
            for c in dossier.claims if c.status in {"verified", "qualified"})

        user = (
            f"LANGUAGE: {language}\n"
            f"STYLE DNA:\n{json.dumps(ctx.script_dna, ensure_ascii=False, indent=2)}\n\n"
            f"A critic reviewed the script below. Rewrite it addressing every point.\n\n"
            f"REQUIRED FIXES:\n" + "\n".join(f"- {f}" for f in fixes) + "\n\n"
            + ("UNSUPPORTED CLAIMS — remove or properly qualify each one:\n"
               + "\n".join(f"- {u}" for u in unsupported) + "\n\n" if unsupported else "")
            + f"THE ONLY EVIDENCE YOU MAY USE:\n{usable}\n\n"
            f"CURRENT SCRIPT:\n{previous.text}\n\n"
            f"Return the complete revised script, approximately {word_target} words, "
            f"narration only."
        )
        text = await self.llm_text(ctx, system=SYSTEM, user=user, max_tokens=64000)
        text = await self._fit_length(ctx, clean_script(text), word_target, language)
        return self._finalize(ctx, text, language, version=previous.version + 1)

    # ── length correction ──────────────────────────────────────────
    async def _fit_length(self, ctx: AgentContext, text: str, target: int, language: str) -> str:
        tolerance = float(ctx.script_dna.get("word_count_target", {}).get("tolerance_pct", 5)) / 100
        low, high = target * (1 - tolerance), target * (1 + tolerance)
        words = count_words(text)
        if low <= words <= high:
            return text

        direction = "EXPAND" if words < low else "CONDENSE"
        pct = abs(words - target) / max(target, 1) * 100
        log.info("script length %d vs target %d (%s by %.0f%%)", words, target, direction, pct)
        await ctx.progress("SCRIPT", 95, f"{direction.lower()}ing to target length")

        user = (
            f"LANGUAGE: {language}\n"
            f"The script below is {words} words. It must be approximately {target} words.\n"
            f"{direction} it to hit that target.\n\n"
            + ("Expand by deepening documented scenes with sensory and procedural detail "
               "drawn from what is already stated. Add NO new factual claims, names, dates, "
               "or quotations.\n"
               if direction == "EXPAND" else
               "Condense by tightening sentences and cutting repetition. Preserve every "
               "factual claim, the hook, all tension peaks, and the ending.\n")
            + f"\nSCRIPT:\n{text}\n\nReturn the adjusted script only."
        )
        adjusted = await self.llm_text(ctx, system=SYSTEM, user=user, max_tokens=64000)
        adjusted = clean_script(adjusted)
        # Keep the adjustment only if it actually moved toward target.
        if abs(count_words(adjusted) - target) < abs(words - target):
            return adjusted
        return text

    def _finalize(self, ctx: AgentContext, text: str, language: str, version: int) -> Script:
        words = count_words(text)
        script = Script(
            version=version, text=text, word_count=words, language=language,
            duration_estimates={
                "50wpm": round(words / 50, 1),
                "100wpm": round(words / 100, 1),
                "160wpm": round(words / 160, 1),
            },
        )
        ctx.store.save_text(f"script/script_v{version}.txt", text)
        ctx.store.save_json(f"script/script_v{version}.json", script)
        return script


register(ScriptWriterAgent())
