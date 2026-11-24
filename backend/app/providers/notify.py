"""Notification adapters. All optional; the dashboard always shows completion, these
push it outward. Every configured channel is attempted; failures are logged, never fatal."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import get_settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import NotificationProvider

log = logging.getLogger("agency.notify")


class TelegramNotifier(NotificationProvider):
    name = "telegram"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.telegram_bot_token and s.telegram_chat_id):
            raise ProviderUnavailableError("telegram", "not configured",
                                           missing=["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
        self.token = s.telegram_bot_token
        self.chat_id = s.telegram_chat_id

    async def send(self, *, title: str, body: str, url: str = "") -> bool:
        text = f"*{title}*\n{body}" + (f"\n{url}" if url else "")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown",
                      "disable_web_page_preview": False},
            )
        return r.status_code < 400


class EmailNotifier(NotificationProvider):
    name = "email"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.smtp_host and s.notify_email_to):
            raise ProviderUnavailableError("email", "SMTP not configured",
                                           missing=["SMTP_HOST", "NOTIFY_EMAIL_TO"])
        self.s = s

    async def send(self, *, title: str, body: str, url: str = "") -> bool:
        return await asyncio.to_thread(self._send_sync, title, body, url)

    def _send_sync(self, title: str, body: str, url: str) -> bool:
        s = self.s
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = s.smtp_from or s.smtp_user
        msg["To"] = s.notify_email_to
        msg.set_content(body + (f"\n\n{url}" if url else ""))
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
                server.starttls()
                if s.smtp_user:
                    server.login(s.smtp_user, s.smtp_password)
                server.send_message(msg)
            return True
        except Exception:
            log.exception("email notification failed")
            return False


class WebhookNotifier(NotificationProvider):
    """Generic JSON webhook — works with Discord, Slack, and custom endpoints."""
    name = "webhook"

    def __init__(self) -> None:
        s = get_settings()
        if not s.notify_webhook_url:
            raise ProviderUnavailableError("webhook", "not configured",
                                           missing=["NOTIFY_WEBHOOK_URL"])
        self.url = s.notify_webhook_url

    async def send(self, *, title: str, body: str, url: str = "") -> bool:
        text = f"**{title}**\n{body}" + (f"\n{url}" if url else "")
        payload = {"content": text, "text": text, "title": title, "body": body, "url": url}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(self.url, json=payload)
        return r.status_code < 400
