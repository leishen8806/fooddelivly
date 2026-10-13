# Codex 完整执行提示词：Tea Cafe 管理后台 v2

你是 Tea Cafe 的资深全栈工程师。仓库：`E:\TeaCafe`（后端 FastAPI + SQLAlchemy async + PostgreSQL，迁移用 Alembic；前端 React + Vite + TypeScript，路由 react-router，i18n 来自 `docs/ui/locales.json`，三语 zh-CN / en / km）。
任务：按下面的需求重做管理后台的菜单、列表和二级页面，并补齐后端接口。本提示词自成一体；更细的页面规格见 `docs/ADMIN_IA_V2.md`、接口与组件契约见 `docs/ADMIN_IA.md` 附录 A/B/C，**冲突时以本提示词为准，仍有疑问先停下来问，不要猜**。

---

## 0. 开工前（必须先做，输出简报后再改代码）

1. `git status` / `git log -5`。前置条件：阶段 A（`frontend/src/admin/` 路由化骨架与通用组件）已提交，工作区干净。否则停下来告诉我。
2. 阅读：`docs/ADMIN_IA_V2.md`、`docs/ADMIN_IA.md`（附录 A/B/C）、`docs/FINANCE_METRICS.md`、`docs/DEPLOYMENT_CHECKLIST.md` §10、`backend/store_context.py`、`backend/dependencies.py`、`backend/routers/{admin_orders,admin_customers,admin_stats,admin_staff,admin_settings,admin_coupons,products,auth}.py`、`backend/{daily_report,refund_metrics,order_lifecycle}.py`、`frontend/src/admin/**`、`frontend/src/components/Admin*.tsx`。
3. 输出 ≤40 行简报：每个阶段将改的文件、迁移、风险、需要我确认的问题。**三个口径已有默认值（见 §3），按默认执行，除非我另行说明。**
4. 验证环境（本机已验证可行，照此做）：
   - Docker 起 `postgres:15-alpine`（线上是 15）；Python **3.12** venv（3.15 装不上 pydantic-core）；Windows 需 `pip install tzdata`；`alembic upgrade head`。
   - 后端端口用 **18000**（8000 常被占）；环境变量：`JWT_SECRET`、`BOT_TOKEN`、`BOT_USERNAME`、`WEBHOOK_SECRET`、`COOKIE_SECURE=false`、`APP_ENV=development`、`ALLOWED_ORIGINS=http://127.0.0.1:3001`、`RATE_LIMIT_ENABLED=false`、`DAILY_REPORT_ENABLED=false`。
   - 前端：`VITE_API_URL=http://127.0.0.1:18000 npx vite --port 3001 --strictPort --host 127.0.0.1`。
   - 后端测试需 `RUN_DB_TESTS=1` 和 `DATABASE_URL=postgresql+asyncpg://...`；HTTP 级测试沿用 `backend/tests/order_lifecycle_integration_test.py` 的写法（ASGITransport + `dependency_overrides[get_db]` + 手签 JWT cookie + Telegram mock）。
   - 已知环境项（不是你的缺陷）：`cross_store_security_test.py` 里"主店读本店支付截图"依赖真实 Telegram，离线必挂；"二号店运维只覆盖二号店"把门店 id 写死为 2，共享库上会失败；手工插入固定 id 的 staff/customer 后需 `SELECT setval(pg_get_serial_sequence('staff','id'), (SELECT max(id) FROM staff))`。
   - 浏览器验证用**本地生成的测试账号**，口令不要写进仓库或回复。

---

## 1. 不可违反的规则

**安全与隔离（已落地，勿回退）**
- `staff.store_id IS NULL` = 总部账号。员工、审计表必须用 `strict_store_clause` / `can_manage_store_row`（NULL 对门店账号不可见）；订单、客户等历史数据沿用 `store_scope_clause` / `can_access_store`（NULL 可见）。
- 共享目录（商品/分类/规格）写权限用 `get_catalog_manager`（总部，或仅一家活跃门店时放行）。目录类 `AuditLog.store_id = NULL` 是有意设计（总部级），不要改成操作者门店。
- 每个新写接口：路由层校验角色、门店隔离、事务、写 `AuditLog`（带正确 `store_id`）、跨店返回 **404**、无权限 **403**。
- 排序字段白名单；搜索一律参数化（`ILIKE :q` + 转义 `%` `_`）；禁止字符串拼 SQL。
- 密码、临时密码、Token、支付凭证内容不得写入日志或审计明文。
- 列表接口禁止 N+1：25 行分页的 SQL 语句数不随行数增长（用 SQLAlchemy 事件计数在测试里断言）。

