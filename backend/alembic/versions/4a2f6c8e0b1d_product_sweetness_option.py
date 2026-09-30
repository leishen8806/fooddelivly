"""add product sweetness selector setting

Revision ID: 4a2f6c8e0b1d
Revises: 3c7d8e9f012a
Create Date: 2026-09-30 13:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "4a2f6c8e0b1d"
down_revision = "3c7d8e9f012a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("sweetness_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("products", "sweetness_enabled")
