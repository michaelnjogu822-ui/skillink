"""Add the email outbox and per-category email switches.

Revision ID: e5a7c9b1f302
Revises: d94b2f7c1a08
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op


revision = "e5a7c9b1f302"
down_revision = "d94b2f7c1a08"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.add_column(sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))

    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_email_outbox_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_outbox_status", "email_outbox", ["status"], unique=False)


def downgrade():
    op.drop_index("ix_email_outbox_status", table_name="email_outbox")
    op.drop_table("email_outbox")
    with op.batch_alter_table("notification_preferences") as batch_op:
        batch_op.drop_column("email_enabled")
