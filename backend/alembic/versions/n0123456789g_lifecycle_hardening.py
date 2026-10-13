"""harden order lifecycle defaults and record shortage refunds"""
from alembic import op
import sqlalchemy as sa

revision = "n0123456789g"
down_revision = "m0123456789f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing deployments keep an explicitly chosen timeout; only the server
    # default is changed so a newly created store starts with automation off.
    op.alter_column("stores", "unpaid_order_timeout_minutes", server_default="0")
    op.alter_column("store_settings", "unpaid_order_timeout_minutes", server_default="0")
    op.add_column("stores", sa.Column("unpaid_order_reminder_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("store_settings", sa.Column("unpaid_order_reminder_minutes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("paid_confirmed_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("adjusted_total_minor", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("removed_minor", sa.BigInteger(), nullable=False, server_default="0"))
    # Preserve the historical confirmed receipt basis for later shortage
    # adjustments.  The latest approved review is the only source for ABA.
    op.execute("""
        UPDATE orders o
           SET paid_confirmed_minor = COALESCE(o.wallet_paid_minor, 0) + COALESCE((
                 SELECT o2.external_due_minor
                   FROM orders o2
                   JOIN payment_reviews pr ON pr.order_id = o2.id
                  WHERE o2.id = o.id AND pr.decision = 'APPROVED'
                  ORDER BY pr.created_at DESC LIMIT 1
               ), 0)
         WHERE o.payment_status IN ('PAID_CONFIRMED', 'PARTIALLY_REFUNDED', 'REFUNDED')
           AND COALESCE(o.wallet_paid_minor, 0) > 0
              OR o.payment_status IN ('PAID_CONFIRMED', 'PARTIALLY_REFUNDED', 'REFUNDED')
              AND EXISTS (SELECT 1 FROM payment_reviews pr2 WHERE pr2.order_id = o.id AND pr2.decision = 'APPROVED')
    """)
    op.create_table(
        "order_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["order_issues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["done_by"], ["staff.id"]),
    )
    op.create_index("ix_order_refunds_id", "order_refunds", ["id"], unique=False)
    op.create_index("order_refunds_order_idx", "order_refunds", ["order_id", "status"])


def downgrade() -> None:
    op.drop_index("order_refunds_order_idx", table_name="order_refunds")
    op.drop_index("ix_order_refunds_id", table_name="order_refunds")
    op.drop_table("order_refunds")
    op.drop_column("orders", "removed_minor")
    op.drop_column("orders", "adjusted_total_minor")
    op.drop_column("orders", "paid_confirmed_minor")
    op.drop_column("store_settings", "unpaid_order_reminder_minutes")
    op.drop_column("stores", "unpaid_order_reminder_minutes")
