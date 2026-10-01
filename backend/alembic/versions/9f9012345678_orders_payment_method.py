"""order payment method

Revision ID: 9f9012345678
Revises: 8f9012345678
Create Date: 2026-10-01 17:35:00.000000

订单要能区分「人工转账 + 截图审核」和「钱包余额直接支付」：
  * MANUAL —— 默认，原有流程（ABA 转账 → 上传截图 → 员工确认）
  * WALLET —— 下单时用钱包余额抵扣，下单即 PAID_CONFIRMED

退款也要据此分流：钱包支付的订单取消时把钱退回钱包（wallet.refund_payment），
人工转账的订单仍然走线下退款。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f9012345678'
down_revision = '8f9012345678'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'orders',
        sa.Column('payment_method', sa.String(), nullable=False, server_default='MANUAL'),
    )


def downgrade() -> None:
    op.drop_column('orders', 'payment_method')
