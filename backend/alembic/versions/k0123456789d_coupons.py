"""coupon campaigns, private/public claim links, and order snapshots

Revision ID: k0123456789d
Revises: j0123456789c
Create Date: 2026-10-12 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "k0123456789d"
down_revision = "j0123456789c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coupon_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), server_default="USD", nullable=False),
        sa.Column("coupon_type", sa.String(), nullable=False),
        sa.Column("distribution_mode", sa.String(), nullable=False),
        sa.Column("min_spend_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=True),
        sa.Column("discount_cap_minor", sa.Integer(), nullable=True),
        sa.Column("fixed_discount_minor", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("per_user_claim_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("total_claim_limit", sa.Integer(), nullable=True),
        sa.Column("total_claimed_quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(), server_default="DRAFT", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coupon_campaigns_id", "coupon_campaigns", ["id"], unique=False)
    op.create_index("coupon_campaigns_store_status_idx", "coupon_campaigns", ["store_id", "status", "starts_at", "ends_at"])

    op.create_table(
        "coupon_claim_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coupon_id", sa.Integer(), nullable=False),
        sa.Column("token_value", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="ACTIVE", nullable=False),
        sa.Column("claimed_by", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupon_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claimed_by"], ["customers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_value"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_coupon_claim_links_id", "coupon_claim_links", ["id"], unique=False)
    op.create_index("ix_coupon_claim_links_token_hash", "coupon_claim_links", ["token_hash"], unique=True)
    op.create_index("coupon_claim_links_coupon_status_idx", "coupon_claim_links", ["coupon_id", "status"])

    op.create_table(
        "customer_coupons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coupon_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("claim_link_id", sa.Integer(), nullable=True),
        sa.Column("claim_request_key", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="AVAILABLE", nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_order_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupon_campaigns.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["claim_link_id"], ["coupon_claim_links.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["used_order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "claim_request_key", name="uq_customer_coupon_claim_request"),
    )
    op.create_index("ix_customer_coupons_id", "customer_coupons", ["id"], unique=False)
    op.create_index("customer_coupons_customer_status_idx", "customer_coupons", ["customer_id", "status"])
    op.create_index("customer_coupons_coupon_idx", "customer_coupons", ["coupon_id"])

    op.add_column("orders", sa.Column("coupon_id", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("coupon_name_snapshot", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("coupon_type_snapshot", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("coupon_discount_minor", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key("orders_coupon_id_fkey", "orders", "customer_coupons", ["coupon_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("orders_coupon_id_fkey", "orders", type_="foreignkey")
    op.drop_column("orders", "coupon_discount_minor")
    op.drop_column("orders", "coupon_type_snapshot")
    op.drop_column("orders", "coupon_name_snapshot")
    op.drop_column("orders", "coupon_id")
    op.drop_index("customer_coupons_coupon_idx", table_name="customer_coupons")
    op.drop_index("customer_coupons_customer_status_idx", table_name="customer_coupons")
    op.drop_index("ix_customer_coupons_id", table_name="customer_coupons")
    op.drop_table("customer_coupons")
    op.drop_index("coupon_claim_links_coupon_status_idx", table_name="coupon_claim_links")
    op.drop_index("ix_coupon_claim_links_token_hash", table_name="coupon_claim_links")
    op.drop_index("ix_coupon_claim_links_id", table_name="coupon_claim_links")
    op.drop_table("coupon_claim_links")
    op.drop_index("coupon_campaigns_store_status_idx", table_name="coupon_campaigns")
    op.drop_index("ix_coupon_campaigns_id", table_name="coupon_campaigns")
    op.drop_table("coupon_campaigns")
