# Codex 执行提示词：管理后台重构（菜单 · 字段 · 控件 · 列表）

> **使用前提：** 先等你手上"其他问题"（`docs/ADMIN_FIX_PROMPT.md` 的 P1 缺陷、财务口径、日报时区、优惠券 N+1 等）全部修完、合入主干并通过测试后，再把本提示词交给 Codex。
> 规格文档：[`docs/ADMIN_IA.md`](ADMIN_IA.md)（页面、字段、控件、接口契约、验收标准）。**本提示词只规定怎么执行；"做什么"以 ADMIN_IA.md 为准，冲突时先停下来问。**

---

你是 Tea Cafe 的资深全栈工程师，仓库 `E:\TeaCafe`（FastAPI + SQLAlchemy async + PostgreSQL；React + Vite + TypeScript；i18n 来自 `docs/ui/locales.json`，三语 zh-CN / en / km）。任务：按 `docs/ADMIN_IA.md` 重构管理后台。

## 0. 开工前（必须先做，输出简报后再改代码）

1. `git status` / `git log -5`：确认工作区干净、前置修复已合入；若有未提交改动，停下来问。
2. 阅读：`docs/ADMIN_IA.md`（全文）、`docs/MULTI_MERCHANT_PLAN.md`、`docs/FINANCE_METRICS.md`、`backend/store_context.py`、`backend/dependencies.py`、`frontend/src/App.tsx` 的 `AdminPage` 及 `components/Admin*.tsx`。
3. 简报（≤40 行）：现有后台接口清单、与 ADMIN_IA 的差距、你打算的 PR 切分、风险点。
4. 验证环境（本机已验证可行）：Docker 起 `postgres:16-alpine`；**Python 3.12 venv**（3.15 装不上 pydantic-core）；Windows 需 `pip install tzdata`；`alembic upgrade head`；种子见 `backend/tests/seed_second_store.sql` 并另插 staff 1(MANAGER, MAIN)、customer 1、category 1、product 1；端口 8000 常被占，用 18000；测试需导出 `JWT_SECRET`、`WEBHOOK_SECRET`。"主店读本店支付截图"一条依赖真实 Telegram，离线必挂，属已知环境项；"新建员工 409"在全新库首跑会因固定 id 序列未前进而出现，二次运行消失（测试夹具问题，可顺手修：种子后 `SELECT setval(pg_get_serial_sequence('staff','id'), (SELECT max(id) FROM staff))`）。

## 1. 不可违反的规则

**安全与隔离（P0 已落地，勿回退）**
- 语义：`staff.store_id IS NULL` = 总部账号。员工、审计表必须用 `strict_store_clause` / `can_manage_store_row`（NULL 对门店账号不可见）；订单、客户等历史数据沿用 `store_scope_clause` / `can_access_store`（NULL 可见）。
- 共享目录（菜品/分类/规格组）写权限用 `get_catalog_manager`（总部，或仅一家活跃门店时放行）。**门店经理的改价/沽清走"门店覆盖"，不得写共享目录。**
- 目录类审计 `AuditLog.store_id = NULL` 是有意设计（总部级），不要改成操作者门店。
- 每个新写接口：角色校验在路由层、门店隔离、事务、`AuditLog`(带正确 `store_id`)、跨店返回 **404**（不泄露存在性）、无权限 403。
- 搜索/排序参数：排序字段白名单，搜索一律参数化（`ILIKE :q` + 转义 `%` `_`），禁止字符串拼 SQL。
- CSV 导出：防公式注入（`= + - @` 开头加 `'`）、同样做隔离与限流、单次 ≤10,000 行。
- 不在日志/审计中写入密码、临时密码、完整 Token、支付凭证内容。

**业务**
- 金额一律最小单位整数；**前端禁止浮点乘除金额**，用字符串/整数运算（见 `MoneyInput`）。
- 不静默改变业务规则；订单状态机、付款流程、钱包逻辑本任务**不改**。
- 旧接口保持兼容（新能力走新路径或新增可选字段）；删除旧接口需单独 PR 并先确认无调用方。