**业务与金额**
- 金额一律最小单位整数；前端**禁止浮点换算金额**（用 `MoneyInput` 的字符串/整数方案；KHR/JPY/VND 为 0 位小数）。
- 不改订单状态机、付款流程、钱包账务、退款规则；财务口径沿用 `refund_metrics.py` 与 `FINANCE_METRICS.md`。
- 旧接口保持兼容：`GET /admin/orders`、`GET /admin/customers` 保留不改；删除旧接口需单独 PR。

**i18n**
- 所有新增文案同步写入 `docs/ui/locales.json` 三语，key 集合一致，无裸 key，无重复 key。注意占位符：**前端文案用 `{{x}}`**（i18next），后端 `tr()` 两种都支持；前端文案写单括号会不生效。
- 高棉语检查表头、按钮、面包屑不溢出。

**工程**
- 不引入大型 UI 框架或图表库；图表用内联 SVG 自绘。新增依赖须说明理由与版本。
- 新代码有类型，禁止 `any`（第三方类型缺失需注释）。
- 每个阶段一个小而可审的 commit；**不推送、不部署、不碰任何线上环境**。

---

## 2. 页面布局硬规则（所有后台页面，验收项）

1. **列表页只放列表。** 自上而下固定：页头（标题 + 右上角主按钮「新增 …」）→ 工具栏（搜索、筛选、日期，仅用于**查询列表**）→ 列表/表格 → 分页。**页内不得出现任何新建/编辑表单**，不得把表单排在列表前面、上方、侧边或折叠区里。
2. **新建与编辑只存在于二级页面（独立路由）**。上一级只提供入口：右上角主按钮（新建）、行内「编辑」（编辑）。
3. 二级页自上而下：面包屑 → 页标题 → 表单分块 → 底部固定「取消 / 保存」；未保存离开提示；保存失败保留输入；保存成功回到列表并**保留原筛选与页码**（筛选写在 URL）。
4. 设置页 `/admin/settings` 只是分组只读概览卡片，每张卡一个「编辑」入口进二级编辑页，编辑表单不放在概览页。
5. 「每日报表」里的"发送到员工群"是操作按钮，可放在报表下方或页头操作区，不得排在数据前面。
6. 现有页面中表单在列表前的（商品页的分类/商品表单、员工页的新建表单、优惠券页的创建表单、设置页长表单）**在对应阶段全部迁出**，不保留旧布局，也不做成折叠区。
7. **自动化验收**：每个列表页一条 DOM 测试——断言除工具栏内的搜索/筛选/日期控件外无 `<form>` 或表单输入；存在指向二级路由的「新增」入口；二级页含面包屑与保存/取消。
8. 手机端（≤768）表格降级为卡片列表，订单页必须可用。

---

## 3. 口径（默认值，直接按此实现）

1. **实收金额** `received_minor = paid_confirmed_minor − 已完成退款(order_refunds.status='DONE' 合计)`；未确认付款为 0；`payment_status = 'REFUNDED'` 为 0；存在待处理退款时另给 `refund_pending_minor` 用于小字提示"待退 $x"。
2. **日活** = 当日（**门店时区**）打开过 Mini App 的**去重用户数**；采集点是 `/auth/telegram` 登录成功。上线前的历史无法还原，迁移时用"当天有下单"回填并在注释/文档标明"历史=下单口径，上线后=打开口径"，两种口径不混用。
3. **日期过滤** 按**门店时区**：起始日 00:00:00 到结束日 23:59:59.999，实现为 `[from 00:00, to+1 00:00)` 转 UTC；只选起始则到今天，只选结束则从最早；起始大于结束报 422/前端提示。
4. 「今日用户数」= **今日新增注册用户**；与「今日活跃用户」分开显示，避免混淆。
5. 订单列表金额列：**订单金额** = `subtotal_minor`（商品小计，删品后为调整后金额）；**优惠金额** = `coupon_discount_minor`（0 显示 `—`，不显示 `-$0.00`）；**应付金额** = `total_minor`（小计 − 优惠 + 配送费 + 服务费）；**实收金额** 见 1。
6. **打开聊天**：有 `@username` → `https://t.me/<username>`；没有 → `tg://user?id=<id>` 并在旁提供「复制 ID」（该链接只能在装了 Telegram 的设备打开）。沿用 `admin_customers.py` 现有的 `telegram_chat_url` 逻辑，不要另写一套。

