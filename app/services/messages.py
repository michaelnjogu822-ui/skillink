"""Project conversations: unread counts and read markers.

Kept in one place because three callers need the same definition of "unread":
the navigation badge, the message inbox, and the polling endpoint.
"""
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.models import Message, Project


def unread_count(user_id: int) -> int:
    """Unread messages from the other participant across all of the user's projects."""
    return db.session.query(func.count(Message.id)).join(
        Project, Project.id == Message.project_id
    ).filter(
        (Project.client_id == user_id) | (Project.freelancer_id == user_id),
        Message.sender_id != user_id,
        Message.read_at.is_(None),
    ).scalar() or 0


def mark_project_read(project_id: int, user_id: int) -> int:
    """Mark every unread message from the counterpart as read. Returns the count."""
    return Message.query.filter(
        Message.project_id == project_id,
        Message.sender_id != user_id,
        Message.read_at.is_(None),
    ).update({"read_at": datetime.now(timezone.utc)}, synchronize_session=False)
