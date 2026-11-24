"""Tests for the infrastructure that keeps the pipeline honest: provider fallback,
circuit breaking, artifact isolation, and the definition-of-done gate."""
from __future__ import annotations

import asyncio

import pytest

from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry
from app.schemas.core import QAReport, RequirementSpec

# ── provider fallback / self-healing ────────────────────────────────────

class _Provider:
    def __init__(self, name: str, fail_times: int = 0):
        self.name = name
        self.fail_times = fail_times
        self.calls = 0

    async def work(self) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderUnavailableError(self.name, "transient failure")
        return self.name


@pytest.fixture(autouse=True)
def _clean_registry():
    provider_registry.reset()
    yield
    provider_registry.reset()


@pytest.fixture
def no_backoff(monkeypatch):
    """Skip the retry backoff so tests run instantly — capture the real sleep first,
    otherwise the replacement calls itself."""
    real_sleep = asyncio.sleep

    async def instant(_delay):
        await real_sleep(0)

    monkeypatch.setattr(provider_registry.asyncio, "sleep", instant)


async def _call(providers, **kwargs):
    async def op(p):
        return await p.work()
    return await provider_registry.call_with_fallback("image", op, **kwargs)


@pytest.mark.asyncio
async def test_falls_through_to_second_provider(monkeypatch):
    dead, alive = _Provider("dead", fail_times=99), _Provider("alive")
    monkeypatch.setattr(provider_registry, "available_providers",
                        lambda cap: [dead, alive])
    result = await _call(None, attempts_per_provider=1)
    assert result == "alive"
    assert dead.calls == 1


@pytest.mark.asyncio
async def test_retries_before_moving_on(monkeypatch, no_backoff):
    """A provider that recovers on retry must not be abandoned."""
    flaky = _Provider("flaky", fail_times=1)
    monkeypatch.setattr(provider_registry, "available_providers", lambda cap: [flaky])
    assert await _call(None, attempts_per_provider=3) == "flaky"
    assert flaky.calls == 2


@pytest.mark.asyncio
async def test_raises_with_missing_credentials_when_chain_dead(monkeypatch):
    monkeypatch.setattr(provider_registry, "available_providers", lambda cap: [])
    monkeypatch.setattr(provider_registry, "missing_credentials",
                        lambda cap: ["GEMINI_API_KEY", "OPENAI_API_KEY"])
    with pytest.raises(ProviderUnavailableError) as exc:
        await _call(None)
    # The error must name exactly what would unblock the user.
    assert "GEMINI_API_KEY" in exc.value.missing


@pytest.mark.asyncio
async def test_never_returns_a_fake_result(monkeypatch, no_backoff):
    """The contract that matters most: a dead chain raises, it does not fabricate."""
    monkeypatch.setattr(provider_registry, "available_providers",
                        lambda cap: [_Provider("dead", fail_times=99)])
    with pytest.raises(ProviderUnavailableError):
        await _call(None, attempts_per_provider=2)


def test_circuit_breaker_opens_then_recovers():
    breaker = provider_registry._Breaker()
    for _ in range(provider_registry.CIRCUIT_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.open, "breaker should open after repeated failures"
    breaker.record_success()
    assert not breaker.open, "a success must close the breaker"


def test_capability_report_shape():
    report = provider_registry.capability_report()
    for capability in ("image", "tts", "video", "youtube", "notify"):
        assert capability in report
        assert set(report[capability]) == {"available", "missing_credentials", "ready"}


# ── artifact store ──────────────────────────────────────────────────────

def test_artifact_store_round_trips_models(store):
    spec = RequirementSpec(topic="Test topic", duration_seconds=600)
    store.save_json("requirements.json", spec)
    loaded = store.load_model("requirements.json", RequirementSpec)
    assert loaded.topic == "Test topic"
    assert loaded.duration_seconds == 600


def test_artifact_store_returns_none_for_missing(store):
    assert store.load_model("nope/missing.json", RequirementSpec) is None
    assert store.load_json("nope/missing.json") is None


def test_artifact_store_blocks_path_traversal(store):
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")


def test_provenance_ledger_accumulates(store):
    store.save_json("provenance/assets.json", [])
    store.record_provenance("/tmp/a.png", "gemini", "text-to-image")
    store.record_provenance("/tmp/b.png", "openai", "text-to-image")
    ledger = store.load_json("provenance/assets.json")
    assert len(ledger) == 2
    assert {e["provider"] for e in ledger} == {"gemini", "openai"}
    assert all(e["timestamp"] for e in ledger)


# ── definition of done ──────────────────────────────────────────────────

class _FakeProject:
    def __init__(self, completed, publish=None, requirement=None):
        self.completed_stages = completed
        self.publish_result = publish or {}
        self.requirement = requirement or {"publication_goal": "publish"}


def test_definition_of_done_requires_everything():
    from app.agents.chief_producer import ChiefProducerAgent
    from app.core.orchestrator import STAGES

    qa = QAReport(overall_score=95, policy="PASS")
    partial = ChiefProducerAgent.definition_of_done(_FakeProject(["REQUIREMENTS"]), qa)
    assert not partial["done"]
    assert "video_rendered" in partial["missing"]

    complete = ChiefProducerAgent.definition_of_done(
        _FakeProject(list(STAGES), publish={"video_id": "abc", "video_url": "https://y/abc"}), qa)
    assert complete["done"], complete["missing"]


def test_definition_of_done_fails_without_qa():
    from app.agents.chief_producer import ChiefProducerAgent
    from app.core.orchestrator import STAGES

    result = ChiefProducerAgent.definition_of_done(
        _FakeProject(list(STAGES), publish={"video_id": "a", "video_url": "u"}), None)
    assert not result["done"]
    assert "final_qa_passed" in result["missing"]


def test_definition_of_done_skips_upload_for_draft():
    from app.agents.chief_producer import ChiefProducerAgent
    from app.core.orchestrator import STAGES

    qa = QAReport(overall_score=95, policy="PASS")
    result = ChiefProducerAgent.definition_of_done(
        _FakeProject(list(STAGES), requirement={"publication_goal": "draft"}), qa)
    assert result["done"], result["missing"]


# ── channel DNA ─────────────────────────────────────────────────────────

def test_anidoc_dna_loads_completely():
    from app.core.dna import load_channel_dna

    dna = load_channel_dna("anidoc")
    assert dna["channel"]["language"] == "hi-IN"
    assert dna["script"]["word_count_target"]["min"] == 3000
    assert "16:9" in dna["visual"]["style_suffix"]
    assert dna["thumbnail"]["variants_per_video"] == 5
    # The serving-official policy must survive into the runtime config.
    assert "symbolic" in dna["channel"]["real_figures_policy"]["currently_serving_heads_of_state"]