---

## 4. 新菜单与路由

```
运营   总览 /admin/overview
       订单 /admin/orders            二级：/admin/orders/:code（详情）
       充值审核 /admin/recharges
       用户 /admin/customers
商品   商品管理 /admin/products       二级：/new、/:id
       分类管理 /admin/categories     二级：/new、/:id
       优惠券管理 /admin/coupons      二级：/new、/:id
财务   每日报表 /admin/finance/daily
       财务统计 /admin/finance/stats
系统   设置 /admin/settings           二级：/business、/payment、/notice、/region
       员工权限 /admin/staff          二级：/new、/:id
       操作日志 /admin/audit
```
- `/admin/finance` 重定向到 `/admin/finance/daily`。
- 权限：STAFF 只看 总览、订单、充值审核、每日报表/财务统计（只读，"发送到员工群"仅 MANAGER）；其余仅 MANAGER；多店且非总部时，商品/分类/优惠券写操作页面只读并提示"由总部维护"（后端本就 403，前端不要让用户走到报错）；总部账号（`MANAGER` 且 `store_id` 为空）顶栏显示门店切换器（本期只有「全部门店」，不实现真实切换）。
- 菜单配置沿用 `frontend/src/admin/layout/nav.ts`，用 `nav.test.ts` 扩充角色可见性断言。

---

## 5. 分阶段交付（顺序固定；每阶段结束停下汇报，等我确认再继续）

### 阶段 B0：日活采集（先上线，让数据积累；不动界面）
- 迁移新增 `customer_daily_activity(customer_id, store_id, activity_date, first_seen_at, last_seen_at, PRIMARY KEY(customer_id, activity_date))`，索引 `(activity_date, store_id)`；`activity_date` 取门店时区日期（客户无门店用主店时区）。
- `routers/auth.py` 的 `telegram_login` 成功路径 upsert（`INSERT … ON CONFLICT DO UPDATE SET last_seen_at`）。**失败只记日志，绝不影响登录响应**；不得循环里逐条提交。
- 迁移用订单回填历史（有下单的客户按订单日期各记一行），注释标明口径差异。迁移可回滚（downgrade 删表）。
- 测试：同一天多次登录只一行且 `last_seen_at` 前进；跨天两行；门店时区跨零点边界（Asia/Phnom_Penh）；写入异常（mock）时登录仍 200；并发登录不产生重复行。

### 阶段 B1：订单列表（需求：列表展示、新增列、搜索、日期过滤）
**后端**
- `GET /api/v1/admin/orders/search`：参数 `q`（匹配订单号包含、用户昵称、`@用户名`，不区分大小写）、`from`、`to`、`status`（可多值）、`payment_status`、`payment_method`、`limit`(1–100，默认 25)、`offset`、`sort`（白名单：`created_at`、`total_minor`，`-` 前缀降序）。返回 `{items,total,limit,offset}`，`items` 每项含：现有订单字段、`customer{id,display_name,username,telegram_user_id,telegram_chat_url}`、`room_number`、`subtotal_minor`、`coupon_discount_minor`、`coupon_name_snapshot`、`total_minor`、`received_minor`、`refund_pending_minor`、`items_preview`（前 2 项名称）、`item_count`、`open_issue_count`。
- `GET /api/v1/admin/orders/counts`：各 `order_status` 数量 + "待核款"(`PROOF_SUBMITTED`) 数量，供 Tab 角标；同样隔离并支持同一组筛选（不含 status）。
- `GET /api/v1/admin/orders/{code}`：详情——订单全字段、金额明细、`items`（含 `options_json`）、`proof`（状态/时间）、`events`（OrderEvent）、`issues`（缺货状态）、`refunds`（order_refunds）。
- 门店隔离沿用 `store_scope_clause`；跨店 404；STAFF 可用。

