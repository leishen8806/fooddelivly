# Codex 修复提示词：订单生命周期补丁（取消 / 超时 / 缺货）

> 背景：你刚完成了"顾客自助取消、取消退券、未付款超时自动取消、缺货处理"（迁移 `m0123456789f`、`backend/order_lifecycle.py`、`routers/orders.py`、`routers/admin_orders.py`）。独立审阅后发现下列问题，**请在提交之前一次性修完**。本任务只修这些问题，不扩展功能，不改订单状态机主流程、钱包账务规则、管理后台页面。
> 工作区里还有 `docs/ADMIN_IA.md`、`docs/CODEX_ADMIN_REDESIGN_PROMPT.md` 等文档（未跟踪），不要改动，提交时与代码分开。

## 0. 先做

1. `git status`；用 `git diff` 重读你自己的改动，对照下面每一条确认问题属实（不属实的写明理由，不要硬改）。
2. 输出 ≤30 行简报：每条问题的确认结论 + 你的修法 + 涉及文件。
3. 验证环境：Docker `postgres:16-alpine`；Python 3.12 venv（3.15 装不上 pydantic-core）；Windows 需 `pip install tzdata`；端口 8000 常被占，用 18000；测试需 `JWT_SECRET`、`WEBHOOK_SECRET`。种子见 `backend/tests/seed_second_store.sql`，另插 staff 1(MANAGER, MAIN)、customer 1、category 1、product 1。"主店读本店支付截图"依赖真实 Telegram，离线必挂（已知环境项）。

## 1. 必须遵守

- 隔离语义不变：订单、客户用 `store_scope_clause` / `can_access_store`；员工、审计用 `strict_store_clause`；跨店访问返回 404。
- 金额最小单位整数；钱包资金操作必须同事务、幂等。
- 新增文案三语同步写入 `docs/ui/locales.json`（key 集合一致，无裸 key）。
- 每个新写入点都写 `OrderEvent` / `AuditLog`（`AuditLog.store_id` 取订单所属门店）。
- 不删除或放宽现有断言；失败如实报告。

## 2. 问题与修法

### 问题 1（高）：取消/超时后没有通知顾客，也不更新员工群卡片
**现状**：`expire_unpaid_orders`、`cancel_customer_order`、`resolve_customer_order_issue(CANCEL_ORDER)` 取消订单后，顾客收不到消息，员工群里的订单卡仍显示"待接单"并带按钮。
**修法**：
- 新增 `order_lifecycle.notify_cancelled(db, order, reason_key)`：在**事务提交之后**调用（失败只记日志，不影响取消结果）。
  - 私聊顾客（用 `order.customer_language`，文案 key 如 `order.cancelledByCustomer` / `order.cancelledExpired` / `order.cancelledAfterShortage`，说明原因、优惠券是否已退回、钱包金额是否已退回）。
  - 调用 `telegram_service.update_order_message` 更新群卡片（与 `admin_orders.change_order_status` 里已有写法一致，用 `store_for_order` 取群 ID 和群语言），使卡片显示已取消且无操作按钮。
- 员工取消路径 `change_order_status` 已有通知，**检查并统一**：三条新路径都走同一个函数，避免三份拷贝。
- 用 monkeypatch/假 Telegram 客户端写测试：断言取消后确实发了私聊和卡片更新；Telegram 抛异常时订单仍为 `CANCELLED` 且已提交。

### 问题 2（高）：迁移默认 30 分钟，部署即批量取消历史订单
**现状**：`stores` / `store_settings` 新列 `unpaid_order_timeout_minutes` 的 `server_default="30"`，部署后首个 60 秒周期会取消所有超过 30 分钟的 `NEW`+未付款历史订单并退钱包。
**修法**：
- 迁移默认值改为 **0（关闭）**；`models.py` 的 `default` 同步。已有门店不会因升级而突然批量取消。
- 后台设置页（`admin_settings` 已支持该字段）的文案说明"0 = 不自动取消"；若前端有输入控件，补 `min=0` 与提示。
- 把 `m0123456789f` 改成可在**尚未部署的库**重新生成；如果你已经对任何共享库执行过它，则新增一个 `n0123456789g` 迁移把默认改 0 并保留已设置的值，并在汇报里说明。
- 测试：全新库升级后 `unpaid_order_timeout_minutes = 0` 且调度器不取消任何订单；设置为 30 后才生效。

### 问题 3（高）：超时计时与"已付款未传凭证"的风险
**现状**：按 `Order.created_at` 计时；`REJECTED`（凭证被拒、等重传）的订单可能刚被拒绝就被取消；顾客已在 ABA 付款但尚未发凭证时到点被取消，资金悬空。
**修法**：
- `REJECTED` 订单从**最近一次拒绝时间**重新计时（取该订单最新 `PaymentReview.created_at` 且 decision=REJECTED；没有则退回 `created_at`）。
- 到期前提醒：新增 `unpaid_order_reminder_minutes`（可复用同一迁移：默认 0=不提醒；开启时在到期前 N 分钟私聊提醒一次，幂等——用订单事件 `EXPIRY_REMINDER_SENT` 去重）。
- 超时取消后若顾客仍通过 Bot 提交凭证：Bot 的拒绝文案要明确"订单已因超时取消，如已付款请联系店员"（`bot.py` 现有的"订单已结束"提示补充说明，三语），并在群里（或审计）留一条 `LATE_PROOF_AFTER_EXPIRY` 记录，供店员人工处理。
- 测试：REJECTED 订单计时基准；提醒只发一次；过期后提交凭证的提示与记录。