**i18n**
- 所有新增文案同步写入 `docs/ui/locales.json` 三语，key 集合必须一致；界面不得出现裸 key 或混杂语言；高棉语需检查溢出与断行。

**工程**
- 不引入大型 UI 框架；表格/抽屉等用自写轻量组件（项目现有风格：`index.css` 变量与类名）。新增依赖须说明理由与版本，优先零依赖。
- 新代码有类型，禁止 `any`（除第三方类型缺失且加注释）。
- 每个 PR 小而可审：一个 PR 只做一个阶段，附验证记录。

## 2. 分阶段交付（顺序固定，每阶段结束停下汇报，等我确认再继续）

### 阶段 A：骨架与通用组件（不改业务行为）
- 新建 `frontend/src/admin/`：`AdminApp`（嵌套路由 `/admin/*`，守卫）、`layout/AdminLayout`（分组侧栏 + 顶栏，门店切换器仅总部显示）、`layout/nav.ts`（`{key,path,labelKey,roles,hqOnly}`）、`hooks/useQueryState`（筛选↔URL）。
- 通用组件：`DataTable`、`Toolbar`、`Drawer`、`ConfirmDialog`（原因必填校验）、`MoneyInput`、`LocalizedInput`、`StatusBadge`、`EmptyState`；规格见 ADMIN_IA 附录 B。
- 把现有 10 个板块**原样迁移**为路由页（先不改内容），`App.tsx` 里的 `AdminPage` 删除。合并重复的财务面板（日报在上、区间统计在下），补显示配送费、服务费，保证 `小计 − 优惠 + 配送费 + 服务费 = 销售额`。
- 新增 `scripts/check-i18n`（三语 key 一致 + 代码引用的 key 均存在）并接入 `npm run lint`；引入 vitest，给 `MoneyInput`（含 0 位小数币种、四舍五入边界、粘贴非法字符）、`LocalizedInput`、`DataTable` 三态写单测。
- 验收：`tsc --noEmit`、lint、vitest 全绿；手工走一遍旧功能无回归（订单状态流转、核款、商品编辑、设置保存）。

### 阶段 B：订单工作台（ADMIN_IA 5.2 + 附录 A.2）
- 后端：`orders/search`、`orders/counts`、`orders/{id}` 详情、`orders/export.csv`；N+1 防护（selectinload / 批量查询），25 行分页 SQL 语句数不随行数增长。
- 前端：Tab 计数、Toolbar 筛选、DataTable、行内主操作按钮、⋯ 菜单、五段式详情抽屉（含时间线）、手机端卡片布局、30 秒轮询刷新 + 新单提示。
- 测试：筛选/搜索/分页组合；跨店 404；STAFF 权限；CSV 注入；SQL 注入样例（`' OR 1=1 --`、`%`、`_`）；语句数断言。

### 阶段 C：钱包、客户、员工、设置、审计
- 客户 `customers/search` + 详情抽屉（订单历史、钱包流水、调账入口，调账仅经理且必填原因、二次确认）；钱包页 Tab 与详情抽屉。
- 员工：抽屉式新建/编辑；`reset-password`（临时密码仅返回一次，`must_change_password` 首登强制改密）；`last_login_at`；自己不可停用。含迁移（新增列均有默认值，可回滚）。
- 设置：四个页签独立保存、未保存提示、币种只读（变更需总部 + 二次确认 + 审计）、`test-notification`。
- 审计：筛选参数与行展开前后值；仍用 `strict_store_clause`。
- 测试：沿用并扩充 `backend/tests/cross_store_security_test.py`；新增密码重置、强制改密流程用例。

### 阶段 D：菜单管理、门店覆盖、沽清、门店管理（ADMIN_IA 5.5/5.6/5.9 + A.4/A.5）
- **先写设计小结并等我确认**：门店经理可覆盖的字段（默认：价格、上下架、排序、售卖时间）、总部账号如何创建（建议 `create_manager.py --hq`）。
- 后端：`admin/menu`、覆盖 PUT/DELETE、沽清 `availability`（门店经理作用于本店覆盖）、批量排序、`stores` CRUD 与入口深链/二维码。
- **关键一致性**：覆盖解析必须复用 `store_context.effective_product()`，菜单展示与下单计价共用同一函数；加测试：二号店覆盖价后，菜单价 = 下单价 = 订单快照价。
- 前端：分类栏 + 商品表 + 编辑抽屉（基本/规格与加料/售卖时间/门店覆盖/历史）；多店时共享字段只读并提示"由总部维护"；沽清面板；门店管理页。