**前端**
- `/admin/orders`：页头 → 工具栏（搜索框「订单号 / 用户昵称」、起止日期、支付状态、支付方式）→ **状态 Tab（带计数）**→ 表格：订单号｜下单时间｜下单用户（Telegram 昵称 + @用户名）｜打开聊天（+复制 ID）｜房间号｜订单金额｜优惠金额｜应付金额｜实收金额｜支付｜状态｜操作（随状态变化的主按钮 + ⋯ 菜单）。筛选与页码写 URL；手机端卡片布局。
- `/admin/orders/:code` 二级详情页：概览、金额明细（小计−优惠+配送+服务=应付；钱包抵扣；实收；已退/待退）、商品、付款凭证（沿用现有查看与确认/拒绝）、时间线、缺货与退款记录。
- **同时修掉已知缺口**：①「报缺货」按钮出现条件与后端一致（`NEW` 且未付款；或 `ACCEPTED/PREPARING` 且已付款——已付款只能删品，不能换品），已有 `OPEN` 缺货的商品不再显示；②订单行/详情显示缺货状态（等待顾客选择 / 已处理）；有 `OPEN` 缺货时禁用接单/确认支付/出餐并显示原因。
- 测试（后端 HTTP 级）：三种搜索命中；日期边界（`2026-10-01 00:00:00` 与 `2026-10-02 23:59:59` 命中，`2026-10-03 00:00:00` 不命中，按门店时区）；金额口径（含部分退款、REFUNDED、未付款）；跨店 404；STAFF 可用；SQL 注入样例（`' OR 1=1 --`、`%`、`_`）；25 行分页 SQL 条数不随行数增长。前端：`DataTable` 排序/分页/行点击、日期范围校验、列表页布局 DOM 测试（§2.7）。

### 阶段 B2：财务拆分
- 「每日报表」`/admin/finance/daily` = 现有日报（日期、预览、文本、投递状态、发送）；「财务统计」`/admin/finance/stats` = 现有区间统计。**两页内容原样搬迁，所有卡片一个不少**（含现金净流入及公式提示、钱包/ABA 退款、待处理退款、缺货删品减少额、对账差额、在审核、取消金额）。对照阶段 A 之前的提交（`git show f750caa:frontend/src/App.tsx` 与现 `AdminDailyReport.tsx`）逐项核对，并写断言所有文案 key 都出现的测试。
- 顺手：警示卡（待处理退款、对账差额）使用明显警示色且不只靠颜色（图标/文字）；优惠为 0 不显示 `-$0.00`。

### 阶段 C1：商品管理 + 分类管理（拆成两个入口）
- **商品列表** `/admin/products`：页头右上角「新增商品」→ `/admin/products/new`；工具栏筛选：名称搜索、分类、状态（全部/上架/售罄）、是否缺翻译、是否带规格（先前端过滤，接口不变）；列：图片｜名称（当前语言，缺翻译标红徽标）｜分类 chips｜价格｜规格/加料数｜售卖时间摘要｜甜度｜状态｜排序｜操作（编辑、上下架）。
- **商品二级页**（新增 `/admin/products/new`、编辑 `/admin/products/:id`）：分块 = 基本信息（`LocalizedInput` 页签三语名称与描述，英语必填；图片上传；价格 `MoneyInput`；分类多选；排序；甜度；上架）→ 规格与加料 → 售卖时间（后两块沿用 `AdminProductRules`，编辑页内展示；新增保存后跳编辑页继续配置）。
- **分类列表** `/admin/categories`：`GET /admin/categories` 增加 `product_count`；右上角「新增分类」→ `/admin/categories/new`；列：名称（三语）｜商品数｜排序｜状态｜操作；编辑页 `/admin/categories/:id`（三语名称、排序、启用）。
- 多店且非总部：页面只读并提示"由总部维护"。
- 必须在**真实浏览器里实际键入**验证价格输入（52、5.5、0.05；KHR 无小数）。

### 阶段 C2：优惠券
- `/admin/coupons` 列表（名称、类型/规则摘要、发放方式、有效期、领取/总量、状态、操作：发布、上下线、复制链接、生成私有链接）+ 右上角「新增优惠券」→ `/admin/coupons/new`（现有表单原样搬入，业务行为不变）；保存后返回并刷新。
- 列表消除 N+1（一次查全部 links 再分组）；分享文案三语化（去掉写死的中文"减"）。

