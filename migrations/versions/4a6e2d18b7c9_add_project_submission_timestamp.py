"""Add the project work submission timestamp.

Revision ID: 4a6e2d18b7c9
Revises: 9c37d431c2e1
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa


revision = "4a6e2d18b7c9"
down_revision = "9c37d431c2e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("projects", "submitted_at")
