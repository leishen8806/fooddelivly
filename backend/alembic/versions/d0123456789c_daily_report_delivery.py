"""daily report deliveries

Revision ID: d0123456789c
Revises: c0123456789b
Create Date: 2026-10-02 09:10:00.000000

每日报表的**投递记录**，作用只有一个：保证「一天一份」不会重复发。

调度器（进程内定时）和外部 cron 都走同一张表：
先 INSERT 抢占当天这条记录（(report_key, chat_id) 唯一），抢不到就说明已经发过，
直接跳过。发送失败则删掉这条记录，下一分钟重试。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd0123456789c'
down_revision = 'c0123456789b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'report_deliveries',
        sa.Column('id', sa.Integer(), nullable=False),
        # 'daily_sales:2026-10-01' —— 含报表类型与日期，方便以后加周报/月报
        sa.Column('report_key', sa.String(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('message_id', sa.String(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_key', 'chat_id', name='report_deliveries_uniq'),
    )
    op.create_index('report_deliveries_date_idx', 'report_deliveries', ['report_date'])


def downgrade() -> None:
    op.drop_index('report_deliveries_date_idx', table_name='report_deliveries')
    op.drop_table('report_deliveries')
