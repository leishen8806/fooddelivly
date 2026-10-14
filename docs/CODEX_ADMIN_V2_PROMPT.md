# Codex 执行提示词：管理后台 v2（列表 + 二级页 + 图表）

> 规格：[`docs/ADMIN_IA_V2.md`](ADMIN_IA_V2.md)（本任务的唯一"做什么"依据）。通用规则、验证环境、安全要求、交付格式沿用 [`docs/CODEX_ADMIN_REDESIGN_PROMPT.md`](CODEX_ADMIN_REDESIGN_PROMPT.md) 的 §0、§1、§3、§4，**本提示词只列差异与阶段**。冲突时以 ADMIN_IA_V2.md 为准；仍有疑问先停下来问。
> 前置条件：阶段 A（路由化骨架 + 通用组件）已提交；`git status` 干净。否则先停。

## 0. 开工前

1. `git status` / `git log -5`；重读 `ADMIN_IA_V2.md` 全文、`ADMIN_IA.md` 附录 A/B/C、`store_context.py`、`dependencies.py`、`routers/admin_orders.py`、`routers/admin_customers.py`、`routers/auth.py`、`daily_report.py`、`refund_metrics.py`，以及 `frontend/src/admin/` 现有组件。
2. 输出 ≤40 行简报：各阶段改动文件、迁移、风险、需要我确认的口径。**三个口径已有默认值（实收金额、日活、日期时区），按默认执行，除非我另行说明。**
3. 验证环境同前提示词（Docker postgres、Python 3.12、`tzdata`、端口 18000、已知环境项）。额外：前端需 `VITE_API_URL=http://127.0.0.1:18000 npx vite --port 3001 --strictPort --host 127.0.0.1`，后端 `ALLOWED_ORIGINS` 含 `http://127.0.0.1:3001`；浏览器验证请用本地生成的测试账号，不要写进仓库。

## 1. 与上一版提示词的差异（必须遵守）

- **新建/编辑一律用二级页面（独立路由），不用抽屉。** 列表右上角放主按钮，进入 `/…/new`；编辑进入 `/…/:id`；面包屑 + 返回；底部固定「取消 / 保存」；未保存离开提示；保存成功返回列表并**保留列表筛选**（筛选写 URL）。
- **布局硬规则（见 ADMIN_IA_V2 §1.5）**：列表页自上而下 = 页头（右上角「新增」入口）→ 工具栏（仅搜索/筛选/日期）→ 列表 → 分页。**列表页内不得出现任何新建/编辑表单**，更不能把表单排在列表前面；新建/编辑只在二级路由页。每个列表页必须有 DOM 测试断言：除工具栏控件外无 `<form>`/表单输入、存在指向二级路由的「新增」入口。现有页面里表单在列表前的（商品、员工、优惠券、设置）在对应阶段全部迁出，不得保留旧布局或做成折叠区。
- 不引入图表库：用内联 SVG 自绘（柱、折线、环形），颜色之外必须有文字或图案区分，支持深浅色与三语，每张图有数据表格式的可访问替代（`<table>` 或 `aria-label` 摘要）。
- 旧路由兼容：`/admin/finance` → `/admin/finance/daily`；旧接口 `GET /admin/orders`、`GET /admin/customers` 保留不改。
- 隔离、权限、审计、参数化、N+1 防护、三语同步、禁止浮点金额运算等 §1 规则**全部沿用**；`AuditLog.store_id` 语义不变（目录类 NULL=总部级）。

## 2. 分阶段交付（顺序固定；每阶段结束停下汇报，等我确认）

### 阶段 B0：日活采集（先上线，让数据积累）
- 迁移新增 `customer_daily_activity(customer_id, store_id, activity_date, first_seen_at, last_seen_at, PK(customer_id, activity_date))`，索引 `(activity_date, store_id)`；`activity_date` 为**门店时区**的日期（客户无门店时用主店时区）。
- 在 `routers/auth.py` 的 `telegram_login` 成功路径 upsert（`INSERT … ON CONFLICT DO UPDATE SET last_seen_at`）。**失败只记日志，绝不影响登录**；不得在循环里逐条提交。
- 迁移里用订单回填历史（有下单的客户按订单日期各记一行），并在注释与文档里标明"历史部分是下单口径，上线后是打开口径"。
- 测试：同一天多次登录只一行、`last_seen_at` 前进；跨天两行；门店时区跨零点边界（Asia/Phnom_Penh）；写入异常（mock）不影响登录 200；并发登录不产生重复行。