### 问题 4（中）：已接单/已付款后无法报缺货 —— 已决策：**删品并退差价（方案 a）**
**现状**：`report_shortage` 用 `customer_can_cancel` 限制，只有 `NEW`+未付款可报；而真实缺货多发生在已接单/已付款/备餐中。
**已定业务规则**（用户决策，不得自行改变）：
- 适用：订单状态 `ACCEPTED` 或 `PREPARING`（**到 `READY` 及之后不可再报缺货**，此时只能走既有的经理退款），付款状态为 `PAID_CONFIRMED` 或 `PARTIALLY_REFUNDED`，或钱包已全额支付；`NEW`+未付款沿用现有逻辑（删品/换品/取消）。
- 已付款订单**只允许"删除缺货商品并退差价"，不允许换品、不允许改价**。顾客在缺货通知里仍可选择"删品"或"整单取消"（整单取消的退款沿用既有规则：钱包部分退钱包；ABA 已确认收款部分**不自动退**，见下）。
- 员工（STAFF 或 MANAGER）可报缺货；员工**不能**直接替顾客决定，必须顾客选择后才执行（沿用 `OrderIssue` 流程），有未处理缺货时订单不能继续推进到 `READY`（含后台与群按钮）。
- **差价规则**：优惠券折扣**保持原值**，仅封顶为"新小计"（折扣不超过新小计），不按比例重算；不因删品退回优惠券、也不向顾客加收。配送费、服务费不变。新总额 = `max(新小计 − 折扣, 0) + 配送费 + 服务费`；**应退金额 = 原总额 − 新总额**。若删除后订单无剩余商品，改走整单取消流程（不是删品）。
- **退款分配顺序**：先退钱包支付部分（`wallet_paid_minor` 内，复用 `wallet_service.refund_payment` 的**部分退款**，幂等键带"已退金额 + 本次金额"，参考 `admin_orders.refund_wallet_order` 的写法）；超出钱包支付部分的差额，属于 ABA 已收款，**不得伪造自动退款**：生成一条"待人工退款"记录供经理线下处理。
- 新增表 `order_refunds`（迁移，带 `store_id`）：`id, order_id, issue_id, amount_minor, method('WALLET'|'ABA_MANUAL'), status('DONE'|'PENDING'|'FAILED'), reference, note, created_at, done_at, done_by`。`WALLET` 在同事务内置 `DONE`；`ABA_MANUAL` 初始 `PENDING`，经理通过新接口 `POST /api/v1/admin/order-refunds/{id}/complete`（必填 `reference` 如 ABA 转账号，门店隔离，写审计）标记完成。列表 `GET /api/v1/admin/order-refunds?status=` 供后台待办使用（仅 MANAGER，按门店隔离）。
- **财务口径不得被改坏**：`admin_stats` 的 "ABA 订单收款" 用 `PaymentReview` 关联订单的 `external_due_minor` 汇总。删品后**不要覆盖已确认的收款金额**：新增 `orders.adjusted_total_minor`/`removed_minor`（或等价字段）记录调整，`total_minor` 更新为新总额但保留 "原已确认收款额"（例如 `paid_confirmed_minor` 快照，在确认支付时写入；历史订单回填为确认时的 `external_due_minor`）。财务页新增"缺货退款"行（`WALLET` 已退 + `ABA_MANUAL` 待处理/已处理），并保证：订单销售额按调整后总额，ABA 收款按实收快照，两者差额等于退款记录合计。先写这一段的设计小结，**等用户确认后再改财务统计**。
- 防内部勾结（延续钱包既有的 self-dealing 约束）：员工不能给自己的 Telegram 对应顾客账户报缺货并触发退款（沿用 `b0123456789a_self_dealing_guards` 的判定方式，查现有实现后复用）；单笔缺货退款超过门店阈值（新增 `shortage_refund_review_minor`，默认 0=不限制）时，需要 MANAGER 确认后才执行。
- 通知：顾客选择后私聊确认退款金额与去向（钱包已退 / 待人工退款），群卡片更新金额，写 `OrderEvent`。
- 三语文案同步；后台订单行/详情展示缺货处理状态与待人工退款。
**实现前先做**：输出设计小结（状态矩阵、金额算例、迁移字段、对统计的影响、幂等键方案），等用户确认再编码。
**测试**：钱包全额支付订单删品 → 钱包退差价、订单总额下降、恒等式成立；MIXED 订单差额大于钱包部分 → 钱包部分 `DONE` + ABA 部分 `PENDING`；`READY` 后报缺货 409；重复点击/并发只退一次；优惠券折扣封顶；删光商品走整单取消；跨店 404；STAFF 不能完成 ABA 人工退款（403）；自己给自己退款被拒；财务统计中 ABA 收款不被删品改变、"缺货退款"行正确。

