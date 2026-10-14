# 上线部署步骤与检查清单（订单生命周期 + 退款财务 + 门店隔离加固 + 后台 v2）

> 适用范围：`7794a65`（门店隔离加固）、`245648d`/`3dc0ace`（订单生命周期、退款财务）、阶段 A–G 的后台 v2（路由化、订单工作台、财务拆分、商品/分类/优惠券/员工/设置二级页、总览图表、待处理退款页）及其后的修复提交。**以最终要上线的提交为准重新核对本清单。**
> 部署架构：Docker Compose（`db` PostgreSQL 15 + `backend` + `frontend`/nginx），后端容器**启动时自动执行 `alembic upgrade head`**（见 `backend/Dockerfile`），所以「发布后端镜像 = 执行迁移」。
> 本文只是清单，**不会替你操作线上环境**；每个破坏性步骤都需要负责人确认。

## 0. 本次发布包含什么（评审用）

| 类别 | 变化 | 风险 |
|---|---|---|
| 迁移 | `l0123456789e` 审计日志 `store_id` 回填；`m` 订单问题表 + 两列超时配置；`n` 订单收款快照 + 退款表（含按审核记录回填 `paid_confirmed_minor`）；`o` 缺货升级/审批控制列 | 全部为加列/建表/回填，无删列；回填是单条 UPDATE |
| 权限 | 员工/审计严格按门店；目录写权限收紧为总部（仅一家活跃门店时放行）；钱包调账路由层 403 | 单店部署无感知；**开第二家店前必须有总部账号**（见 §6） |
| 迁移（后台 v2） | `p0123456789i` 新建 `customer_daily_activity`（日活，按门店时区的日期去重）并**用历史订单回填**；`q0123456789j` 给 `staff` 加 `last_login_at`、`must_change_password`（默认 false） | 回填是一条 INSERT…SELECT，订单量大时先在副本库估时；两者均可 downgrade |
| 业务 | 顾客自助取消、取消退券、缺货流程、已付款删品退差价、ABA 人工退款记录 | 新功能，默认不影响既有流程 |
| 后台 v2 | 菜单分组与路由全部改变（见 §12）；新增总览图表、日活、待处理退款页、员工重置密码与强制改密、设置分组编辑、测试消息、币种变更二次确认 | **员工与店长的使用习惯会变**，上线前要通知；旧书签 `/admin/finance` 会自动跳转 |
| 定时 | 进程内每 60 秒执行超时取消/提醒/缺货升级；**阈值默认 0 = 关闭** | 部署后不会取消任何订单 |
| 财务 | 日报、统计新增退款/待处理/对账字段；现金净流入扣除已转账 ABA 退款 | 新字段，旧字段语义不变 |

## 1. 发布前（T-1 天）

- [ ] 确认目标提交：`git log --oneline origin/main | head` 与要上线的 commit 一致；CI/本地测试已跑（见 §9）。
- [ ] 确认 VPS 当前运行版本与迁移版本：`docker compose exec backend alembic current`（期望为 `k0123456789d`，若不是先弄清原因再继续）。**本次新增 6 个迁移：`l → m → n → o → p → q`（依次接在 `k0123456789d` 之后）。**
- [ ] 检查磁盘：`df -h`；数据库卷与上传卷（`postgres_data`、`uploaded_images`）所在分区剩余空间 > 当前数据库大小的 3 倍。
- [ ] 检查 `.env` 无新增必填项：本次**没有新增必填环境变量**。保持 `COOKIE_SECURE=true`、`APP_ENV=production`、`ALLOWED_ORIGINS` 与线上域名一致。
- [ ] 选择低峰窗口（避开营业高峰和 22:35 日报投递），通知店员：发布期间群里按钮可能短暂无响应。
- [ ] 确认有人能在 Telegram 里实机验证（需要真实 Bot、员工群、一个测试顾客账号）。

## 2. 备份（必做，T-0）

```bash
# 在 VPS 项目目录执行；DB_USER/DB_NAME 取自 .env（默认 teacafe）
mkdir -p ~/backups && cd ~/backups
docker compose -f /path/to/TeaCafe/docker-compose.yml exec -T db \
  pg_dump -U teacafe -d teacafe -Fc > teacafe_$(date +%Y%m%d_%H%M).dump
ls -lh teacafe_*.dump          # 文件大小应远大于 0

# 上传文件卷（付款凭证、商品图）
docker run --rm -v teacafe_uploaded_images:/data -v "$PWD":/backup alpine \
  tar czf /backup/uploads_$(date +%Y%m%d_%H%M).tgz -C /data .
```
- [ ] 备份文件已**拷贝到 VPS 之外**（本机或对象存储）。
- [ ] 在一台临时机器/容器上**试恢复一次**（`pg_restore --list` 至少能列出对象；条件允许时完整恢复到临时库）。
- [ ] 记录当前镜像 ID，便于回滚：`docker compose images`。