### 阶段 B1：订单列表（需求 2、3）
- 后端：`orders/search`、`orders/counts`、`orders/{code}` 详情，字段与口径见 ADMIN_IA_V2 §3（`customer{…telegram_chat_url}`、订单金额=小计、优惠金额、应付、`received_minor = paid_confirmed − 已完成退款`，`REFUNDED` 与未付款为 0、`refund_pending_minor`）。搜索匹配订单号/昵称/用户名；日期按**门店时区**取 `[from 00:00, to+1 00:00)`；排序白名单；服务端分页；`%`/`_` 转义。
- 前端：`/admin/orders`（DataTable：列见规格、状态 Tab 带计数、搜索框、起止日期、支付筛选、URL 状态、手机端卡片布局）、`/admin/orders/:code` 二级详情页（概览、金额明细、商品、付款凭证、时间线、缺货状态与退款记录）。
- **同时修掉已知缺口**（见 `DEPLOYMENT_CHECKLIST.md` §10 的 1、2）：「报缺货」按钮条件与后端一致（未付款 NEW，或 ACCEPTED/PREPARING 已付款，已付款仅能删品）；订单行/详情显示缺货状态；有 `OPEN` 缺货时禁用接单/确认支付/出餐并说明原因。
- 测试（后端 HTTP 级，沿用 `order_lifecycle_integration_test.py` 的写法）：搜索三种命中、日期含整天边界（`2026-10-01 00:00:00` 与 `2026-10-02 23:59:59` 命中，`10-03 00:00:00` 不命中）、门店时区、跨店 404、STAFF 可用、SQL 注入样例、25 行分页 SQL 条数不随行数增长、金额口径用例（含部分退款）；前端：`DataTable` 排序/分页/行点击、日期范围校验的单测。

### 阶段 B2：财务拆分（需求 6）
- `/admin/finance/daily`（日报，原样）与 `/admin/finance/stats`（区间统计，原样，含现金净流入等**全部**卡片）。菜单分两个入口；STAFF 只读，"发送到员工群"仅 MANAGER。
- 顺手：警示卡使用明显警示色且不只靠颜色；优惠为 0 不显示 `-$0.00`。
- 验收：对照 `git show <阶段A前提交>:frontend/src/App.tsx` 确认卡片一个不少；快照式测试或断言文案 key 全部出现。

### 阶段 C1：商品管理 + 分类管理（需求 4）
- 菜单拆两个入口：`/admin/products`、`/admin/categories`。
- 商品列表：列与筛选见规格 §4（名称搜索、分类、状态、缺翻译、带规格）；右上角「新增商品」→ `/admin/products/new`；编辑 `/admin/products/:id`（分块：基本、规格与加料、售卖时间——沿用 `AdminProductRules`；新增保存后跳编辑页继续配置规格）。
- 分类列表：`GET /admin/categories` 增加 `product_count`；右上角「新增分类」→ `/admin/categories/new`；编辑页含三语名称、排序、启用。
- 多店且非总部：页面只读并提示"由总部维护"（后端本就 403，前端不要让用户走到报错）。
- 用 `LocalizedInput`（页签、缺翻译红点、英语必填）与 `MoneyInput`；**必须在真实浏览器里实际键入验证**价格输入（52、5.5、0.05）。

### 阶段 C2：优惠券（需求 5）
- `/admin/coupons` 列表 + 右上角「新增优惠券」→ `/admin/coupons/new`（现有表单原样搬入，行为不变）；保存后返回并刷新。列表消除 N+1（一次查出全部 links 再分组），分享文案三语化。

### 阶段 D：员工权限 + 设置（需求 7）
- 员工：列表（含最近登录）、`/admin/staff/new`、`/admin/staff/:id`；迁移新增 `staff.last_login_at`、`must_change_password`（均有默认值，可回滚）；`POST /admin/staff/{id}/reset-password`（临时密码仅返回一次，不入日志/审计明文）；首次登录强制改密页；自己不能停用/降级。
- 设置：`/admin/settings` 概览（分组只读卡片）+ 二级编辑页 `business / payment / notice / region`；币种只读，变更需总部二次确认并写审计；`POST /admin/settings/test-notification`（返回失败原因）。
- 测试：重置密码与强制改密流程、跨店 404/403、币种变更审计、测试消息失败路径。

### 阶段 E：总览图表与用户统计（需求 1）
- 后端 `GET /api/v1/admin/dashboard?days=7|30&store=`：`kpis`（今日订单、今日销售额、待接单、待核款、待处理退款、**总用户数、今日新增用户、今日活跃用户**）、`series`（按日：订单、销售额、新增用户、日活）、`status_breakdown`、`payment_breakdown`、`hourly`、`top_products`；一次请求、服务端聚合、门店隔离、日期按门店时区；销售额不含取消。
- 前端：自绘 SVG 图表（订单趋势、状态分布、支付占比、时段分布、热销 TOP10、用户趋势）+ 指标卡（可点击跳转）；空数据、加载、错误三态；日活数据不足时显示"自 xx 日起统计"提示而不是 0 的折线。
- 测试：已知数据集断言每个序列与口径（含取消订单排除、门店时区跨零点、日活口径）、跨店隔离、无 N+1。

### 阶段 F：不在本任务内（先别做）
门店管理 CRUD、门店切换器真实接入、门店覆盖价。

## 3. 交付与验证（每阶段）

按前一提示词 §3 的格式交付，并**额外**：
- 提供桌面（1280）与窄屏（375）下的关键页面截图或浏览器验证记录；高棉语至少看一页的表头与按钮不溢出。
- 前端：`npx tsc -p tsconfig.app.json --noEmit`、`npm run build`、`npm test`、`npm run lint`（含 i18n 校验）全绿。
- 后端：迁移链在空库与"带数据副本库"各跑一次；`cross_store_security_test.py`、`daily_report_test.py`、生命周期测试保持通过。
- 如实报告未验证项；不得删除或放宽断言；**不推送、不部署**；每阶段一个 commit。

## 4. 开始

先完成 §0 并输出简报，等我确认后开始阶段 B0。
