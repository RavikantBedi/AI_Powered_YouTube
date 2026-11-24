"""Music selection from a local, rights-cleared library.

Deliberately not a generative music call: publishing requires known rights status, so
tracks come from a directory the operator has stocked with licensed/CC0 audio. Each
subdirectory is a mood bucket; a `LICENSES.json` at the library root records provenance
and is copied into the project's asset ledger.

Empty library -> returns None. The Editor renders narration-only rather than risking
unknown rights (spec §30).
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from app.config import get_settings
from app.providers.base import MusicProvider

log = logging.getLogger("agency.music")

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
MOOD_ALIASES = {
    "grave": ["grave", "tense", "dark", "somber"],
    "tense": ["tense", "suspense", "grave"],
    "hopeful": ["hopeful", "uplifting", "resolution"],
    "neutral": ["neutral", "ambient", "bed"],
}


class LocalMusicLibrary(MusicProvider):
    name = "local-library"

    def __init__(self) -> None:
        self.root = Path(get_settings().music_library_dir)

    def licenses(self) -> dict:
        lic = self.root / "LICENSES.json"
        if lic.exists():
            try:
                return json.loads(lic.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("music LICENSES.json is not valid JSON")
        return {}

    async def select(self, *, mood: str, duration_seconds: float) -> Path | None:
        if not self.root.exists():
            log.info("music library %s absent — rendering narration-only", self.root)
            return None
        buckets = MOOD_ALIASES.get(mood.lower(), [mood.lower(), "neutral"])
        for bucket in buckets:
            d = self.root / bucket
            if d.is_dir():
                tracks = [p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT]
                if tracks:
                    return random.choice(tracks)
        flat = [p for p in self.root.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXT]
        return random.choice(flat) if flat else None

    def license_for(self, track: Path) -> dict:
        entry = self.licenses().get(track.name)
        if entry:
            return entry
        return {"license": "UNKNOWN", "source": str(track), "creator": "unknown"}
