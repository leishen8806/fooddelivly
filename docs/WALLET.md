# 钱包 / 充值（Wallet & Recharge）

给 Tea+Cafe 订单系统加的钱包模块：**人工转账 → 上传截图 → 员工确认 → 到账**，
余额可以**在下单时直接抵扣**。数据库 PostgreSQL，金额沿用项目既有约定：
**整数最小货币单位**（USD → cents），与 `orders.total_minor` / `products.price_minor` 同单位。

- 数据库对象全部在独立的 **`wallet` schema**（表 + 函数 + 触发器 + 视图）
- 由 alembic 迁移 `7e8f90123456` / `8f9012345678` / `9f9012345678` 建立
- 业务代码**只允许**调用数据库函数，禁止 `UPDATE wallet.wallets`
- 三个界面都已接好：Telegram 机器人、客户 Mini App 钱包页、管理端充值审核页

---

## 1. 资金模型

### 1.1 唯一事实来源是账本

```
wallet.ledger_entries    ← append-only 账本（触发器禁止 UPDATE / DELETE）
        │  物化
        ▼
wallet.wallets           ← principal / bonus / frozen 余额列
```

任何余额变动都必须经过 `wallet._apply()`，它是整个系统里**唯一**改余额的地方。
好处是账本永远可以重算验证：`wallet.reconcile()` 会比对
「各 bucket 的流水累加」与「wallets 表的余额列」，不一致就返回差异行。

### 1.2 双桶 + 赠送批次

- `principal`：**本金**，充值到账的实付部分，不退不消。
- `bonus`：**赠送**，按**批次（lot）**管理 — 每笔赠送一个 `wallet.bonus_lots` 行，
  各自带到期时间；扣款时**先扣快到期的**，到期时只清对应批次的剩余额。

为什么必须按批次：如果只用一个全局「赠送到期时间」字段，那么
「永久赠送 + 30 天赠送」并存时，后一笔赠送会把永久余额也改成会过期，
等于偷走用户资产；而且做不到「先扣快到期的」。

`wallets.bonus` 是各批次剩余额的物化和；`wallets.bonus_expire_at` 是最近一个到期日。

### 1.3 关键不变量

| 不变量 | 由什么保证 |
|---|---|
| 账本不可篡改 | 触发器 `trg_ledger_immutable`（UPDATE/DELETE 直接报 `LEDGER_IMMUTABLE`） |
| 余额不为负 | `wallets` 的 CHECK 约束 + 函数内的余额判断 |
| 不重复入账 | 每个资金函数都有 `idempotency_key` 唯一约束，重放返回既有结果 |
| 不重复扣款 | `spend_balance` 以业务订单号 `biz_id` 为锚点 |
| 并发安全 | 所有状态流转都 `SELECT ... FOR UPDATE`，并且**取到行锁之后再查一次幂等** |
| 同一张截图不能刷两次 | `wallet.recharge_proofs.tg_file_unique_id` 唯一索引 |
| 权限不可绕过 | `wallet.is_reviewer()` / `wallet.can_adjust()` 在函数内部再校验一次 |

---

## 2. 权限口径

| 动作 | 谁能做 | 函数 |
|---|---|---|
| 确认充值到账 / 驳回 | **任意在职员工**（`staff.active`） | `approve_recharge` / `reject_recharge` |
| 改实收金额 | 同上 | `set_received_amount` |
| 手动调账 | **仅 `role='MANAGER'`** | `adjust_balance`（`can_adjust` 校验） |
| 取消未付款的充值单 | 客户本人 | `cancel_recharge` |

「审核到账」放宽到任意在职员工，是为了与**订单收款审核**保持同一口径
（现在就是群里任何一个员工点「确认收款」）。审核会凭空造出可消费余额，
所以「不能审核自己的单」是硬约束：

```sql
-- 注意：staff.id 与 customers.id 是两套独立的 id 空间，
-- 必须靠 telegram_user_id 对齐才能判断「这个人是不是本单客户本人」。
wallet.is_order_owner_staff(p_staff_id, p_customer_id)
```