## 3. 部署

```bash
cd /path/to/TeaCafe
git fetch origin && git status                # 工作区应干净
git checkout main && git pull --ff-only
docker compose build backend frontend
docker compose up -d                          # 后端启动时自动 alembic upgrade head
docker compose logs -f backend | head -80     # 观察迁移日志，直到出现 Application startup complete
```
- [ ] 迁移日志依次出现 `l0123456789e → m → n → o0123456789h → p0123456789i → q0123456789j`，**无报错**；`p` 的回填耗时在日志里能看出，若超过预期不要中断，等待完成。
- [ ] `docker compose ps`：三个服务均 `healthy`/`running`。
- [ ] `docker compose exec backend alembic current` = `q0123456789j (head)`。

> 若迁移失败：后端容器会反复重启。**不要**手动改库；先看日志，必要时按 §8 回滚。

## 4. 发布后即时检查（5 分钟内）

- [ ] `curl -s https://food.workline.ink/health` → `{"status":"ok",...}`。
- [ ] `https://food.workline.ink/api/v1/menu` 返回菜单；`/admin` 能登录。
- [ ] 数据库抽查：

```sql
-- 自动化默认关闭：三项都应为 0
SELECT code, unpaid_order_timeout_minutes, unpaid_order_reminder_minutes,
       shortage_customer_response_timeout_minutes, shortage_refund_review_minor
  FROM stores;

-- 审计回填：订单/员工/钱包类审计不应再有大量 NULL（商品、分类、全局设置类 NULL 属正常）
SELECT entity_type, count(*) FILTER (WHERE store_id IS NULL) AS null_store, count(*) AS total
  FROM audit_logs GROUP BY entity_type ORDER BY 1;

-- 收款快照：已确认收款的订单应有 paid_confirmed_minor
SELECT count(*) FROM orders
 WHERE payment_status IN ('PAID_CONFIRMED','PARTIALLY_REFUNDED','REFUNDED') AND paid_confirmed_minor = 0;

-- 日活回填：行数应等于「每个客户 × 门店本地日期」去重后的订单天数；上线后每次有人打开 Mini App 会新增/更新当天一行
SELECT count(*) AS activity_rows, count(DISTINCT customer_id) AS customers, min(activity_date), max(activity_date)
  FROM customer_daily_activity;

-- 员工安全字段：默认都不应处于「必须改密」
SELECT count(*) FILTER (WHERE must_change_password) AS must_change, count(*) AS total FROM staff;

-- 活跃门店数（决定目录写权限规则，见 §6）
SELECT count(*) FROM stores WHERE status = 'ACTIVE';
```
- [ ] 后端日志无 `ERROR`；搜索 `unpaid order expiry failed`、`cancellation notification failed` 应为空。

## 5. 真实 Telegram 联调（本次**自动化测试全部 mock 了 Telegram，必须实机验证**）

用测试顾客账号 + 员工群逐项过：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 顾客下单后自助取消（未付款） | 顾客收到取消通知；群里订单卡变为已取消、无按钮；使用过的优惠券回到「可用」 |
| 2 | 员工对未付款订单「报缺货」→ 顾客选「去掉商品」 | 顾客收到缺货通知；金额重算；群卡片更新并出现处理结果消息 |
| 3 | 已付款订单：员工报缺货 → 顾客删品 | 钱包部分立即退回；ABA 差额生成「待人工退款」；日报出现待处理退款行 |
| 4 | 经理在后台/接口完成 ABA 人工退款（填转账号） | 记录变 DONE；财务页现金净流入相应减少 |
| 5 | 有未处理缺货时，员工点「接单/确认支付/出餐」 | 被拒绝并有本地化提示，群按钮同样被拒 |
| 6 | （按需，见 §7）把未付款超时设为 3 分钟，下单后不付款 | 到期前提醒一次；到期后自动取消并通知顾客与群 |
| 7 | 22:35 日报 | 含退款相关行（有数据时）；三语不出现裸 key |
| 8 | 在 Telegram 打开 Mini App（用测试顾客） | `customer_daily_activity` 当天新增一行；后台总览「今日活跃用户」+1；同一天再次打开不新增行 |
| 9 | 设置 → 通知页点「发送测试消息」 | 员工群**真的收到消息**；把群 ID 临时改错再点，页面显示明确失败原因（不是"成功"） |
| 10 | 经理在员工页对一名测试员工「重置密码」 | 临时密码只显示一次；该员工用临时密码登录 → 被带到改密页；改密前访问任何后台接口都被拒绝；改密后恢复正常；审计里有记录且**没有密码明文** |
| 11 | 设置 → 收款页修改币种 | 门店经理不能改；总部必须二次确认；审计里有前后值。**不要在线上真实门店实际改币种，用测试门店验证** |
| 12 | 总览页 | 今日订单/销售额与「每日报表」「财务统计」同口径；待接单与订单页「新订单」数量一致 |

