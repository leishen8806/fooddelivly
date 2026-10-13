"""backfill audit_logs.store_id for rows written after the multi-store migration

Revision ID: l0123456789e
Revises: k0123456789d
Create Date: 2026-10-13 10:00:00.000000

多店迁移之后写入的审计日志大多没有带 store_id（NULL）。门店经理的审计查询已改成
严格按 store_id 过滤，NULL 只对总部可见，所以这里按被操作对象推出归属门店：
订单 -> orders.store_id，员工 -> staff.store_id，充值单 -> wallet.recharge_orders.store_id，
钱包调账 -> customers.store_id。商品/分类等目录操作保持 NULL（总部级）。
"""
from alembic import op


revision = "l0123456789e"
down_revision = "k0123456789d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE audit_logs a SET store_id = o.store_id
          FROM orders o
         WHERE a.store_id IS NULL AND a.entity_type = 'order'
           AND a.entity_id ~ '^[0-9]+$' AND o.id = a.entity_id::int
    """)
    op.execute("""
        UPDATE audit_logs a SET store_id = s.store_id
          FROM staff s
         WHERE a.store_id IS NULL AND a.entity_type = 'staff'
           AND a.entity_id ~ '^[0-9]+$' AND s.id = a.entity_id::int
    """)
    op.execute("""
        UPDATE audit_logs a SET store_id = r.store_id
          FROM wallet.recharge_orders r
         WHERE a.store_id IS NULL AND a.entity_type = 'wallet_recharge'
           AND a.entity_id ~ '^[0-9]+$' AND r.id = a.entity_id::bigint
    """)
    op.execute("""
        UPDATE audit_logs a SET store_id = c.store_id
          FROM customers c
         WHERE a.store_id IS NULL AND a.entity_type = 'wallet'
           AND a.entity_id ~ '^[0-9]+$' AND c.id = a.entity_id::int
    """)


def downgrade() -> None:
    # 回填是纯补数据，不可也不需要还原
    pass
