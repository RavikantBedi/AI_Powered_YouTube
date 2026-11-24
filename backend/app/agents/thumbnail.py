"""Thumbnail Agent — generates concepts, renders them, scores them by looking at the
results, and picks a winner.

Scoring is done on the rendered images (not the concepts) because thumbnail quality is
almost entirely about what actually came out: whether the headline is legible at phone
size, whether the face reads, whether it pops against a dark UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.costs import IMAGE_PRICE_DEFAULT, record_cost
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.schemas.core import ThumbnailConcept

log = logging.getLogger("agency.thumbnail")

CONCEPT_SYSTEM = """You are the Thumbnail Agent for a documentary channel. You design
click-worthy thumbnails that are accurate to the video's content.

For each concept produce a complete, standalone AI image-generation prompt that
implements the channel's thumbnail DNA exactly: headline treatment, text placement,
character count and expression, split composition, colour contrast, emotion trigger, and
the mandatory badge.

Requirements:
- The Hindi headline must be stated in the prompt as the exact text to render, in
  Devanagari, with the one highlighted keyword identified.
- Titles must be accurate to what the video actually documents. No claim the video does
  not support, no fabricated shock.
- Visual tension comes from weather, light, silhouettes, and documents — never gore.
- Any person who is a currently serving official must appear only symbolically.

Return ONLY JSON:
{"concepts": [{"hindi_title": "...", "characters": "who and their expression",
  "background": "the split composition", "colors": "...", "emotion_trigger": "...",
  "image_prompt": "the full standalone generation prompt"}]}"""

SCORE_SYSTEM = """You are a YouTube thumbnail analyst. You are shown rendered thumbnail
candidates for one documentary video.

Score each 0-100 on:
- readability: is the headline legible as a 246x138px phone-sized thumbnail? Long text,
  thin type, or text over busy areas score low.
- text_quality: is the Devanagari text correctly formed, or garbled/misspelled/invented?
  Garbled script is disqualifying — score 0-20.
- emotional_clarity: does one clear emotion read instantly?
- visual_hierarchy: does the eye land on face, then headline, then context?
- contrast: does it pop against a dark interface?
- brand_alignment: does it match the described channel formula, badge included?
- curiosity: does it raise a question worth clicking?

ctr_potential 0-100 is your overall judgement.

