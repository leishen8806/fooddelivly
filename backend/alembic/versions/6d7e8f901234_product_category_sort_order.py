"""add stable product ordering within menu categories

Revision ID: 6d7e8f901234
Revises: 5b6c7d8e9f01
Create Date: 2026-09-30 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "6d7e8f901234"
down_revision = "5b6c7d8e9f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("products", "sort_order")
