"""add shortage escalation and manager refund approval controls"""
from alembic import op
import sqlalchemy as sa

revision = "o0123456789h"
down_revision = "n0123456789g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stores ADD COLUMN IF NOT EXISTS shortage_customer_response_timeout_minutes INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS shortage_customer_response_timeout_minutes INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE stores ADD COLUMN IF NOT EXISTS shortage_refund_review_minor BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE store_settings ADD COLUMN IF NOT EXISTS shortage_refund_review_minor BIGINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE order_refunds ADD COLUMN IF NOT EXISTS approved_by INTEGER")
    op.execute("""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'order_refunds_approved_by_fkey') THEN
            ALTER TABLE order_refunds ADD CONSTRAINT order_refunds_approved_by_fkey
              FOREIGN KEY (approved_by) REFERENCES staff(id);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE order_refunds DROP CONSTRAINT IF EXISTS order_refunds_approved_by_fkey")
    op.execute("ALTER TABLE order_refunds DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE order_refunds DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE order_refunds DROP COLUMN IF EXISTS requires_approval")
    op.execute("ALTER TABLE store_settings DROP COLUMN IF EXISTS shortage_refund_review_minor")
    op.execute("ALTER TABLE stores DROP COLUMN IF EXISTS shortage_refund_review_minor")
    op.execute("ALTER TABLE store_settings DROP COLUMN IF EXISTS shortage_customer_response_timeout_minutes")
    op.execute("ALTER TABLE stores DROP COLUMN IF EXISTS shortage_customer_response_timeout_minutes")
