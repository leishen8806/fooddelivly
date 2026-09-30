"""persist payment handoff selection and order locale

Revision ID: 2b6c7d8e9f10
Revises: 1a2b3c4d5e6f
Create Date: 2026-09-30 08:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2b6c7d8e9f10"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("pending_payment_order_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("customer_language", sa.String(), server_default="en", nullable=False))
    op.add_column("store_settings", sa.Column("staff_group_language", sa.String(), server_default="en", nullable=False))


def downgrade() -> None:
    op.drop_column("store_settings", "staff_group_language")
    op.drop_column("orders", "customer_language")
    op.drop_column("customers", "pending_payment_order_id")
