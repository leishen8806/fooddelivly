# 每日经营报表

每天 **早上 8:00**（店铺时区，默认 `Asia/Phnom_Penh`）把**前一天**的经营数据
推送到员工群。

报表区间是**店铺时区的自然日**：`昨天 00:00:00.000` ~ `23:59:59.999`
（左闭右开，`[start, end)`），换算成 UTC 再查库——与 `管理端 → 财务` 的
统计口径一致，两处数字应当对得上。

---

## 1. 报表内容

| 指标 | 口径 |
|---|---|
| 订单数 / 订单合计 | 当天创建的订单，按状态拆分 |
| 确认收款 | **银行转账**（`payment_reviews` 中 APPROVED）+ **钱包余额支付**（`wallet.order_payments` 净额 = 支付 − 已退） |
| 已退款 | 当天订单里已经退掉的金额 |
| 充值到账 | 当天 `reviewed_at` 落在区间内的已入账充值单：笔数 / 本金 / 赠送金 |
| 待审核充值单 | 当天创建、仍未完成的充值单 |
| 待审核凭证 | 当天创建、`PROOF_SUBMITTED` 还压着的订单 |
| 新增客户 | 当天新建的客户 |

> 钱包余额支付的订单**没有** `PaymentReview` 记录（下单即扣款），所以
> 只按 `PaymentReview` 汇总会漏掉这部分收入——报表与财务接口都已把
> `payment_method = 'WALLET'` 的订单按订单号关联 `wallet.order_payments` 计入，并扣掉退款。

## 2. 怎么发

### 进程内定时（默认，开箱即用）

`backend/daily_report.py` 里一个每分钟醒一次的协程：到达 `DAILY_REPORT_HOUR`
（默认 8）就发前一天的报表。开关与时间：

```bash
DAILY_REPORT_ENABLED=true
DAILY_REPORT_HOUR=8          # 店铺时区的整点
```

### 外部 cron（可选）

不想用进程内定时，也可以让系统 cron 调接口——带 `X-Cron-Secret` 即可，
不需要管理员会话：

```bash
# 每天早上 8:05（服务器时区）
5 8 * * * curl -fsS -X POST https://<host>/api/v1/admin/reports/daily/send \
  -H "X-Cron-Secret: $CRON_SECRET" -H "Content-Type: application/json" -d '{}'
```

`CRON_SECRET` 不配置时这条路径**直接关闭**（不会因为没配就放行）。

## 3. 为什么不会重复发

`report_deliveries` 表在 `(report_key, chat_id)` 上有唯一约束，
`report_key = daily_sales:2026-10-01`。发送顺序是：

1. **先抢占**这条记录（插入失败 = 已经发过 → 返回 `skipped`）；
2. 再调 Telegram 发送；
3. **发送失败就删掉占位记录**，下一分钟自动补发（宁可迟到，不能漏发）；
4. 发送成功回填 `message_id` 与 `sent_at`。

所以进程内定时、外部 cron、后台手动点「发送」三条路径互相之间也是互斥的，
多副本部署同样只会送达一次。

## 4. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/admin/reports/daily?date=YYYY-MM-DD` | 任何在职员工 | 预览（默认今天），返回结构化数据 + 渲染好的文案 + 投递状态 |
| POST | `/api/v1/admin/reports/daily/send` | MANAGER 或 `X-Cron-Secret` | 发送（默认昨天）；`force=true` 才允许重发 |

管理端**财务**区块里有对应的「每日报表」卡片：可换日期预览、看是否已发送、
MANAGER 可以手动补发。

## 5. 验证

```bash
cd backend
# 统计口径 + 投递幂等 + 调度到点判断（17 项，用桩替掉 Telegram 发送层）
DATABASE_URL=postgresql+asyncpg://... python tests/daily_report_test.py

# 权限矩阵与接口契约在安全套件里
python tests/wallet_security_test.py http://127.0.0.1:8000
```
