# Tea Cafe 管理后台：问题清单 + 修复方案（可直接交给开发 Agent 的提示词）

你是 Tea Cafe 的资深全栈工程师。仓库：E:\TeaCafe（FastAPI + SQLAlchemy async + PostgreSQL；React + Vite 前端；三语 i18n 来自 docs/ui/locales.json）。
先读 docs/MULTI_MERCHANT_PLAN.md、docs/DEVELOPMENT-HANDOFF.md，以及 backend/store_context.py、backend/dependencies.py。

## 规则
- 不静默改变业务规则；金额一律整数最小单位；新增文案必须三语同步，key 不得外露。
- 每个阶段：先写失败用例 → 再修 → 跑测试 → 汇报「已验证 / 未验证」。不要只说"已完成"。
- 门店隔离语义（已锁定）：`staff.store_id IS NULL` = 总部账号；员工、审计日志必须用 `strict_store_clause` / `can_manage_store_row`（NULL 不对门店账号可见）；订单、客户等历史数据沿用 `store_scope_clause`（NULL 可见）。
- 验证环境：Docker 起 postgres:16，用 Python 3.12 venv（3.15 装不上 pydantic-core），Windows 需 `pip install tzdata`；`alembic upgrade head` → 灌种子（见 backend/tests/seed_second_store.sql，另需 staff 1、customer 1、category 1、product 1 属于 MAIN 店）→ `JWT_SECRET=... WEBHOOK_SECRET=... python backend/tests/cross_store_security_test.py <url>`。注意端口 8000 可能被占用。"主店读本店支付截图" 一条依赖真实 Telegram，离线必挂，属已知环境问题。

## 已完成（P0，未提交，勿重做）
员工列表/改动严格按门店（门店经理看不到、改不了总部账号）；所有 AuditLog 写入带 store_id，审计查询严格过滤，迁移 l0123456789e 回填；优惠券写操作补审计；菜品/分类/售卖时间/规格组写权限改为 `get_catalog_manager`（总部，或仅一家活跃门店时放行）；cross_store_security_test 新增 15 条。

## 待修问题与方案（按顺序）

### P1 后台骨架 + 已知缺陷（先做）
1. `frontend/src/App.tsx` 的 `AdminPage` 约 1000 行、10 个板块塞一个组件，大量 `any`。方案：react-router 嵌套路由 `/admin/{overview,orders,recharges,customers,products,coupons,finance,audit,staff,settings}`，抽 AdminLayout（按 role 生成导航）+ 每板块独立组件（放 `frontend/src/admin/`）；定义 Order/Stats 等类型替换 `any`；总部账号加门店切换器（`?store=CODE`，settings 接口已支持）。
2. 财务 tab 同时渲染 `AdminDailyReport` 与旧财务面板（App.tsx 约 991、1014 行）。方案：确认后合并为一个页面（日报在上、区间统计在下）。
3. 财务对账口径不闭合：日报/财务/订单行只展示 小计、优惠，缺配送费、服务费，`小计−优惠≠销售额`；且 `subtotal` 为空时回退 `total_minor`（已含运费）。方案：补 `order_sales_delivery_fee_minor`、`order_sales_service_fee_minor` 并展示，回退口径改为 `total − delivery − service + discount` 或标注"历史订单无小计"；加后端单测。
4. `admin_reports._parse_date` 默认用服务器本地日期，应改为按调用者门店时区；`admin_stats` 对总部（跨店）用主店时区切日，需让总部按所选门店取时区或明确标注。
5. `admin_wallet adjust` 路由用 `get_current_staff`，只靠 DB 函数校验经理，应在路由层先 403（纵深防御）。
6. 前端无测试：加 vitest + 一个"三语 key 一致 + 代码里 t('...') 引用的 key 都存在"的校验脚本，并接入 CI。

### P2 门店与菜单（需先向用户确认）
先确认：门店经理可覆盖哪些字段（建议 价格/上下架/排序/售卖时间）；总部账号如何创建（目前只能改库，建议 create_manager.py 加 `--hq`）。
- 总部：门店 CRUD（含 merchant、佣金、结算周期、二维码深链 `?start=store_{code}`），门店状态 ACTIVE/SUSPENDED。
- 门店：`store_product_overrides` 管理 API + 页面；`effective_product()` 必须与下单计价共用（已有，勿分叉）。
- 分类/菜品新建时的 `store_id` 语义写进文档（现在目录全店共用，`store_id` 仅作来源标记）。
- 测试：二号店改价后菜单价 = 下单价；门店经理不能改别店覆盖。

### P3 订单工作台
`GET /admin/orders` 现在全量加载（含明细）、`GET /admin/customers` 全量返回，且只有前端状态过滤。方案：服务端分页（limit/offset 或 keyset）、搜索（public_code/房间号/客户名/Telegram）、日期范围、状态/支付状态筛选；订单详情抽屉含 OrderEvent 时间线；CSV 导出（受门店隔离）；新单轮询刷新（30s）+ 提示。客户列表同样分页搜索，并消除 count 的全表 join 性能问题。

### P4 财务与结算（涉及真钱，严格按 MULTI_MERCHANT_PLAN 第 3 节）
商家账本、结算单 `settlement_statements`（DRAFT→加盟商确认）、门店间内部往来（钱包跨店通用）、对账函数；新增只读财务角色 `HQ_FINANCE`（所有写接口拒绝）；双写校验后再切流量。先出设计评审再编码。

### P5 营销与客户
优惠券：编辑（仅 DRAFT）、作废/提前结束、核销与领取统计、`list_coupons` 消除 N+1（一次查全部 links 后分组）、分享文案三语化（现在写死中文"减"）。客户详情页：钱包流水、调账入口（仅经理，必填原因）。

### P6 会话与质量
- JWT 7 天且无法撤销：给 staff 加 `token_version`（或 password_changed_at），改密码/改角色/停用时递增，`get_current_staff` 校验。
- 登录对不存在账号跳过 bcrypt，响应时间可区分：不存在时也做一次 dummy hash 校验。
- 限流为进程内存计数：文档标注多副本局限，或迁移到网关/Redis。
- Playwright 覆盖：登录、接单→确认支付→出餐、三语切换（含高棉语换行）、门店经理看不到别店。

## 交付格式
每阶段给出：改动文件列表、新增/修改的测试、实际执行的验证命令与结果、未验证项、需要用户拍板的问题。