> ⚠️ 这是移植时踩过的坑：直接写 `p_staff_id = v_order.customer_id` 会**误判**
> （把不相干的员工当成本人拒掉）也会**漏判**（真正的本人反而能过）。

---

## 3. 客户流程

```
/wallet  ──▶ 钱包卡片（本金/赠送/到期）+ 充值档位按钮
   │
   ├─ 点档位 wamount_<金额>  ──▶ start_recharge()  建单（幂等键 = chat:message:档位）
   │        └─ 转账指引：订单号 / 金额 / 赠送 / 备注必须填订单号
   │
   ├─ 发截图 ──▶ submit_proof()  status: awaiting_proof → under_review
   │        └─ 推送员工群（带图 + 「确认到账 / 驳回」按钮）
   │
   └─ wcancel_<id> ──▶ cancel_recharge()  仅 awaiting_proof 可自行取消
```

配套的 Mini App / 网页入口（已实现）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/wallet` | 余额 + 档位 + 限额 |
| GET | `/api/v1/wallet/ledger` | 交易明细（分页） |
| GET | `/api/v1/wallet/recharges` | 我的充值单 |
| POST | `/api/v1/wallet/recharges` | 建单（需 `Idempotency-Key` 头） |
| GET | `/api/v1/wallet/recharges/{id}` | 单详情 + 凭证 |
| POST | `/api/v1/wallet/recharges/{id}/cancel` | 取消未付款单 |

`payment_handoff()` 复用订单那套收款信息（ABA 二维码 / payment_link），
另外给一个 `bot_deeplink = https://t.me/<bot>?start=rc_<订单号>`，
用户在网页上建单后可以直接跳到机器人发截图。

**Mini App 钱包页**（`frontend/src/components/CustomerWallet.tsx`，底部导航第 4 个 tab）：

- 余额卡片（本金 / 赠送 / 赠送到期时间）+ 固定档位与自定义金额充值
- 建单后展示订单号、应付金额、赠送、有效期、ABA 二维码 / 收款链接、
  「去机器人发截图」按钮、取消按钮
- 「我的充值单」列表（状态、驳回原因、未完成单据可直接取消）
- 「交易明细」列表（每笔流水带变动后余额）

---

## 3.1 下单用余额抵扣

结算页勾选「用钱包余额支付」→ `POST /api/v1/orders` 带 `pay_with_wallet: true`。

服务端的处理顺序（`routers/orders.py`）：

1. 先按原有逻辑建单、算总价（**金额永远由服务端算，不信前端**）；
2. `wallet.spend_balance(customer_id, total_minor, biz_id=public_code)` —— 与建单
   **在同一个事务里**，所以「扣了钱没建单」或「建了单没扣钱」都不可能发生；
3. 余额不足 → 整个事务回滚、返回 **409 余额不足**，订单不会留下；
4. 扣款成功后订单直接是 `payment_status = PAID_CONFIRMED`、`payment_method = WALLET`，
   `payment_handoff` 不再返回任何转账信息（避免用户重复付款）。

状态机上的三处配套改动（`routers/admin_orders.py`）：

| 场景 | 行为 |
|---|---|
| 员工接单（ACCEPTED）时已 `PAID_CONFIRMED` | 直接进入 `PREPARING`，与「确认收款」后的既有行为一致；否则订单会卡在 `ACCEPTED` 无法走到 `READY` |
| 取消钱包支付的订单 | 允许，并在同一事务里调 `wallet.refund_payment` 退回余额，`payment_status = REFUNDED` |
| 取消人工转账的已确认付款订单 | **仍然禁止**（退款要走线下，不能一键退进钱包） |

退款按原支付的「赠送 / 本金」构成比例退回；退回的赠送金在数据库里新建一个批次
并给新的有效期窗口（`refund_bonus_valid_days`，默认 30 天），不会复活已过期的旧批次。

---

## 4. 员工流程

**A. Telegram 员工群**（与订单收款审核同一个群）：
充值单进入 `under_review` 后推送 `walletok_<id>` / `walletno_<id>` 按钮，
点「确认到账」即调用 `approve_recharge` 并给客户发通知。

