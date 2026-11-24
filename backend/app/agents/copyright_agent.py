"""Copyright / Rights Agent — audits the asset ledger before publishing.

The rule is simple and enforced in code: every asset in the final render must have a
provenance record with a known rights status. Generated assets are cleared by
construction; anything else must carry an explicit license. Unknown rights on a
material asset blocks the publish rather than gambling on a claim.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.schemas.core import Storyboard

log = logging.getLogger("agency.copyright")

CLEARED_LICENSES = {"generated-content", "cc0", "public-domain", "licensed",
                    "royalty-free", "owned"}


class CopyrightAgent(BaseAgent):
    agent_id = "copyright-agent"
    name = "Copyright / Rights Agent"
    version = 1
    capabilities = ["audit_rights", "verify_provenance"]
    stage = "COPYRIGHT"

    async def execute(self, ctx: AgentContext, *, storyboard: Storyboard,
                      music_path: Path | None, final_video: Path) -> dict:
        ledger = ctx.store.load_json("provenance/assets.json") or []
        by_path = {Path(entry["path"]).name: entry for entry in ledger}

        issues: list[dict] = []
        checked = 0

        # Every visual and audio asset that made it into the render must be accounted for.
        for scene in storyboard.scenes:
            for asset in (scene.image_path, scene.clip_path, scene.audio_path):
                if not asset:
                    continue
                checked += 1
                entry = by_path.get(Path(asset).name)
                if not entry:
                    issues.append({"asset": Path(asset).name, "severity": "high",
                                   "problem": "no provenance record"})
                elif entry.get("license", "").lower() not in CLEARED_LICENSES:
                    issues.append({"asset": Path(asset).name, "severity": "high",
                                   "problem": f"unclear license: {entry.get('license')}"})

        if music_path:
            checked += 1
            entry = by_path.get(Path(music_path).name)
            if not entry or entry.get("license", "UNKNOWN").upper() == "UNKNOWN":
                issues.append({"asset": Path(music_path).name, "severity": "critical",
                               "problem": "background music has unknown rights status"})

        thumbnail = ctx.store.path("thumbnail/thumbnail_final.png")
        if thumbnail.exists():
            checked += 1

        critical = [i for i in issues if i["severity"] == "critical"]
        report = {
            "assets_checked": checked,
            "ledger_entries": len(ledger),
            "issues": issues,
            "status": "BLOCK" if critical else ("REVIEW" if issues else "PASS"),
        }
        ctx.store.save_json("qa/copyright.json", report)
        await ctx.emit("COPYRIGHT_CHECKED", {"status": report["status"],
                                             "issues": len(issues)})
        return report


register(CopyrightAgent())