## 6. 多门店前置条件（开第二家店之前）

- [ ] **先创建总部账号**（`staff.store_id IS NULL` 的 MANAGER）。目前 `create_manager.py` 只在没有任何经理时可用，且新建的是总部账号；已有经理时需要用库内操作或后续提供的 `--hq` 选项。**没有总部账号就开第二家店，会导致任何人都无法修改共享菜单。**
- [ ] 门店经理改价/沽清在多店模式下 403，需等「门店覆盖」功能（见后台重构提示词阶段 D）。
- [ ] 验证：`python backend/tests/cross_store_security_test.py <url>`（需种子数据）。

## 7. 逐步开启新自动化（建议在稳定运行 1–2 天后，由店长在「设置」里改）

| 配置 | 建议起始值 | 说明 |
|---|---|---|
| 未付款订单超时（分钟） | 30 | 先在一家门店开；确认通知发出后再推广 |
| 到期前提醒（分钟） | 5 | 必须小于超时 |
| 缺货顾客响应超时（分钟） | 20 | 半程提醒顾客，满额升级到员工群 |
| 缺货退款经理复核阈值（金额） | 5.00（按币种） | 0 = 不限制；超过的钱包退款需经理批准 |

开启后第一个 24 小时：每天查看「待处理退款」卡片与日报，**经理每日清零**；对账差额应为 0，非 0 要排查。

## 8. 回滚

**优先回滚代码，不回滚数据库。** 本次迁移全部为加列/建表，旧版本代码对新列无感知（新增列均有默认值）：
```bash
git checkout <上一个稳定提交> && docker compose build backend frontend && docker compose up -d
```
> 旧代码启动时会执行 `alembic upgrade head`，因库已在更新版本，Alembic 会报「找不到版本」而无法启动。回滚代码前需临时把 Dockerfile 的启动命令去掉 `alembic upgrade head`（或用 `alembic stamp` 谨慎处理），**这一步务必在演练环境先试过**。

> 迁移 `p`（日活表）、`q`（员工两列）均可 `alembic downgrade` 回退，但回退会丢失日活数据与「必须改密」标记；`m`/`n`/`o` 的回退同样只删表/删列。**回滚代码前先备份。**

只有在数据损坏等极端情况才恢复数据库：
```bash
docker compose stop backend
docker compose exec -T db dropdb -U teacafe teacafe && docker compose exec -T db createdb -U teacafe teacafe
docker compose exec -T db pg_restore -U teacafe -d teacafe < teacafe_YYYYMMDD_HHMM.dump
docker compose start backend
```
> 恢复会丢失备份之后的所有订单与资金流水，必须由负责人决定，并与钱包账务对账后再放开服务。

## 9. 发布前质量门禁

- [ ] 后端（必须用 PostgreSQL 15，设 `RUN_DB_TESTS=1`；HTTP 级测试要求 `ALLOWED_ORIGINS=http://localhost:5173`）：`order_lifecycle_integration_test.py`、`order_lifecycle_db_test.py`、`customer_daily_activity_test.py`、`admin_v2_remaining_test.py`、`coupon_admin_test.py`、`admin_fix_patch_test.py`、`daily_report_test.py`、`cross_store_security_test.py`（已知 2 条环境项：依赖真实 Telegram 的支付截图用例、二号店 id 写死）。汇报里出现「skipped」视为未验证。
- [ ] 前端：`npx tsc -p tsconfig.app.json --noEmit`、`npm run build`、`npm test`、`npm run lint`（含三语 key 校验与引用 key 存在性扫描）。
- [ ] **真实浏览器走查**（用测试账号、真实后端）：直接访问/刷新/不存在子路径；经理与服务员各一遍；桌面 1280 与窄屏 375；高棉语至少一页。
- [ ] 迁移链在空库与「带数据的副本库」各跑一次（用 §2 的备份恢复出副本）。
- [ ] 上线前处理或明确接受下列**已知界面缺口**（见 §10）。

## 10. 已知缺口（浏览器实测记录；上线前按状态决定是否接受）

