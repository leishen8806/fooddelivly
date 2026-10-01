"""pending recharge order pointer

Revision ID: 8f9012345678
Revises: 7e8f90123456
Create Date: 2026-10-01 17:20:00.000000

客户在 /wallet 里点「充值 $10」后，机器人要记住「接下来这张截图属于哪张充值单」。
与已有的 pending_payment_order_id（订单收款截图）同构。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f9012345678'
down_revision = '7e8f90123456'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('pending_recharge_order_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('customers', 'pending_recharge_order_id')
