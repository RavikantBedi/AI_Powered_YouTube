"""Filesystem artifact store. Layout mirrors the production stages:

/artifacts/{project_id}/
    requirements.json  plan.json
    research/  script/  storyboard/  visuals/  audio/  subtitles/
    thumbnail/  final/  metadata/  qa/  provenance/
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings
from app.schemas.core import AssetProvenance

SUBDIRS = ["research", "script", "storyboard", "visuals", "audio", "subtitles",
           "thumbnail", "final", "metadata", "qa", "provenance"]

T = TypeVar("T", bound=BaseModel)


class ArtifactStore:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.root = get_settings().artifact_root_path / project_id
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"artifact path escapes project root: {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ── JSON documents ─────────────────────────────────────────────
    def save_json(self, rel: str, data: BaseModel | dict | list) -> str:
        payload = data.model_dump() if isinstance(data, BaseModel) else data
        p = self.path(rel)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)

    def load_json(self, rel: str) -> dict | list | None:
        p = self.path(rel)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def load_model(self, rel: str, model: type[T]) -> T | None:
        data = self.load_json(rel)
        return model.model_validate(data) if data is not None else None

    # ── Text / binary ──────────────────────────────────────────────
    def save_text(self, rel: str, text: str) -> str:
        p = self.path(rel)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def load_text(self, rel: str) -> str | None:
        p = self.path(rel)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def save_bytes(self, rel: str, data: bytes) -> str:
        p = self.path(rel)
        p.write_bytes(data)
        return str(p)

    def exists(self, rel: str) -> bool:
        return self.path(rel).exists()

    # ── Provenance ledger ──────────────────────────────────────────
    def record_provenance(self, asset_path: str, provider: str, method: str,
                          source: str = "generated", license_: str = "generated-content") -> None:
        ledger_rel = "provenance/assets.json"
        ledger: list = self.load_json(ledger_rel) or []
        ledger.append(AssetProvenance(
            asset_id=Path(asset_path).name,
            path=asset_path,
            provider=provider,
            source=source,
            license=license_,
            generation_method=method,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ).model_dump())
        self.save_json(ledger_rel, ledger)

    def list_files(self) -> list[dict]:
        out = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                out.append({"path": str(p.relative_to(self.root)), "bytes": p.stat().st_size})
        return out
