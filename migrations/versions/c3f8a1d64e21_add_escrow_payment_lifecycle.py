"""Add the escrow payment lifecycle to transactions.

Revision ID: c3f8a1d64e21
Revises: 4a6e2d18b7c9
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op


revision = "c3f8a1d64e21"
down_revision = "4a6e2d18b7c9"
branch_labels = None
depends_on = None

OLD_STATUSES = "status IN ('pending', 'succeeded', 'failed', 'refunded')"
NEW_STATUSES = "status IN ('pending', 'held', 'released', 'succeeded', 'failed', 'refunded')"


def upgrade():
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="manual"))
        batch_op.add_column(sa.Column("settlement_reference", sa.String(length=160), nullable=True))
        batch_op.drop_constraint("ck_transactions_status", type_="check")
        batch_op.create_check_constraint("ck_transactions_status", NEW_STATUSES)
        batch_op.create_check_constraint("ck_transactions_kind", "kind IN ('manual', 'escrow')")
        batch_op.create_index(batch_op.f("ix_transactions_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_transactions_settlement_reference"), ["settlement_reference"], unique=True)


def downgrade():
    # The previous schema has no escrow concept, so rows using the new
    # statuses must be folded into states it understands before the narrower
    # CHECK constraint is restored: money already released stays recorded as a
    # successful payment, and money still held is recorded as a failed
    # transaction rather than pretending it was paid.
    op.execute("UPDATE transactions SET status = 'succeeded' WHERE status = 'released'")
    op.execute("UPDATE transactions SET status = 'failed' WHERE status = 'held'")
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index(batch_op.f("ix_transactions_settlement_reference"))
        batch_op.drop_index(batch_op.f("ix_transactions_kind"))
        batch_op.drop_constraint("ck_transactions_kind", type_="check")
        batch_op.drop_constraint("ck_transactions_status", type_="check")
        batch_op.create_check_constraint("ck_transactions_status", OLD_STATUSES)
        batch_op.drop_column("settlement_reference")
        batch_op.drop_column("kind")
