"""record de-duplicated Mini App activity by store-local day

Historical rows are backfilled from order creation dates, so the historical
portion uses the "customer placed an order" definition.  New rows written by
the Telegram login path use the "customer opened the Mini App" definition.
The two definitions are intentionally documented separately and are not
treated as interchangeable. 历史回填使用“下单口径”，上线后的登录采集使用“打开口径”。
"""
from alembic import op
import sqlalchemy as sa


revision = "p0123456789i"
down_revision = "o0123456789h"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_daily_activity",
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("customer_id", "activity_date"),
    )
    op.create_index(
        "ix_customer_daily_activity_date_store",
        "customer_daily_activity",
        ["activity_date", "store_id"],
        unique=False,
    )

    # Historical backfill: an order is the only reliable pre-release signal.
    # Use the order's store timezone; for legacy NULL store_id rows, fall back
    # to the customer's store, then MAIN/first store, then Phnom Penh.
    op.execute(
        """
        WITH fallback_store AS (
            SELECT
                COALESCE(
                    (SELECT id FROM stores WHERE code = 'MAIN' ORDER BY id LIMIT 1),
                    (SELECT id FROM stores ORDER BY id LIMIT 1)
                ) AS id,
                COALESCE(
                    (SELECT timezone FROM stores WHERE code = 'MAIN' ORDER BY id LIMIT 1),
                    (SELECT timezone FROM stores ORDER BY id LIMIT 1),
                    'Asia/Phnom_Penh'
                ) AS timezone
        ), order_activity AS (
            SELECT
                o.customer_id,
                COALESCE(o.store_id, c.store_id, fs.id) AS store_id,
                (
                    o.created_at AT TIME ZONE COALESCE(s.timezone, cs.timezone, fs.timezone)
                )::date AS activity_date,
                MIN(o.created_at) AS first_seen_at,
                MAX(o.created_at) AS last_seen_at
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            LEFT JOIN stores s ON s.id = o.store_id
            LEFT JOIN stores cs ON cs.id = c.store_id
            CROSS JOIN fallback_store fs
            WHERE o.customer_id IS NOT NULL AND o.created_at IS NOT NULL
            GROUP BY
                o.customer_id,
                COALESCE(o.store_id, c.store_id, fs.id),
                (
                    o.created_at AT TIME ZONE COALESCE(s.timezone, cs.timezone, fs.timezone)
                )::date
        )
        INSERT INTO customer_daily_activity
            (customer_id, activity_date, store_id, first_seen_at, last_seen_at)
        SELECT customer_id, activity_date, store_id, first_seen_at, last_seen_at
        FROM order_activity
        """
    )


def downgrade() -> None:
    op.drop_index("ix_customer_daily_activity_date_store", table_name="customer_daily_activity")
    op.drop_table("customer_daily_activity")