| # | 位置 | 问题 | 状态 |
|---|---|---|---|
| 1 | 后台订单页 | 已付款备餐中订单缺「报缺货」；不显示缺货状态；有未处理缺货时接单仍可点 | ✅ 已修（订单工作台 B1 及修复） |
| 2 | 后台 | 没有「待处理退款」页面 | ✅ 已修（`/admin/refunds`） |
| 3 | 后台设置 | 营业时间编辑丢失 | ✅ 已修 |
| 4 | 员工安全 | 强制改密仅前端拦截；测试消息假成功；币种变更无确认 | ✅ 已修（后端 403、真实发送、总部二次确认） |
| 5 | 顾客订单弹窗 | 已确认收款的订单仍显示「ABA 待支付」；缺货块「取消订单」重复；缺货未处理时仍可点「已付款，发送凭证」 | ❌ 未修（顾客端，影响理解不影响数据） |
| 6 | 顾客订单列表 | 看不出哪些订单在等待自己选择缺货处理 | ❌ 未修 |
| 7 | 后台总览 | 「订单状态」「支付方式」「热销商品」是文字列表而非图表；订单趋势只有订单数 | ✅ 已修（6 张 SVG 图表，订单趋势含销售额） |
| 8 | 后台退款页 | 「方式」列表头写成「支付状态」 | ✅ 已修（改为「退款方式」） |
| 9 | 后台审计页 | 状态变化列显示原始枚举（如 `PAID_CONFIRMED`） | ❌ 旧行为，未处理 |
| 10 | 测试 | 日活并发用例用 100 路并发，撞 Postgres 默认 100 连接上限 | ✅ 已修（30 路 + 连接池 5，连跑 5 次通过） |
| 11 | 多门店 | 无总部账号创建入口；门店覆盖价、门店管理、真实门店切换未做 | ❌ 阶段 F，未做；**开第二家店前必须解决** |
| 12 | 会话 | 7 天 JWT 无法在改密/停用时撤销 | ❌ 未修 |
| 13 | 后台权限 | 服务员能直接输入地址进入经理页面；读设置能看到员工群 ID 和收款链接 | ✅ 已修（路由级角色守卫；接口对服务员隐藏敏感字段） |
| 14 | 手机端 | 375 宽度下订单详情、商品新增、总览、财务统计横向溢出 | ✅ 已修（含网格列 `minmax(0,1fr)`）。**注意**：`npm run check:admin-overflow` 用空数据时检查不出订单详情的溢出，上线前仍需用真实数据在 375 下抽查 |

> 5、6 只影响顾客理解，7–10 是体验和测试稳定性，都不影响数据安全，可先上线并保持新自动化关闭，按 §7 逐步开启。**11 是多店上线的硬前置条件。**

## 11. 监控与值守（上线后一周）

- 日志关键字：`unpaid order expiry failed`、`cancellation notification failed`、`shortage notification failed`、`限流命中`、`ERROR`。
- 每日检查：待处理退款笔数与金额、对账差额、日报是否按时投递（`report_deliveries`）、数据库大小。
- 异常处理：Telegram 通知失败**不会**回滚订单取消/退款（设计如此），需要人工在群里补通知；退款对账差额非 0 时先看 `order_refunds` 与 `payment_reviews` 是否有缺记录。
- 限流计数在进程内存中，多副本部署时各算各的；当前单副本无影响。

## 12. 后台地址变化与书签（通知店员）

| 旧入口 | 新地址 |
|---|---|
| 概览 | `/admin/overview`（含图表） |
| 订单 | `/admin/orders`（列表，点订单号进 `/admin/orders/<订单号>` 详情） |
| 商品（含分类、规格） | `/admin/products`（商品）、`/admin/categories`（分类），新增/编辑在二级页 `…/new`、`…/<id>` |
| 优惠券 | `/admin/coupons`，新增在 `/admin/coupons/new` |
| 财务 | `/admin/finance` 自动跳转到 `/admin/finance/daily`（每日报表）；区间统计在 `/admin/finance/stats` |
| 员工 | `/admin/staff`，新增在 `/admin/staff/new`；重置密码在员工编辑页 |
| 设置 | `/admin/settings` 为概览，分组编辑在 `/admin/settings/{business,payment,notice,region}` |
| （新）待处理退款 | `/admin/refunds` |

- 前端 nginx 已配置 SPA 回退（`try_files … /index.html`），深链接刷新不会 404。
- **列表页不再有新建表单**：新建一律点页面右上角的「新增」进入二级页。通知店员这一变化。
- 不存在的后台地址会跳转到总览（已修复一次无限重定向缺陷，上线后用 `/admin/does-not-exist` 抽查）。
