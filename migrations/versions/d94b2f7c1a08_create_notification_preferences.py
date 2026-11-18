"""Create the notification_preferences table.

Revision ID: d94b2f7c1a08
Revises: c3f8a1d64e21
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op


revision = "d94b2f7c1a08"
down_revision = "c3f8a1d64e21"
branch_labels = None
depends_on = None

CATEGORIES = "('applications', 'projects', 'messages', 'payments', 'reviews')"


def upgrade():
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint(f"category IN {CATEGORIES}", name="ck_notification_preferences_category"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "category"),
    )


def downgrade():
    op.drop_table("notification_preferences")
