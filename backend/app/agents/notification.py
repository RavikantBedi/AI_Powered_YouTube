"""Notification Agent — tells the user the job is done (or why it stopped).

Fans out to every configured channel. A channel that is not configured is skipped
silently; a channel that fails is logged but never fails the project, because the work
is already finished by the time this runs.
"""
from __future__ import annotations

import logging

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import register
from app.core.errors import ProviderUnavailableError
from app.providers import registry as provider_registry

log = logging.getLogger("agency.notify")


class NotificationAgent(BaseAgent):
    agent_id = "notification-agent"
    name = "Notification Agent"
    version = 1
    capabilities = ["notify_user"]
    stage = "NOTIFY"
    max_attempts = 1

    async def execute(self, ctx: AgentContext, *, title: str, body: str,
                      url: str = "") -> dict:
        delivered: list[str] = []
        failed: list[str] = []
        for provider in provider_registry.available_providers("notify"):
            try:
                ok = await provider.send(title=title, body=body, url=url)
                (delivered if ok else failed).append(provider.name)
            except (ProviderUnavailableError, Exception) as e:
                log.warning("notification via %s failed: %s", provider.name, e)
                failed.append(provider.name)

        result = {"delivered": delivered, "failed": failed}
        await ctx.emit("NOTIFICATION_SENT", result)
        return result

    @staticmethod
    def success_body(*, video_title: str, duration_seconds: float, quality: float,
                     production_seconds: float, cost_usd: float,
                     privacy: str) -> str:
        mins, secs = divmod(int(duration_seconds), 60)
        prod_m, prod_s = divmod(int(production_seconds), 60)
        return (
            f"{video_title}\n\n"
            f"Duration: {mins}m {secs}s\n"
            f"Quality score: {quality:.0f}/100\n"
            f"Privacy: {privacy}\n"
            f"Production time: {prod_m}m {prod_s}s\n"
            f"Estimated cost: ${cost_usd:.2f}"
        )


register(NotificationAgent())
