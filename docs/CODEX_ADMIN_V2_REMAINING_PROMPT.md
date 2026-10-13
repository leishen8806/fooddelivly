# Codex 一次性执行提示词：管理后台 v2 剩余阶段（C1 → C2 → D → E → G）

你是 Tea Cafe 的资深全栈工程师，仓库 `E:\TeaCafe`。阶段 A、B0、B1、B2 已完成并验收（当前 HEAD 应为 `6d094ae` 或更新）。**本次要一次性完成全部剩余阶段**：C1 商品+分类、C2 优惠券、D 员工+设置、E 总览图表、G 待处理退款页。中途不需要等我确认，但每个阶段必须过下面的"阶段门禁"才能进入下一阶段。

**规格与规则以这三份文档为准，开工前完整阅读：**
- `docs/CODEX_ADMIN_V2_FULL_PROMPT.md`：§1 不可违反的规则、§2 页面布局硬规则、§3 口径、§4 菜单与路由、§5 中 C1/C2/D/E 各阶段的详细规格、§6 交付格式。**这些在本次全部继续有效。**
- `docs/ADMIN_IA_V2.md`、`docs/ADMIN_IA.md` 附录 A/B/C。
- `docs/DEPLOYMENT_CHECKLIST.md` §10（已知缺口）。

---

## 0. 从前几个阶段学到的教训（必须遵守，都是真实发生过的缺陷）

1. **相对路径跳转会无限循环。** 阶段 B2 里 `<Navigate to="finance/daily">` 在嵌套路由下拼成 `/admin/finance/finance/daily`，落入兜底路由后无限追加，页面空白。**所有重定向、`navigate()`、`<Link>` 一律用绝对路径**（`/admin/...`）。每个新页面完成后自测三种访问：直接输入地址、刷新、不存在的子路径（如 `/admin/products/nope/extra`），不得出现循环或空白。
2. **不能只靠单元测试或模拟接口验证。** 前几轮"浏览器验证通过"用的是本地 Vite + 模拟接口，结果漏掉了真实后端下的缺陷。**每个阶段必须用真实后端 + 真实 PostgreSQL + 真实浏览器验证**（环境见 §2）。
3. **迁移页面不得丢功能。** 阶段 A 误删过区间财务面板，B1 丢过"取消订单""上传付款凭证"。迁出旧页面前，逐项列出 `git show 8f711c0:frontend/src/admin/AdminLegacyPage.tsx` 中该板块的所有控件与动作，新页面要一一对应，并在汇报里附"旧功能 → 新位置"对照表。
4. **数据库测试必须真的跑。** `RUN_DB_TESTS=1` 才会执行；汇报里"N skipped"视为未验证。每个阶段新增的后端测试必须是 HTTP 级（见 `backend/tests/order_lifecycle_integration_test.py` 的写法），并且**在本机实际执行通过**。
5. **操作失败不要清空页面**，错误要本地化并显示在页面顶部；失败后重新拉取最新数据，让界面与服务端一致（B1 中 409 之后状态还是旧的）。
6. **前端文案占位符用 `{{x}}`**（i18next）；写单括号不生效。所有新增文案三语同步，`npm run lint` 含 i18n 校验必须通过。确认框、按钮、标签都走 i18n，不得写死英文。
7. **前端与列表页布局硬规则**：列表页不得含新建/编辑表单（DOM 测试断言）；新建/编辑只在二级路由页。
8. 键盘输入类组件（`MoneyInput`、`LocalizedInput`）**必须在真实浏览器里实际键入验证**（价格输入 52、5.5、0.05；KHR 无小数）。

---

## 1. 阶段门禁（每个阶段结束前必须全部满足，才能 commit 并进入下一阶段）

| # | 门禁 | 怎么证明 |
|---|---|---|
| 1 | 前端 `npx tsc -p tsconfig.app.json --noEmit`、`npm run build`、`npm test`、`npm run lint`（含 i18n）全绿 | 贴命令结果 |
| 2 | 后端新增与既有测试在 PostgreSQL 15 上通过：本阶段新增测试、`order_lifecycle_integration_test.py`、`order_lifecycle_db_test.py`、`customer_daily_activity_test.py`、`cross_store_security_test.py`、`daily_report_test.py` | 贴命令与结果；`RUN_DB_TESTS=1`；已知环境项见 FULL 提示词 §0 |
| 3 | 迁移（若有）在空库与带数据的副本库各跑一次，并验证 downgrade → upgrade 往返 | 贴结果 |
| 4 | **真实浏览器验证**：桌面 1280 与窄屏 375 各一遍；用本地测试账号（经理、服务员各一）；直接访问/刷新/不存在子路径三种访问；新建→保存→回到列表并保留筛选；失败路径（例如重复提交、权限不足）页面保持并显示本地化错误；高棉语至少一页确认不溢出 | 贴关键截图路径或 DOM 检查记录 |
| 5 | 权限与隔离：STAFF 看不到经理页、门店经理看不到别店数据（跨店 404）、多店非总部时目录页只读 | HTTP 级用例 |
| 6 | "旧功能 → 新位置"对照表，无遗漏 | 贴表 |
| 7 | 一个阶段一个 commit，提交信息写清范围；**不推送、不部署** | `git log --oneline` |

任一门禁失败：先修，不要带病进入下一阶段。若被我必须决策的事项卡住（见 §4），在汇报里写明并**继续做不依赖该决策的部分**，不要整体停下。

