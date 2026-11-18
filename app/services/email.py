"""Transactional email with an outbox.

An action such as "you were hired" must not depend on an SMTP server being
reachable at that instant. The route therefore only *queues* an email: a row is
written to ``email_outbox`` inside the same transaction as the business change.
Delivery then happens through a backend, and a failed attempt stays on the row
so ``flask send-emails`` can retry it later.

Backends:

- ``console`` (default in development) prints the message to the application log.
- ``memory`` captures messages in ``app.extensions["sent_emails"]`` for tests.
- ``smtp`` sends through a real mail server using the ``MAIL_*`` settings.

Switching to a provider such as Mailgun or SES means adding one backend class
here; no route or template changes.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage

from flask import current_app, render_template

from app.extensions import db
from app.models import EmailOutbox, User
from app.services import notifications

MAX_ATTEMPTS = 5

# event -> (notification category, subject template, body template)
EVENTS = {
    "application_received": ("applications", "New application for {job_title}", "email/application_received.txt"),
    "hired": ("applications", "You were hired for {job_title}", "email/hired.txt"),
    "work_approved": ("projects", "Work approved for {job_title}", "email/work_approved.txt"),
    "project_cancelled": ("projects", "Project cancelled: {job_title}", "email/project_cancelled.txt"),
}


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailBackend:
    name = "base"

    def send(self, message: OutgoingEmail) -> None:
        raise NotImplementedError


class ConsoleBackend(EmailBackend):
    """Writes the email to the application log instead of sending it."""

    name = "console"

    def send(self, message: OutgoingEmail) -> None:
        current_app.logger.info(
            "\n--- SkillLink email ---\nTo: %s\nSubject: %s\n\n%s\n--- end email ---",
            message.to, message.subject, message.body,
        )


class MemoryBackend(EmailBackend):
    """Keeps sent messages in memory so tests can assert on them."""

    name = "memory"

    def send(self, message: OutgoingEmail) -> None:
        current_app.extensions.setdefault("sent_emails", []).append(message)


class SmtpBackend(EmailBackend):
    name = "smtp"

    def __init__(self, config):
        self.server = config.get("MAIL_SERVER")
        self.port = int(config.get("MAIL_PORT", 587))
        self.username = config.get("MAIL_USERNAME")
        self.password = config.get("MAIL_PASSWORD")
        self.use_tls = bool(config.get("MAIL_USE_TLS", True))
        self.sender = config.get("MAIL_DEFAULT_SENDER")
        self.timeout = int(config.get("MAIL_TIMEOUT", 10))
        if not self.server or not self.sender:
            raise RuntimeError("SMTP mail requires MAIL_SERVER and MAIL_DEFAULT_SENDER.")

    def send(self, message: OutgoingEmail) -> None:
        mime = MimeMessage()
        mime["From"] = self.sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(self.server, self.port, timeout=self.timeout) as client:
            if self.use_tls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(mime)


BACKENDS = {backend.name: backend for backend in (ConsoleBackend, MemoryBackend)}


def backend() -> EmailBackend:
    name = current_app.config.get("MAIL_BACKEND", "console")
    if name == SmtpBackend.name:
        return SmtpBackend(current_app.config)
    backend_class = BACKENDS.get(name)
    if backend_class is None:
        raise RuntimeError(f"Unknown MAIL_BACKEND: {name!r}")
    return backend_class()


def queue(user: User | None, event: str, **context) -> EmailOutbox | None:
    """Render and store an email. Returns ``None`` when it is not wanted.

    The body is rendered now, not at delivery time, so a retry cannot fail
    because a template or a related row changed in the meantime.
    """
    if user is None or not getattr(user, "email", None):
        return None
    try:
        category, subject_template, body_template = EVENTS[event]
    except KeyError:
        raise ValueError(f"Unknown email event: {event!r}") from None
    if not notifications.is_enabled(user.id, category, channel="email"):
        return None
    row = EmailOutbox(
        user_id=user.id,
        recipient=user.email,
        subject=subject_template.format(**context)[:200],
        body=render_template(body_template, **context),
        status="pending",
    )
    db.session.add(row)
    return row


def flush(limit: int = 20, include_failed: bool = False) -> tuple[int, int]:
    """Attempt delivery for queued email. Returns (sent, failed).

    Called after a state-changing request commits, and by the CLI for retries.
    A failure is recorded on the row; it is not raised to the caller, because a
    mail outage must never fail the business action that triggered the email.
    """
    statuses = ("pending", "failed") if include_failed else ("pending",)
    rows = EmailOutbox.query.filter(EmailOutbox.status.in_(statuses)).order_by(EmailOutbox.id).limit(limit).all()
    if not rows:
        return 0, 0
    sender = backend()
    sent = failed = 0
    for row in rows:
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.last_error = f"Gave up after {row.attempts} attempts."
            failed += 1
            continue
        try:
            sender.send(OutgoingEmail(to=row.recipient, subject=row.subject, body=row.body))
        except Exception as error:  # noqa: BLE001 - any backend failure is recorded, not raised
            row.attempts += 1
            row.status = "failed"
            row.last_error = str(error)[:500]
            failed += 1
        else:
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            row.last_error = None
            sent += 1
    db.session.commit()
    return sent, failed