### 阶段 E：工作台、优惠券页升级
- 工作台 KPI 与待办队列（超时阈值：先用门店设置里的新字段 `order_overdue_minutes`，默认 30）。
- 优惠券：新增"已使用数"、作废、复制为新券、分步创建与试算预览；消除 `list_coupons` 的 N+1；分享文案三语化。

## 2.5 必须纳入的已知缺口（浏览器实测发现，详见 `docs/DEPLOYMENT_CHECKLIST.md` §10）

这些是上一轮订单生命周期功能留下的后台界面缺口，**在重构对应页面时一并解决，不要照搬旧页面的缺陷**：

- **阶段 B（订单工作台）**
  1. 「报缺货」按钮的出现条件必须与后端一致：`NEW` 且未付款，或 `ACCEPTED/PREPARING` 且已付款（已付款订单只能删品，不能换品）。已有未处理缺货的商品不再显示该按钮。
  2. 订单行与抽屉显示缺货状态（等待顾客选择 / 已处理及处理结果）；有 `OPEN` 缺货时禁用「接单/确认支付/出餐」并显示原因（对应后端 409），不要让员工点了才报错。
  3. 订单抽屉的金额明细要展示已删品减少额（`removed_minor`）与已退款（`order_refunds`），并保证 `小计 − 优惠 + 配送费 + 服务费 = 订单总额`。
- **阶段 C（钱包/财务相关）**
  4. 新增「待处理退款」页面（经理）：列表（订单号、金额、方式、状态、创建时间）、筛选（待处理/已完成）、ABA 人工退款「完成」操作（必填转账号，二次确认）、钱包退款「批准」操作（超过阈值的）。接口已存在：`GET /api/v1/admin/order-refunds`、`POST .../{id}/complete`、批准接口（见 `routers/admin_orders.py`）。完成后财务页的「现金净流入」应相应减少，加一条前端或接口测试覆盖。
  5. 财务页：警示类卡片（待处理退款、对账差额）使用明显的警示色（红/橙，不只靠颜色，带图标或文字）；数值为 0 时不显示负号（现在会出现 `-$0.00`）；合并「日报」与「区间统计」两个面板。
- **顾客 Mini App（不在后台范围，但请一并修，改动很小）**
  6. 订单弹窗：已确认收款的订单不要显示「ABA 待支付」（改显示已收/已退）；缺货块里的「取消订单」与底部重复，只保留一个；有未处理缺货时，「已付款，发送凭证」旁提示先处理缺货。
  7. 订单列表加「待处理缺货」徽标，让顾客不依赖 Bot 消息也能看到。

## 3. 每个阶段的交付格式（必须）

1. 改动文件列表（含迁移）。
2. 新增/修改的测试名称。
3. **实际执行过的验证命令与完整结果摘要**（后端：迁移链、`cross_store_security_test.py`、`daily_report_test.py`、新增用例；前端：`tsc`、lint、vitest、i18n 校验；UI：至少桌面 + 窄屏截图或 Playwright 结果）。
4. 未验证项与原因（如离线无 Telegram、未跑高棉语真机）。
5. 需要我拍板的问题。
6. 不得声称"已完成"而无验证证据；失败用例要如实列出，不得删除或放宽断言来通过。

## 4. 明确不做（本任务范围外）

结算/抽成/门店间往来（P4）；订单状态机或退款规则变更；顾客端 Mini App 改版；自动化财务对账；骑手/配送轨迹；任何生产环境操作（部署、改线上库）——这些需要我单独授权。

## 5. 开始

先完成第 0 节并输出简报，等我确认后开始阶段 A。