### 问题 5（中）：替换商品的价格、规格、售卖时间不对
**现状**：`report_shortage` 直接取 `Product.price_minor`；替换时沿用旧商品的 `options_json`；没检查售卖时间窗/甜度/必选规格。
**修法**：
- 价格与可售性必须走 `store_context.load_overrides` + `effective_product()`（与 `POST /orders` 同一逻辑）：用门店有效价、门店上下架、售卖时间窗（`sale_window.is_on_sale`，按门店时区）。
- 替换商品若 `sweetness_enabled` 或有必选规格组：**不允许作为替换候选**（报缺货时返回 422 "该商品需要选择规格，不能直接替换"），避免产生缺少必选项的订单行。
- 替换时**重置** `options_json = {}`（不继承旧商品选项）。
- 币种校验保留。
- 测试：二号店覆盖价 → 替换后订单行单价 = 门店有效价；有必选规格/甜度的商品被拒；售卖时间外被拒。

### 问题 6（中）：多店下替换候选被错误过滤
**现状**：过滤 `Product.store_id IS NULL OR Product.store_id == order.store_id`，而迁移 `g0123456789f` 把现有商品都回填成了主店的 `store_id`，二号店永远选不到。
**修法**：商品目录是全店共用的（`/menu` 不按 `products.store_id` 过滤），这里同样**不要按 `products.store_id` 过滤**，可售性只由 `effective_product()` 决定。测试：二号店订单可以选主店创建的商品作替换。

### 问题 7（中）：有未处理缺货时仍可接单/确认支付
**修法**：`change_order_status`（ACCEPTED 以及其后的推进）与 `review_payment` 在存在 `OrderIssue.status == 'OPEN'` 时返回 409，文案"该订单有未处理的缺货，请等待顾客选择"；群里按钮路径（`bot.py` 的 accept/confirmpay 回调）同样拦截，沿用其本地化错误提示。取消不受限。测试覆盖后台与群回调两条路径。

### 问题 8（中）：顾客处理缺货后，员工无感知，卡片金额过期
**修法**：
- `resolve_customer_order_issue` 提交后：写 `OrderEvent`（`ORDER_ISSUE_RESOLVED:<resolution>`）与 `AuditLog`（actor 为顾客时 `actor_staff_id` 不能为空——审计表该列非空，则只写 `OrderEvent`，不要伪造员工 ID）。
- 更新群订单卡（金额、商品变更）并向群里发一条简短提示（群语言）："顾客选择了 X"。
- 删品/换品后订单金额变化，要同步更新 `external_due_minor` 并保证 `小计 − 优惠 + 配送费 + 服务费 = 总额`（`_recalculate_unpaid_order` 已做，补测试）；优惠券因门槛不足被退回时，事件里要体现。
- 测试：三种 resolution 各一条，断言事件、卡片更新调用、金额恒等式。

### 问题 9（低）：退款幂等键不统一
**修法**：员工取消路径（`refund:{code}`）与 `cancel_order_record`（`refund:{code}:cancel`）改成同一 helper，使用同一个幂等键；确认二次取消/并发取消只退一次。测试：并发/重复调用后钱包流水仅一笔退款。

### 问题 10（低）：测试覆盖不足
新增后端集成测试（沿用 `backend/tests` 的 HTTP 风格，可并入 `cross_store_security_test.py` 或新建 `order_lifecycle_e2e.py`），至少覆盖：
- 顾客取消：他人订单 404；`PROOF_SUBMITTED` / `ACCEPTED` 订单 409；`MIXED` 钱包订单取消后钱包退回、优惠券归还；过期券归还后为 `EXPIRED`。
- 超时：默认关闭不动作；开启后只取消符合条件的订单；多门店各用各自阈值；调度重复执行幂等。
- 缺货：跨店报缺货 404；STAFF/MANAGER 均可；重复报缺货 409；顾客三种处理；他人订单 404。
- 问题 1/3/7/8 的通知与拦截（用假 Telegram）。
保留并继续通过：`cross_store_security_test.py`、`daily_report_test.py`、`order_lifecycle_rules_test.py`。

## 3. 交付格式（必须）

1. 改动文件列表（含迁移与文案）。
2. 新增/修改的测试名称。
3. **实际执行的验证命令与结果摘要**：迁移链（空库升级到 head，并说明是否需要补 `n…` 迁移）、各测试脚本、`npm run build` / `tsc`、i18n 校验。
4. 未验证项与原因。
5. 需要用户决策的问题（问题 4 的资金规则已决定为"删品并退差价"；仍需用户确认的是：财务统计改法、`shortage_refund_review_minor` 阈值默认值）。
6. 提交：代码一个 commit，不含 `docs/ADMIN_IA.md` 与 Codex 提示词文档；**不推送、不部署**。

## 4. 不做

已付款订单的换品或改价；ABA 自动原路退款（只做"待人工退款"记录）；后台页面改版（见 `CODEX_ADMIN_REDESIGN_PROMPT.md`）；任何生产环境操作。

先完成第 0 节简报，等确认后再改代码。
