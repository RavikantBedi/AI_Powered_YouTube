import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("AGENCY_API_KEY", "test-key")
os.environ.setdefault("ARTIFACT_ROOT", tempfile.mkdtemp(prefix="agency-artifacts-"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CHANNEL_DNA_ROOT",
                      str(Path(__file__).resolve().parents[2] / "channel_dna"))


@pytest.fixture
def store():
    from app.core.artifacts import ArtifactStore
    return ArtifactStore("test-project")