**B. 管理后台 API**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/admin/recharges?status=` | 充值单列表 / 待审核 |
| GET | `/api/v1/admin/recharges/{id}/proof` | 凭证原图（代理 Telegram，file_id 不能直接暴露） |
| POST | `/api/v1/admin/recharges/{id}/approve` | 通过入账（可带 `received_minor`） |
| POST | `/api/v1/admin/recharges/{id}/reject` | 驳回（必填原因） |
| POST | `/api/v1/admin/recharges/{id}/received` | 审核前**暂存**实收金额 |
| GET | `/api/v1/admin/wallets/{customer_id}` | 查客户钱包 + 账本 |
| POST | `/api/v1/admin/wallets/{customer_id}/adjust` | 手动调账（仅 MANAGER） |
| POST | `/api/v1/admin/wallet/maintenance` | 运维：过期单清理 + 对账（见 §6） |

**C. 管理端「充值管理」页**（`frontend/src/components/AdminRecharges.tsx`）：
默认只看 `under_review`，可切「全部」。表格列：

| 列 | 说明 |
|---|---|
| 用户ID | `customers.id` |
| 用户名称 | `display_name`，缺失时回退 `username` |
| Telegram | `@username`（可点开）+ `telegram_user_id` |
| **余额** | 客户**当前**钱包余额，并在下方拆出本金 / 赠送（后端 LEFT JOIN `wallet.wallets`，没有钱包按 0） |
| 充值金额 | 本单金额 + 赠送额 + 订单号 |
| 状态 | 充值单状态 + 已传凭证张数 |
| 提交时间 | 建单时间 + 提交凭证时间 |
| 操作 | 「确认到账」「驳回」（仅未完成的单）+「详情」 |

「详情」展开后：转账截图、实收金额输入（暂存）、驳回原因输入、
查看该客户钱包与账本（调账表单仅 MANAGER 可见，后端与数据库还会再校验一次）。

> 余额取的是**当前**值，不是充值当时的快照——审核时要看的是
> 「这个客户账户里现在有多少钱、是不是老客户」，而不是历史值。

**实收金额**：员工可以先改实收（`set_received_amount` 持久化到
`recharge_orders.pending_received_amount`），点通过时由数据库按
「显式传入 > 暂存值 > 订单金额」取值，并**按实收重算赠送**——
否则用户转 $1 却填了 $100 的单，赠送会被放大 100 倍。

---

## 5. 幂等与并发

| 场景 | 幂等键 | 结果 |
|---|---|---|
| 机器人档位按钮连点 | `chat_id:message_id:a{金额}` | 同一条消息只建一张单 |
| 网页重复提交建单 | 前端 `Idempotency-Key` | 返回同一张单 |
| 审核按钮双击 / 两人同时点 | DB 行锁 + 「已 credited 直接返回」 | 只入账一次，8 线程实测 |
| 同一订单号重复扣款 | `biz_id` 锚点 | 只扣一次 |
| 同一张截图用两次 | `proof_unique_file_idx` 唯一索引 | 第二次被拒，凭证数不变 |
| 重复取消 | 状态已 `cancelled` 直接返回 | 幂等 |

**并发测试抓到的真实 bug**：先查后写的幂等判断在 READ COMMITTED 下会双扣款——
必须**拿到行锁之后再查一次**（见 `wallet.spend_balance`）。这条不要改回去。

---

## 6. 运维

本项目没有内置调度器，所以运维入口是幂等 HTTP 接口，交给外部 cron：

```bash
# 每 10 分钟：过期充值单 + 对账自检
*/10 * * * * curl -fsS -X POST -H "Cookie: admin_session_token=<manager>" \
  https://<host>/api/v1/admin/wallet/maintenance