Return ONLY JSON:
{"results": [{"index": 1, "readability": 0, "text_quality": 0, "emotional_clarity": 0,
 "visual_hierarchy": 0, "contrast": 0, "brand_alignment": 0, "curiosity": 0,
 "ctr_potential": 0, "problems": ["..."]}]}"""


class ThumbnailAgent(BaseAgent):
    agent_id = "thumbnail-agent"
    name = "Thumbnail Agent"
    version = 1
    capabilities = ["design_thumbnails", "score_thumbnails"]
    task_kind = "thumbnail"
    stage = "THUMBNAIL"
    quality_threshold = 88.0

    async def execute(self, ctx: AgentContext, *, topic: str, script_summary: str,
                      variants: int = 5) -> list[ThumbnailConcept]:
        dna = ctx.thumbnail_dna
        user = (
            f"TOPIC: {topic}\n\n"
            f"WHAT THE VIDEO ACTUALLY DOCUMENTS:\n{script_summary[:4000]}\n\n"
            f"THUMBNAIL DNA:\n{json.dumps(dna, ensure_ascii=False, indent=2)}\n\n"
            f"VISUAL STYLE:\n{json.dumps(ctx.visual_dna, ensure_ascii=False)[:2500]}\n\n"
            f"Design {variants} distinct concepts — vary the angle of attack, not just the "
            f"wording."
        )
        data = await self.llm_json(ctx, system=CONCEPT_SYSTEM, user=user, max_tokens=12000)

        badge = dna.get("badge", "")
        concepts: list[ThumbnailConcept] = []
        for i, c in enumerate(data.get("concepts", [])[:variants]):
            prompt = " ".join(str(c.get("image_prompt", "")).split())
            if badge and "badge" not in prompt.lower():
                prompt += f" {badge}"
            if "16:9" not in prompt:
                prompt += " 16:9 aspect ratio."
            concepts.append(ThumbnailConcept(
                concept_id=f"T{i + 1}",
                hindi_title=c.get("hindi_title", ""),
                characters=c.get("characters", ""),
                background=c.get("background", ""),
                colors=c.get("colors", ""),
                emotion_trigger=c.get("emotion_trigger", ""),
                image_prompt=prompt,
            ))

        await ctx.progress("THUMBNAIL", 30, f"rendering {len(concepts)} candidates")
        await self._render(ctx, concepts)

        rendered = [c for c in concepts if c.image_path]
        if not rendered:
            raise ProviderUnavailableError(
                "image", "no thumbnail candidate could be rendered",
                missing=provider_registry.missing_credentials("image"))

        await ctx.progress("THUMBNAIL", 70, "scoring candidates")
        await self._score(ctx, rendered, topic)

        rendered.sort(key=lambda c: c.score, reverse=True)
        winner = rendered[0]
        final_path = ctx.store.path("thumbnail/thumbnail_final.png")
        final_path.write_bytes(Path(winner.image_path).read_bytes())

        ctx.store.save_json("thumbnail/concepts.json", [c.model_dump() for c in concepts])
        await ctx.emit("THUMBNAIL_READY", {"winner": winner.concept_id,
                                           "score": winner.score,
                                           "title": winner.hindi_title})
        return rendered

    async def _render(self, ctx: AgentContext, concepts: list[ThumbnailConcept]) -> None:
        semaphore = asyncio.Semaphore(3)

        async def one(concept: ThumbnailConcept) -> None:
            async with semaphore:
                out = ctx.store.path(f"thumbnail/{concept.concept_id}.png")
                used = {"name": ""}

                async def op(provider):
                    return await provider.generate(prompt=concept.image_prompt,
                                                   aspect_ratio="16:9", out_path=out)
                try:
                    await provider_registry.call_with_fallback(
                        "image", op, attempts_per_provider=2,
                        on_provider_used=lambda n: used.update(name=n))
                    concept.image_path = str(out)
                    ctx.store.record_provenance(str(out), used["name"], "thumbnail generation")
                    await record_cost(ctx.project_id, self.agent_id, "image",
                                      provider=used["name"], units=1,
                                      cost_usd=IMAGE_PRICE_DEFAULT)
                except ProviderUnavailableError as e:
                    log.warning("thumbnail %s failed: %s", concept.concept_id, e)

        await asyncio.gather(*(one(c) for c in concepts))

    async def _score(self, ctx: AgentContext, concepts: list[ThumbnailConcept],
                     topic: str) -> None:
        listing = "\n".join(f"Image {i + 1}: headline is \"{c.hindi_title}\""
                            for i, c in enumerate(concepts))
        data = await self.vision_json(
            ctx, system=SCORE_SYSTEM,
            user=(f"Video topic: {topic}\n"
                  f"Channel formula: {json.dumps(ctx.thumbnail_dna, ensure_ascii=False)[:1500]}\n\n"
                  f"{listing}\n\nScore each candidate."),
            images=[Path(c.image_path) for c in concepts], max_tokens=6000)

        weights = {"readability": 0.22, "text_quality": 0.20, "emotional_clarity": 0.15,
                   "visual_hierarchy": 0.10, "contrast": 0.10, "brand_alignment": 0.10,
                   "curiosity": 0.13}
        for r in data.get("results", []):
            idx = int(r.get("index", 0)) - 1
            if not (0 <= idx < len(concepts)):
                continue
            weighted = sum(float(r.get(k, 0)) * w for k, w in weights.items())
            # Blend the analyst's holistic CTR read with the weighted rubric.
            concepts[idx].score = round(0.6 * weighted + 0.4 * float(r.get("ctr_potential", 0)), 1)


register(ThumbnailAgent())
