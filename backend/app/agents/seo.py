"""YouTube SEO Agent — generates and scores metadata candidates.

Titles are generated as multiple candidates and scored; the winner must be both
compelling and accurate. A title that overstates what the video documents is rejected
outright, regardless of predicted click-through.
"""
from __future__ import annotations

import logging

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Storyboard, YouTubeMetadata

log = logging.getLogger("agency.seo")

SYSTEM = """You are the YouTube SEO Agent for a documentary channel.

Produce a complete metadata package. Every element must be accurate to what the video
actually documents — a title or description that promises more than the video delivers
is a policy violation and a subscriber-losing move, not a win.

Deliver:
- 5 title candidates in distinct formats: curiosity gap, revelation, how/why,
  language-mix viral hook, short punchy. Each must follow the channel title formula and
  word limit, and each must be literally true.
- description: 150-200 words in the video's language, documentary register, front-loading
  the searchable subject in the first two lines, ending with a subscribe CTA in the
  channel's voice.
- 15 hashtags and 30 tags mixing the video's language and English, ordered most to least
  relevant.
- chapters: timestamps derived from the supplied section boundaries, starting at 00:00.
- filename: a lowercase, hyphenated, ASCII-safe filename.
- playlist: the playlist this belongs in.

For each title also give: accuracy (0-100, is it strictly supported by the content?),
ctr_potential (0-100), searchability (0-100), clarity (0-100).

Return ONLY JSON:
{"titles": [{"text": "...", "format": "curiosity_gap", "accuracy": 0, "ctr_potential": 0,
  "searchability": 0, "clarity": 0}],
 "description": "...", "hashtags": ["#..."], "tags": ["..."],
 "chapters": [{"start": "00:00", "title": "..."}],
 "filename": "...", "playlist": "...", "keywords": ["..."]}"""


class SEOAgent(BaseAgent):
    agent_id = "seo-agent"
    name = "YouTube SEO Agent"
    version = 1
    capabilities = ["generate_metadata", "score_titles"]
    task_kind = "seo"
    stage = "SEO"
    quality_threshold = 85.0

    async def execute(self, ctx: AgentContext, *, topic: str, script_summary: str,
                      storyboard: Storyboard, language: str,
                      thumbnail_title: str = "") -> YouTubeMetadata:
        chapters_hint = self._chapter_hints(storyboard)
        user = (
            f"TOPIC: {topic}\n"
            f"LANGUAGE: {language}\n"
            f"VIDEO DURATION: {int(storyboard.total_duration // 60)}m "
            f"{int(storyboard.total_duration % 60)}s\n\n"
            f"WHAT THE VIDEO DOCUMENTS:\n{script_summary[:5000]}\n\n"
            f"CHANNEL SEO STRATEGY: {ctx.channel_dna.get('seo_strategy', '')}\n"
            f"TITLE FORMULA: {ctx.thumbnail_dna.get('title_formula', '')}\n"
            f"CTA STYLE: {ctx.channel_dna.get('cta_style', '')}\n"
            + (f"CHOSEN THUMBNAIL HEADLINE (title should complement, not duplicate it): "
               f"{thumbnail_title}\n" if thumbnail_title else "")
            + f"\nSECTION TIMESTAMPS AVAILABLE FOR CHAPTERS:\n{chapters_hint}\n\n"
            f"Produce the metadata package."
        )
        data = await self.llm_json(ctx, system=SYSTEM, user=user, max_tokens=12000)

        candidates = data.get("titles", [])
        scored = []
        for t in candidates:
            accuracy = float(t.get("accuracy", 0))
            # Accuracy is a gate, not a weight: an inaccurate title cannot win.
            if accuracy < 85:
                log.info("dropping title for low accuracy (%.0f): %s", accuracy, t.get("text"))
                continue
            score = (0.40 * float(t.get("ctr_potential", 0))
                     + 0.30 * float(t.get("searchability", 0))
                     + 0.20 * float(t.get("clarity", 0))
                     + 0.10 * accuracy)
            scored.append((score, t))
        scored.sort(key=lambda x: x[0], reverse=True)

        if scored:
            best_title = scored[0][1].get("text", "")
        else:
            log.warning("no title passed the accuracy gate; falling back to the topic")
            best_title = (candidates[0].get("text") if candidates else topic)[:100]

        meta = YouTubeMetadata(
            title=best_title[:100],
            title_options=[t.get("text", "") for _, t in scored] or
                          [t.get("text", "") for t in candidates],
            description=data.get("description", ""),
            hashtags=[h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", [])][:15],
            tags=data.get("tags", [])[:30],
            chapters=data.get("chapters", []),
            language=language.split("-")[0],
            playlist=data.get("playlist", ""),
            filename=data.get("filename", "") or self._fallback_filename(topic),
        )
        # Hashtags belong in the description body for YouTube to index them.
        if meta.hashtags and "#" not in meta.description:
            meta.description = f"{meta.description}\n\n{' '.join(meta.hashtags)}"

        ctx.store.save_json("metadata/youtube.json", meta)
        await ctx.emit("SEO_READY", {"title": meta.title, "tags": len(meta.tags),
                                     "candidates": len(candidates)})
        return meta

    def _chapter_hints(self, storyboard: Storyboard) -> str:
        """Offer roughly one chapter marker per 90 seconds of runtime."""
        hints, next_mark = [], 0.0
        for scene in storyboard.scenes:
            if scene.start_seconds >= next_mark:
                m, s = divmod(int(scene.start_seconds), 60)
                hints.append(f"{m:02d}:{s:02d} — {scene.narration_text[:110]}")
                next_mark = scene.start_seconds + 90
        return "\n".join(hints[:14])

    def _fallback_filename(self, topic: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in " -" else "" for ch in topic.lower())
        return "-".join(safe.split())[:60] + ".mp4"


register(SEOAgent())
