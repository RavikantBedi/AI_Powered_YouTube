"""Subtitle Agent — SRT + VTT in the source language plus an English translation.

Timing comes from the measured per-scene audio durations, so subtitles are already in
sync with the render. Long scenes are split into multiple cues on clause boundaries so
no cue exceeds a readable line budget or dwell time.
"""
from __future__ import annotations

import logging
import re

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Storyboard

log = logging.getLogger("agency.subtitles")

MAX_CUE_CHARS = 84
MAX_CUE_SECONDS = 6.0
MIN_CUE_SECONDS = 0.9


def _timestamp(seconds: float, vtt: bool = False) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _split_cue_text(text: str, max_chars: int = MAX_CUE_CHARS) -> list[str]:
    """Split on clause boundaries, then on width, preserving word integrity."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    for clause in re.split(r"(?<=[,;।:])\s+", text):
        if not clause.strip():
            continue
        if len(clause) <= max_chars:
            chunks.append(clause.strip())
            continue
        words, line = clause.split(), ""
        for word in words:
            if len(line) + len(word) + 1 > max_chars and line:
                chunks.append(line.strip())
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            chunks.append(line.strip())
    # Merge runt chunks back into their neighbour.
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) + len(chunk) + 1 <= max_chars:
            merged[-1] = f"{merged[-1]} {chunk}"
        else:
            merged.append(chunk)
    return merged or [text]


def build_cues(storyboard: Storyboard) -> list[dict]:
    cues: list[dict] = []
    for scene in storyboard.scenes:
        if not (scene.narration_text and scene.audio_path):
            continue
        pieces = _split_cue_text(scene.narration_text)
        total_chars = sum(len(p) for p in pieces) or 1
        clock = scene.start_seconds
        for piece in pieces:
            share = len(piece) / total_chars
            dur = max(MIN_CUE_SECONDS,
                      min(scene.duration_seconds * share, MAX_CUE_SECONDS))
            cues.append({"start": clock, "end": clock + dur, "text": piece,
                         "scene_id": scene.scene_id})
            clock += dur
        # Snap the last cue to the scene boundary so drift cannot accumulate.
        if cues and cues[-1]["scene_id"] == scene.scene_id:
            cues[-1]["end"] = max(cues[-1]["start"] + MIN_CUE_SECONDS,
                                  scene.start_seconds + scene.duration_seconds)
    return cues


def render_srt(cues: list[dict]) -> str:
    out = []
    for i, cue in enumerate(cues, 1):
        out.append(f"{i}\n{_timestamp(cue['start'])} --> {_timestamp(cue['end'])}\n"
                   f"{cue['text']}\n")
    return "\n".join(out)


def render_vtt(cues: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for cue in cues:
        out.append(f"{_timestamp(cue['start'], vtt=True)} --> "
                   f"{_timestamp(cue['end'], vtt=True)}\n{cue['text']}\n")
    return "\n".join(out)


TRANSLATE_SYSTEM = """You translate documentary subtitles into natural English.

Rules:
- Translate meaning, not word order. The result must read like professional broadcast
  subtitles.
- Keep proper nouns, place names, ranks, and organisation names in their standard English
  forms.
- One output line per input line, in the same order. Never merge or split lines, never
  add or drop a line — the line count must match exactly.
- Never add commentary.

Return ONLY JSON: {"lines": ["...", "..."]}"""


class SubtitleAgent(BaseAgent):
    agent_id = "subtitle-agent"
    name = "Subtitle Agent"
    version = 1
    capabilities = ["generate_subtitles", "translate_subtitles"]
    task_kind = "formatting"
    stage = "SUBTITLES"

    BATCH = 60

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      language: str, translate_to: str = "en") -> dict:
        cues = build_cues(storyboard)
        if not cues:
            log.warning("no cues could be built — no narrated scenes")
            return {}

        lang_code = language.split("-")[0].lower()
        paths = {
            f"{lang_code}_srt": ctx.store.save_text(f"subtitles/{lang_code}.srt", render_srt(cues)),
            f"{lang_code}_vtt": ctx.store.save_text(f"subtitles/{lang_code}.vtt", render_vtt(cues)),
        }

        if translate_to and translate_to != lang_code:
            await ctx.progress("SUBTITLES", 50, f"translating to {translate_to}")
            translated = await self._translate(ctx, cues, language, translate_to)
            if translated:
                t_cues = [{**c, "text": t} for c, t in zip(cues, translated)]
                paths[f"{translate_to}_srt"] = ctx.store.save_text(
                    f"subtitles/{translate_to}.srt", render_srt(t_cues))
                paths[f"{translate_to}_vtt"] = ctx.store.save_text(
                    f"subtitles/{translate_to}.vtt", render_vtt(t_cues))

        ctx.store.save_json("subtitles/cues.json", cues)
        await ctx.emit("SUBTITLES_READY", {"cues": len(cues), "files": list(paths)})
        return paths

    async def _translate(self, ctx: AgentContext, cues: list[dict], source: str,
                         target: str) -> list[str] | None:
        out: list[str] = []
        for i in range(0, len(cues), self.BATCH):
            batch = [c["text"] for c in cues[i:i + self.BATCH]]
            numbered = "\n".join(f"{j + 1}. {t}" for j, t in enumerate(batch))
            data = await self.llm_json(
                ctx, system=TRANSLATE_SYSTEM,
                user=f"Source language: {source}. Target: {target}.\n"
                     f"Translate these {len(batch)} subtitle lines.\n\n{numbered}",
                max_tokens=12000, effort="low", task="formatting")
            lines = data.get("lines", [])
            if len(lines) != len(batch):
                # Line-count mismatch would desync every later cue — fail the translation
                # rather than shipping misaligned subtitles.
                log.warning("translation returned %d lines for %d cues — skipping %s subtitles",
                            len(lines), len(batch), target)
                return None
            out.extend(lines)
        return out


register(SubtitleAgent())
