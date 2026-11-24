"""Channel DNA loader. DNA lives as JSON on disk (versionable, editable) and is
mirrored into the channels table on startup. The architecture supports any number of
channels; `anidoc` ships as the reference configuration."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import get_settings
from app.db.base import get_session_factory
from app.db.models import ChannelRecord

log = logging.getLogger("agency.dna")

DNA_FILES = {
    "channel": "channel_dna.json",
    "script": "script_dna.json",
    "visual": "visual_dna.json",
    "thumbnail": "thumbnail_dna.json",
}


def load_channel_dna(channel_id: str) -> dict:
    root = Path(get_settings().channel_dna_root) / channel_id
    dna: dict = {}
    for key, fname in DNA_FILES.items():
        p = root / fname
        if p.exists():
            dna[key] = json.loads(p.read_text(encoding="utf-8"))
        else:
            log.warning("DNA file missing for channel %s: %s", channel_id, fname)
            dna[key] = {}
    return dna


async def sync_channels_to_db() -> None:
    root = Path(get_settings().channel_dna_root)
    if not root.exists():
        log.warning("channel DNA root not found: %s", root)
        return
    async with get_session_factory()() as session:
        for child in root.iterdir():
            if not (child.is_dir() and (child / "channel_dna.json").exists()):
                continue
            dna = load_channel_dna(child.name)
            existing = await session.get(ChannelRecord, child.name)
            if existing:
                existing.dna = dna
                existing.name = dna["channel"].get("channel_name", child.name)
            else:
                session.add(ChannelRecord(
                    id=child.name,
                    name=dna["channel"].get("channel_name", child.name),
                    dna=dna,
                ))
        await session.commit()
