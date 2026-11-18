"""In-app notifications: one formatter, one writer, one preference check.

Every notification used to be built inline at its call site, which meant the
wording lived in ten places and no user could switch a category off. Routes now
call :func:`notify` with a kind and the facts, and this module decides the text
and whether the recipient wants it.

Categories map kinds to the switches a user can change on
``/settings/notifications``:

===============  ==========================================
category         kinds
===============  ==========================================
applications     job_application, hired
projects         project_submitted, project_completed, project_cancelled
messages         message
payments         escrow_funded, payment_released, payment_refunded
reviews          review_received
===============  ==========================================
"""
from __future__ import annotations

from app.extensions import db
from app.models import Notification, NotificationPreference

CATEGORIES = NotificationPreference.CATEGORIES

CATEGORY_LABELS = {
    "applications": "Applications and hiring",
    "projects": "Project progress",
    "messages": "Messages",
    "payments": "Payments and escrow",
    "reviews": "Reviews",
}

# kind -> category
KIND_CATEGORY = {
    "job_application": "applications",
    "hired": "applications",
    "project_submitted": "projects",
    "project_completed": "projects",
    "project_cancelled": "projects",
    "message": "messages",
    "escrow_funded": "payments",
    "payment_released": "payments",
    "payment_refunded": "payments",
    "review_received": "reviews",
}

# kind -> message template. Only documented placeholders are used by callers.
FORMATS = {
    "job_application": "{actor} applied for {title}.",
    "hired": "You were hired for {title}.",
    "project_submitted": "Work was submitted for {title}.",
    "project_completed": "{title} was marked complete.",
    "project_cancelled": "{title} was cancelled by the client.",
    "message": "{actor} sent a message about {title}.",
    "escrow_funded": "{currency} {amount} is now held in escrow for {title}.",
    "payment_released": "{currency} {amount} was released to you for {title}.",
    "payment_refunded": "{currency} {amount} was refunded for {title}.",
    "review_received": "{actor} left you a {rating}-star review.",
}


def category_for(kind: str) -> str:
    try:
        return KIND_CATEGORY[kind]
    except KeyError:
        raise ValueError(f"Unknown notification kind: {kind!r}") from None


def format_message(kind: str, **details) -> str:
    try:
        template = FORMATS[kind]
    except KeyError:
        raise ValueError(f"Unknown notification kind: {kind!r}") from None
    return template.format(**details)


def is_enabled(user_id: int, category: str) -> bool:
    """A missing row means enabled, so new accounts need no setup."""
    preference = db.session.get(NotificationPreference, (user_id, category))
    return True if preference is None else preference.enabled


def notify(user_id: int, kind: str, *, target_url: str | None = None, **details) -> Notification | None:
    """Create an in-app notification unless the recipient muted the category.

    Returns the notification, or ``None`` when the preference is off. Callers
    must not treat ``None`` as an error: the action itself still happened.
    """
    category = category_for(kind)
    if not is_enabled(user_id, category):
        return None
    notification = Notification(
        user_id=user_id,
        kind=kind,
        message=format_message(kind, **details),
        target_url=target_url,
    )
    db.session.add(notification)
    return notification


def set_preference(user_id: int, category: str, enabled: bool) -> NotificationPreference:
    """Create or update one switch."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown notification category: {category!r}")
    preference = db.session.get(NotificationPreference, (user_id, category))
    if preference is None:
        preference = NotificationPreference(user_id=user_id, category=category, enabled=enabled)
        db.session.add(preference)
    else:
        preference.enabled = enabled
    return preference


def preferences_for(user_id: int) -> dict[str, bool]:
    """Every category with its effective value, ready for the settings form."""
    rows = {row.category: row.enabled for row in NotificationPreference.query.filter_by(user_id=user_id).all()}
    return {category: rows.get(category, True) for category in CATEGORIES}
