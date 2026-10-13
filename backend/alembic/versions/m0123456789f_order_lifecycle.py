"""customer cancellation, coupon release, unpaid timeout and shortage issues"""
from alembic import op
import sqlalchemy as sa


revision = "m0123456789f"
down_revision = "l0123456789e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("unpaid_order_timeout_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("store_settings", sa.Column("unpaid_order_timeout_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "order_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=True),
        sa.Column("issue_type", sa.String(), server_default="OUT_OF_STOCK", nullable=False),
        sa.Column("status", sa.String(), server_default="OPEN", nullable=False),
        sa.Column("replacement_product_id", sa.Integer(), nullable=True),
        sa.Column("replacement_name_snapshot", sa.String(), nullable=True),
        sa.Column("replacement_unit_price_minor", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("resolved_by_type", sa.String(), nullable=True),
        sa.Column("resolved_by_id", sa.Integer(), nullable=True),
        sa.Column("resolution_note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["replacement_product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_issues_id", "order_issues", ["id"], unique=False)
    op.create_index("order_issues_order_status_idx", "order_issues", ["order_id", "status"])


def downgrade() -> None:
    op.drop_index("order_issues_order_status_idx", table_name="order_issues")
    op.drop_index("ix_order_issues_id", table_name="order_issues")
    op.drop_table("order_issues")
    op.drop_column("store_settings", "unpaid_order_timeout_minutes")
    op.drop_column("stores", "unpaid_order_timeout_minutes")