### 阶段 D：员工权限 + 设置
- **员工** `/admin/staff` 列表（账号｜角色｜归属门店（总部可见）｜Telegram ID｜状态｜最近登录｜操作）+ 右上角「新增员工」→ `/admin/staff/new`（登录名、初始密码强度提示 12+、角色、归属门店（仅总部可选）、Telegram ID）；编辑 `/admin/staff/:id`（角色、Telegram ID、**重置密码**、停用/启用）。自己不能停用/降级。
- 迁移：`staff.last_login_at`、`staff.must_change_password`（均有默认值，可回滚）；登录成功更新 `last_login_at`；`POST /api/v1/admin/staff/{id}/reset-password`（生成一次性临时密码，仅返回一次，置 `must_change_password`，写审计且不含明文）；首次登录强制改密页与接口。
- **设置** `/admin/settings` 概览：营业 / 收款 / 通知 / 区域 四张只读卡片（营业含接单开关、营业时间、起送、配送费、服务费、未付款超时/提醒、缺货响应超时、缺货退款复核阈值），各自「编辑」→ `/admin/settings/{business,payment,notice,region}`；营业时间的"新增时间段"在营业编辑页内。币种在收款页只读，变更需总部二次确认并写审计；通知页提供「发送测试消息」→ `POST /api/v1/admin/settings/test-notification`（返回成功或失败原因）。每个编辑页独立保存、未保存提示、审计记录前后值。
- 测试：重置密码与强制改密流程、跨店 404/403、币种变更审计、测试消息失败路径。

### 阶段 E：总览图表与用户统计
- 后端 `GET /api/v1/admin/dashboard?days=7|30&store=`：`kpis`（今日订单、今日销售额、待接单、待核款、待处理退款、**总用户数、今日新增用户、今日活跃用户**）、`series`（按日：订单数、销售额、新增用户、日活）、`status_breakdown`、`payment_breakdown`（ABA/钱包/混合）、`hourly`（24 小时订单数）、`top_products`（销量 TOP10，取订单明细快照）；一次请求、服务端聚合、门店隔离（总部可 `?store=`）、日期按门店时区、销售额不含取消。
- 前端 `/admin/overview`：指标卡（可点击跳转到对应列表）+ 自绘 SVG 图表：订单趋势、状态分布、支付占比、时段分布、热销 TOP10、用户趋势（新增 + 日活）。颜色之外要有文字或图案区分；深浅色与三语；每张图有表格式可访问替代；空数据/加载/错误三态；日活数据不足时显示"自 xx 日起统计"提示，而不是画 0 折线。
- 测试：已知数据集断言每个序列与口径（取消订单排除、门店时区跨零点、日活口径）、跨店隔离、无 N+1。

### 阶段 F：不在本任务内（先别做）
门店管理 CRUD、门店切换器真实接入、门店覆盖价；"待处理退款"管理页（ABA 人工完成、钱包审批的界面）另行安排。

---

## 6. 每个阶段的交付格式（必须）

1. 改动文件列表（含迁移、文案）。
2. 新增/修改的测试名称。
3. **实际执行的验证命令与结果摘要**：
   - 后端：迁移链在**空库**与**带数据的副本库**各跑一次；本阶段新增测试；`cross_store_security_test.py`、`daily_report_test.py`、`order_lifecycle_*` 保持通过。
   - 前端：`npx tsc -p tsconfig.app.json --noEmit`、`npm run build`、`npm test`、`npm run lint`（含 i18n 校验）全绿。
   - **浏览器**：桌面（1280）与窄屏（375）关键页面截图或验证记录；高棉语至少一页确认表头与按钮不溢出；涉及输入的页面必须实际键入验证。
4. 未验证项与原因（如离线无 Telegram、未跑真机）。
5. 需要我拍板的问题。
6. 不得声称"已完成"而无验证证据；失败用例如实列出，不得删除或放宽断言来通过。
7. 一个阶段一个 commit（提交信息写清范围）；**不推送、不部署**。

## 7. 明确不做

订单状态机/退款规则/钱包账务变更；顾客 Mini App 改版（除非我另行指示）；任何生产环境操作；引入图表库或大型 UI 框架；门店管理与真实门店切换（阶段 F）。

## 8. 开始

先完成第 0 节并输出简报，等我确认后开始阶段 B0。