---

## 2. 验证环境（本机已验证可行，照此做）

- Docker `postgres:15-alpine`（线上是 15）；Python **3.12** venv；Windows 需 `pip install tzdata`；`alembic upgrade head`。
- 后端：`uvicorn main:app --port 18000 --host 127.0.0.1`，环境变量 `JWT_SECRET`、`BOT_TOKEN`、`BOT_USERNAME`、`WEBHOOK_SECRET`、`COOKIE_SECURE=false`、`APP_ENV=development`、`ALLOWED_ORIGINS=http://127.0.0.1:3001`、`RATE_LIMIT_ENABLED=false`、`DAILY_REPORT_ENABLED=false`、`UPLOAD_DIR`。
- 前端：`VITE_API_URL=http://127.0.0.1:18000 npx vite --port 3001 --strictPort --host 127.0.0.1`。
- **注意**：`order_lifecycle_integration_test.py` 里的 HTTP 测试使用 `Origin: http://localhost:5173`，跑它们时 `ALLOWED_ORIGINS` 必须设为 `http://localhost:5173`，否则会被跨域校验拦成 403（这是环境配置，不是缺陷）；浏览器验证时用 `http://127.0.0.1:3001`。两套不要混用。
- 种子数据：可用 `backend/tests/seed_second_store.sql` + 自行插入 staff/customer/category/product/订单；测试账号口令自己生成，**不要写进仓库或回复**。
- 已知环境项：`cross_store_security_test.py` 的"主店读本店支付截图"依赖真实 Telegram 必挂；"二号店运维只覆盖二号店"把门店 id 写死为 2，共享库上会失败；手工插入固定 id 后要 `setval` 推进序列。
- 浏览器验证口令、Token 等不要出现在最终回复里。

---

## 3. 各阶段范围（细则见 FULL 提示词 §5，这里只列要点与本次追加项）

### C1 商品管理 + 分类管理
FULL §5 C1 全部。追加：商品/分类的**写权限**在多店且非总部时前端只读并提示"由总部维护"；单店或总部可写。`MoneyInput`/`LocalizedInput` 真实键入验证；商品二级页的"规格与加料""售卖时间"沿用 `AdminProductRules`，功能一项不能少（对照旧页）。

### C2 优惠券
FULL §5 C2 全部。追加：保存成功回列表并刷新；创建后链接/分享行为与旧页一致；`list_coupons` N+1 用 SQL 条数用例断言；分享文案三语化且金额格式化走币种小数位（不再写死中文、不用浮点 `/ 100:.2f`）。

### D 员工权限 + 设置
FULL §5 D 全部（迁移 `last_login_at`、`must_change_password`；重置密码；强制改密；设置概览 + 分组二级编辑页；币种只读与二次确认；测试消息接口）。追加：
- 设置概览与四个编辑页的所有字段与旧设置页**完全对应**，包含：接单开关、营业时间多段、起送、配送费、服务费、未付款超时、到期提醒、缺货响应超时、缺货退款复核阈值（金额用 `MoneyInput`）、ABA 二维码上传、支付链接、员工群 ID 与语言、时区、日报相关提示。
- 重置密码的临时密码**只展示一次**，不进日志/审计明文，页面离开后不可再查看；强制改密页必须真实走通一次（登录 → 被迫改密 → 进入后台）。

### E 总览图表与用户统计
FULL §5 E 全部（`/admin/dashboard` 接口、KPI、六类自绘 SVG 图表、可访问替代、三态、日活提示）。追加：图表数据与现有口径一致——销售额与财务页一致（不含取消）、日活按 B0 的采集口径；用已知数据集的测试断言数值；窄屏下图表可读。

### G 待处理退款页（部署清单缺口 #3）
新增 `/admin/refunds`（经理）：列表（订单号、金额、方式、状态、创建时间、关联订单链接）、筛选（待处理/已完成）、ABA 人工退款「完成」（必填转账号、二次确认）、钱包退款「批准」（超过阈值的）。接口已存在：`GET /api/v1/admin/order-refunds`、`POST /api/v1/admin/order-refunds/{id}/complete` 及批准接口（见 `routers/admin_orders.py`）。完成后财务统计页的「现金净流入」应相应减少、待处理退款下降，需有 HTTP 级用例与一次浏览器实操。菜单放在"财务"组。

---

## 4. 需要我提前拍板的事项（已给默认值，按默认执行）

- 实收金额、日活、日期时区：沿用 FULL §3 的默认。
- 员工"归属门店"：总部账号可选；门店经理固定为本店且不可见该字段。
- 设置里"新增门店"不在本次范围（依赖门店 CRUD，阶段 F）。

---

## 5. 最终汇报（全部阶段完成后一次性给我）

1. 提交列表（每阶段一个 commit）。
2. **验证矩阵**：每阶段 × 每项门禁（通过/未通过/未验证 + 证据）。"未验证"必须写明原因，不得含糊。
3. "旧功能 → 新位置"对照表（合并所有阶段）。
4. 新增迁移清单与回滚说明。
5. 已知缺陷与建议的后续工作（诚实列出，不要隐瞒）。
6. 本次没做的事项。

规则不变：**不推送、不部署、不碰线上环境**；不删除或放宽既有断言；失败如实报告。

## 6. 开始

先 `git status`（应干净）、`git log -5`，用不超过 30 行说明执行顺序与风险，然后直接开始 C1，**不必等待确认**。
