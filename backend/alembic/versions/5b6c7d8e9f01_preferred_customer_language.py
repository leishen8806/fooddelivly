"""persist the customer's selected Mini App language

Revision ID: 5b6c7d8e9f01
Revises: 4a2f6c8e0b1d
Create Date: 2026-09-30 13:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = "5b6c7d8e9f01"
down_revision = "4a2f6c8e0b1d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("preferred_language", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "preferred_language")