```

返回里 `reconcile_drift` **必须恒为空数组**；非空说明账本与钱包余额不一致，
属于资金事故的第一信号，应当立刻告警。

赠送金到期：`wallet.expire_bonus(customer_id, currency)` 按批次清理，
可由同一个定时任务遍历有赠送余额的用户调用。

---

## 7. 文件清单

| 文件 | 说明 |
|---|---|
| `backend/alembic/versions/7e8f90123456_wallet_recharge.py` | wallet schema 全部 DDL + 资金函数（自包含） |
| `backend/alembic/versions/8f9012345678_pending_recharge_order.py` | `customers.pending_recharge_order_id` |
| `backend/alembic/versions/9f9012345678_orders_payment_method.py` | `orders.payment_method`（MANUAL / WALLET） |
| `backend/wallet_service.py` | 唯一数据访问层：只调数据库函数 + 错误码翻译 |
| `backend/routers/wallet.py` | 客户侧 / 员工侧 REST API |
| `backend/routers/orders.py` | 下单支持 `pay_with_wallet`（扣款与建单同事务） |
| `backend/routers/admin_orders.py` | 接单进制作 / 取消自动退款 / 列出支付方式 |
| `backend/routers/bot.py` | `/wallet`、档位按钮、凭证、群里审核（改动点见 §3） |
| `backend/telegram_service.py` | 钱包卡片 / 转账指引 / 审核推送文案 |
| `docs/ui/locales.json` | 新增 87 个文案键（en / zh-CN / km，三种语言键数保持一致） |
| `frontend/src/components/CustomerWallet.tsx` | 客户钱包页（余额 / 充值 / 充值单 / 明细） |
| `frontend/src/components/AdminRecharges.tsx` | 管理端充值审核页 |
| `frontend/src/format.ts` | 金额 / 时间格式化（金额一律最小单位整数） |
| `frontend/src/App.tsx` | 底部导航第 4 个 tab、结算页「用余额支付」、管理端侧栏入口 |
| `frontend/src/index.css` | 钱包样式 + 底部导航改 5 列 |
| `backend/tests/wallet_tests.sql` | 77 项 SQL 功能用例 |
| `backend/tests/wallet_concurrency_test.py` | 19 项 8 线程并发用例 |
| `backend/tests/wallet_api_e2e.py` | 51 项 API + Telegram webhook 端到端用例 |
| `backend/tests/run_wallet_tests.sh` | 一键回归 |
| `backend/tests/seed_wallet_fixture.sql` | 端到端验证用的种子数据 |

### 两条「下一张截图属于谁」的指针必须互斥

`customers.pending_payment_order_id`（餐费凭证）与
`customers.pending_recharge_order_id`（充值凭证）只能有一个有效。
进入任一流程时必须把另一个清空，否则用户从 `pay_` 深链进来传的**餐费**截图
会被残留的充值指针劫持，记到充值单上——这是真实踩到过的 bug。

---

## 8. 怎么验证

```bash
# 一键：建临时库 → 迁移 → 77 项功能用例 → 19 项并发用例
cd backend && PYTHON=python3 ./tests/run_wallet_tests.sh [PGHOST] [PGUSER]

# 端到端（需要先起服务 + 种子数据）
psql -d <db> -f tests/seed_wallet_fixture.sql
uvicorn main:app --port 8000
E2E_URL=http://127.0.0.1:8000 ./tests/run_wallet_tests.sh
```

端到端用例覆盖：建单/幂等/限额/取消 → 机器人建单 → deep link → 发截图 →
同一张图被拒 → 员工群确认到账 → 余额与账本核对 → STAFF 不能调账 →
下单余额抵扣（含重复提交不重复扣款、余额不足不留单）→ 取消自动退款。
断言全部基于**增量**，可反复运行。

前端：`npm run build`（tsc + vite）与 `npx oxlint` 通过。

---

## 9. 已知限制与后续

1. **前端没有浏览器自动化测试**：类型检查、构建、lint 和接口契约都验过，
   但没有在真实浏览器里点过一遍（Telegram WebApp 的 initData 登录无法在本地伪造）。
   上线前请在 Telegram 里走一遍：钱包页充值 → 发截图 → 员工确认 → 结算勾选余额支付。
2. **赠送规则没有运营界面**：规则在 `wallet.recharge_rules` 表里，
   默认**不配置任何规则 = 不赠送**（安全默认值）；要上活动时直接改表或加界面。
3. **`/report`、`/staff` 等管理端功能仍未实现**（与钱包无关的既有缺口）。
4. **退款只支持全额退回**：`/api/v1/admin/orders/{id}/status` 的取消会全额退款；
   部分退款需要再开一个接口（数据库函数 `refund_payment` 已支持部分金额）。
