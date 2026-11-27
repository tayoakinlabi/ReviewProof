"""Send alerts to the QA lead by email or webhook (step 2.5).

An alert holds queue-level findings only. It names no reviewer, item or planted error. Nothing
is sent anywhere unless a route is set in the settings and sending is asked for: ReviewProof has
no telemetry.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage

from reviewproof.canonical import format_timestamp
from reviewproof.config import AlertRoute, Smtp
from reviewproof.metrics.checks import Check

TIMEOUT_SECONDS = 15


class DeliveryError(Exception):
    """An alert could not be delivered by at least one route."""


@dataclass(frozen=True)
class Message:
    queue: str
    subject: str
    body: str
    #: The webhook's JSON body.
    payload: dict


def compose(queue: str, breaches: Sequence[Check], generated_at) -> Message:
    """One message for everything outside its limit in one queue."""
    count = len(breaches)
    subject = (
        f"ReviewProof: {count} item{'s' if count != 1 else ''} need{'' if count != 1 else 's'} "
        f"attention in queue {queue}"
    )
    lines = [f"- {check.title}: {check.detail}" for check in breaches]
    body = "\n".join(
        [
            f"Queue: {queue}",
            "",
            *lines,
            "",
            "See the full report for the evidence. This is not a finding about any individual.",
            "Send this only to the people responsible for QA of this queue.",
        ]
    )
    payload = {
        "tool": "reviewproof",
        "queue": queue,
        "generated_at": format_timestamp(generated_at),
        "alerts": [{"rule": c.rule, "title": c.title, "detail": c.detail} for c in breaches],
    }
    return Message(queue, _one_line(subject), body, payload)


def send(
    message: Message,
    route: AlertRoute,
    smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Deliver by every configured route. Returns what was delivered.

    Every route is tried even if an earlier one fails, then ``DeliveryError`` names the
    failures.
    """
    if not route.emails and not route.webhook:
        raise DeliveryError("no alert route is set: add alerts.qa_lead to the settings")
    environment = os.environ if environment is None else environment
    delivered, failures = [], []
    if route.emails:
        try:
            _send_email(message, route.emails, route.smtp, smtp_factory, environment)
            delivered.append(f"email to {len(route.emails)} address(es)")
        except (OSError, smtplib.SMTPException, DeliveryError) as error:
            failures.append(f"email: {error}")
    if route.webhook:
        try:
            _send_webhook(message, route.webhook)
            delivered.append("webhook")
        except (OSError, ValueError, DeliveryError) as error:
            # The address is not repeated: webhook addresses often contain a secret.
            failures.append(f"webhook: {type(error).__name__}: {_reason(error)}")
    if failures:
        raise DeliveryError("; ".join(failures))
    return delivered


def _reason(error: Exception) -> str:
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error)


def _send_email(
    message: Message,
    recipients: Sequence[str],
    settings: Smtp,
    smtp_factory: Callable[..., smtplib.SMTP],
    environment: Mapping[str, str],
) -> None:
    credentials = None
    if settings.username_env:
        try:
            credentials = (
                environment[settings.username_env],
                environment[settings.password_env],
            )
        except KeyError as error:
            raise DeliveryError(f"environment variable {error} is not set") from None
    email = EmailMessage()
    email["Subject"] = message.subject
    email["From"] = settings.sender
    email["To"] = ", ".join(recipients)
    email.set_content(message.body)
    with smtp_factory(settings.host, settings.port, timeout=TIMEOUT_SECONDS) as server:
        if settings.starttls:
            server.starttls(context=ssl.create_default_context())
        if credentials:
            server.login(*credentials)
        server.send_message(email)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """An alert goes where it was configured to go, and nowhere else."""

    def redirect_request(self, *args, **kwargs):
        return None


def _send_webhook(message: Message, address: str) -> None:
    request = urllib.request.Request(  # noqa: S310 - the settings allow only https or localhost
        address,
        data=json.dumps(message.payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "reviewproof"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        error.close()  # the error holds the open reply
        status = error.code
    if not 200 <= status < 300:
        raise DeliveryError(f"HTTP {status}")


def _one_line(text: str) -> str:
    return " ".join(text.split())
