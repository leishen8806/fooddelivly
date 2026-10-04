"""allow products to belong to multiple menu categories

Revision ID: j0123456789c
Revises: i0123456789b
Create Date: 2026-10-04 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "j0123456789c"
down_revision = "i0123456789b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_categories",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id", "category_id"),
    )
    op.create_index("product_categories_category_idx", "product_categories", ["category_id", "sort_order"])
    op.create_index("product_categories_product_idx", "product_categories", ["product_id", "sort_order"])
    # Existing products keep their current category as the initial association.
    op.execute(
        """
        INSERT INTO product_categories (product_id, category_id, sort_order)
        SELECT id, category_id, 0
        FROM products
        WHERE category_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("product_categories_product_idx", table_name="product_categories")
    op.drop_index("product_categories_category_idx", table_name="product_categories")
    op.drop_table("product_categories")
