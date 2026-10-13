# 上线部署步骤与检查清单（订单生命周期 + 退款财务 + 门店隔离加固）

> 适用范围：`7794a65`（门店隔离加固）、`245648d`/`3dc0ace`（订单生命周期、退款财务）及随后的修复提交。
> 部署架构：Docker Compose（`db` PostgreSQL 15 + `backend` + `frontend`/nginx），后端容器**启动时自动执行 `alembic upgrade head`**（见 `backend/Dockerfile`），所以「发布后端镜像 = 执行迁移」。
> 本文只是清单，**不会替你操作线上环境**；每个破坏性步骤都需要负责人确认。

## 0. 本次发布包含什么（评审用）

| 类别 | 变化 | 风险 |
|---|---|---|
| 迁移 | `l0123456789e` 审计日志 `store_id` 回填；`m` 订单问题表 + 两列超时配置；`n` 订单收款快照 + 退款表（含按审核记录回填 `paid_confirmed_minor`）；`o` 缺货升级/审批控制列 | 全部为加列/建表/回填，无删列；回填是单条 UPDATE |
| 权限 | 员工/审计严格按门店；目录写权限收紧为总部（仅一家活跃门店时放行）；钱包调账路由层 403 | 单店部署无感知；**开第二家店前必须有总部账号**（见 §6） |
| 业务 | 顾客自助取消、取消退券、缺货流程、已付款删品退差价、ABA 人工退款记录 | 新功能，默认不影响既有流程 |
| 定时 | 进程内每 60 秒执行超时取消/提醒/缺货升级；**阈值默认 0 = 关闭** | 部署后不会取消任何订单 |
| 财务 | 日报、统计新增退款/待处理/对账字段；现金净流入扣除已转账 ABA 退款 | 新字段，旧字段语义不变 |

## 1. 发布前（T-1 天）

- [ ] 确认目标提交：`git log --oneline origin/main | head` 与要上线的 commit 一致；CI/本地测试已跑（见 §9）。
- [ ] 确认 VPS 当前运行版本与迁移版本：`docker compose exec backend alembic current`（期望为 `k0123456789d`，若不是先弄清原因再继续）。
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
- [ ] 迁移日志依次出现 `l0123456789e → m → n → o0123456789h`，**无报错**。
- [ ] `docker compose ps`：三个服务均 `healthy`/`running`。
- [ ] `docker compose exec backend alembic current` = `o0123456789h (head)`。

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

只有在数据损坏等极端情况才恢复数据库：
```bash
docker compose stop backend
docker compose exec -T db dropdb -U teacafe teacafe && docker compose exec -T db createdb -U teacafe teacafe
docker compose exec -T db pg_restore -U teacafe -d teacafe < teacafe_YYYYMMDD_HHMM.dump
docker compose start backend
```
> 恢复会丢失备份之后的所有订单与资金流水，必须由负责人决定，并与钱包账务对账后再放开服务。

## 9. 发布前质量门禁

- [ ] 后端：`RUN_DB_TESTS=1` 的生命周期测试、`daily_report_test.py`、`cross_store_security_test.py`（已知 2 条环境项：依赖真实 Telegram 的支付截图用例、二号店 id 写死）。
- [ ] 前端：`npx tsc -p tsconfig.app.json --noEmit`、`npm run build`。
- [ ] 迁移链在空库与「带数据的副本库」各跑一次（用 §2 的备份恢复出副本）。
- [ ] 上线前处理或明确接受下列**已知界面缺口**（见 §10）。

## 10. 已知缺口（浏览器实测发现，建议在开启缺货功能前修）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 1 | 后台订单页 | 已接单/已付款订单（备餐中）没有「报缺货」按钮，后端其实支持 | 按钮条件改为与后端一致（NEW 未付款，或 ACCEPTED/PREPARING 已付款） |
| 2 | 后台订单页 | 不显示订单的缺货状态（等待顾客选择/已处理）；有未处理缺货时仍显示「接单」，点击只得到 409；已有未处理缺货的商品仍可再次点「报缺货」 | 显示缺货徽标；有 OPEN 缺货时禁用推进按钮并提示原因；对已有 OPEN 的商品隐藏报缺货 |
| 3 | 后台 | 没有「待处理退款」页面：不能在界面里完成 ABA 人工退款（填转账号）或批准钱包退款，只有接口 | 新增退款待办列表与完成/批准操作（经理） |
| 4 | 顾客订单弹窗 | 已确认收款的订单仍显示「ABA 待支付：US$…」，删品后金额也不再对应 | 已付款订单隐藏该行，或改显示实收/已退 |
| 5 | 顾客订单弹窗 | 缺货块里的「取消订单」与底部「取消订单」重复；有未处理缺货时仍可点「已付款，发送凭证」 | 去重；缺货未处理时提示先处理缺货 |
| 6 | 顾客订单列表 | 看不出哪些订单在等待自己选择缺货处理（只靠 Bot 消息） | 列表上加「待处理缺货」徽标 |
| 7 | 财务页 | 对账差额/待处理的警示色与普通卡片区分不明显；优惠为 0 时显示「-$0.00」；日报与区间面板仍是两块 | 警示卡用红/橙；0 值不加负号；合并面板（后台重构阶段 A） |

> 以上 1–3 影响员工日常使用，4–6 影响顾客理解；都不影响数据安全。可先上线并保持新自动化关闭，按 §7 逐步开启。

## 11. 监控与值守（上线后一周）

- 日志关键字：`unpaid order expiry failed`、`cancellation notification failed`、`shortage notification failed`、`限流命中`、`ERROR`。
- 每日检查：待处理退款笔数与金额、对账差额、日报是否按时投递（`report_deliveries`）、数据库大小。
- 异常处理：Telegram 通知失败**不会**回滚订单取消/退款（设计如此），需要人工在群里补通知；退款对账差额非 0 时先看 `order_refunds` 与 `payment_reviews` 是否有缺记录。
- 限流计数在进程内存中，多副本部署时各算各的；当前单副本无影响。
