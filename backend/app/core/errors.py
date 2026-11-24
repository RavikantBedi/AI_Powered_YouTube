"""Typed failure taxonomy. The orchestrator branches on these — never on strings."""


class AgencyError(Exception):
    """Base class for all pipeline errors."""


class ProviderUnavailableError(AgencyError):
    """A provider cannot run (missing credentials, outage, unsupported op).

    Never fake output instead of raising this. The router will try fallbacks;
    if none exist the project is BLOCKED with `missing` surfaced to the user.
    """

    def __init__(self, provider: str, reason: str, missing: list[str] | None = None):
        self.provider = provider
        self.reason = reason
        self.missing = missing or []
        super().__init__(f"{provider}: {reason}" + (f" (set: {', '.join(self.missing)})" if self.missing else ""))


class QualityGateFailure(AgencyError):
    """An artifact scored below its stage gate after all revision loops."""

    def __init__(self, stage: str, score: float, threshold: float, report: dict | None = None):
        self.stage = stage
        self.score = score
        self.threshold = threshold
        self.report = report or {}
        super().__init__(f"Quality gate '{stage}' failed: {score:.1f} < {threshold}")


class PolicyBlockedError(AgencyError):
    """Content cannot be safely auto-fixed; publishing must not proceed."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("Policy check blocked publishing: " + "; ".join(reasons))


class ProjectBlockedError(AgencyError):
    """The project cannot continue autonomously. `dependency` names exactly what is missing."""

    def __init__(self, dependency: str, detail: str = ""):
        self.dependency = dependency
        self.detail = detail
        super().__init__(f"BLOCKED on: {dependency}. {detail}".strip())


class CapabilityGapError(AgencyError):
    """No registered agent covers a required capability — routes to the Agent Factory."""

    def __init__(self, capability: str, context: str = ""):
        self.capability = capability
        self.context = context
        super().__init__(f"Missing capability: {capability}")
