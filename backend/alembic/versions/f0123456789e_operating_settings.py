"""operating settings: business hours, pause, min order, delivery/service fee

Revision ID: f0123456789e
Revises: e0123456789d
Create Date: 2026-10-02 09:50:00.000000

把「营业时间」从一段没人校验的自由文本（store_settings.open_hours）变成
真正生效的结构化配置，并补齐下单时要用的经营参数：

  store_settings.is_accepting_orders  暂停接单开关
  store_settings.business_hours       营业时间（多段 JSON，支持跨午夜）
  store_settings.min_order_minor      最低起送（按商品小计判断）
  store_settings.delivery_fee_minor   配送费
  store_settings.service_fee_minor    服务费

订单上记录金额构成，方便对账与分店结算：

  orders.subtotal_minor        商品小计
  orders.delivery_fee_minor    当单配送费
  orders.service_fee_minor     当单服务费

默认值全部为「不限制 / 不加价」，所以对现有数据和测试零影响。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f0123456789e'
down_revision = 'e0123456789d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('store_settings',
                  sa.Column('is_accepting_orders', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('store_settings',
                  sa.Column('business_hours', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('store_settings',
                  sa.Column('min_order_minor', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('store_settings',
                  sa.Column('delivery_fee_minor', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('store_settings',
                  sa.Column('service_fee_minor', sa.Integer(), nullable=False, server_default='0'))

    op.add_column('orders', sa.Column('subtotal_minor', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('delivery_fee_minor', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('orders', sa.Column('service_fee_minor', sa.Integer(), nullable=False, server_default='0'))
    # 历史订单：小计 = 总额（当时没有费用项）
    op.execute("UPDATE orders SET subtotal_minor = total_minor WHERE subtotal_minor IS NULL")


def downgrade() -> None:
    op.drop_column('orders', 'service_fee_minor')
    op.drop_column('orders', 'delivery_fee_minor')
    op.drop_column('orders', 'subtotal_minor')
    op.drop_column('store_settings', 'service_fee_minor')
    op.drop_column('store_settings', 'delivery_fee_minor')
    op.drop_column('store_settings', 'min_order_minor')
    op.drop_column('store_settings', 'business_hours')
    op.drop_column('store_settings', 'is_accepting_orders')
