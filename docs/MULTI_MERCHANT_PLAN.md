# 多商家（7 个加盟商）改造规划

> 现状：整套系统是**单店**假设——一份 `store_settings`、一个员工群、一个菜单、
> 一个钱包池。加盟商要做到「独立结算、独立配送」，核心是把**店铺（store）**
> 变成所有业务数据的第一等维度。
>
> 本文是规划，不含代码改动。文末列出需要你们拍板的问题——**这些问题定不下来，
> schema 建出来就是错的**，尤其是钱包余额跨店归属（涉及真钱）。

---

## 1. 结论先行：需要拍板的 6 件事

| # | 问题 | 影响面 | 我的建议 |
|---|---|---|---|
| 1 | **钱包余额能不能跨店用** | 资金、结算、对账 | 余额绑定到店（`(customer_id, store_id)`），跨店要显式转账 |
| 2 | 一个 bot 还是 7 个 bot | 运营、客户归属 | 1 个 bot + 按二维码/深链带 `store_id` 绑定客户 |
| 3 | 菜单是共享还是各店独立维护 | 运营工作量 | 总部维护「品牌菜品库」，各店可覆盖价格/上下架/售卖时间 |
| 4 | 客户能否跨店下单 | 配送、结算 | 默认绑定一个店；换店需重新绑定（或管理员改绑） |
| 5 | 抽成方式 | 结算 | 按店配置「抽成比例 + 固定费」，写进结算单 |
| 6 | 结算周期与出账方式 | 财务 | 日结/周结生成结算单，加盟商在后台确认 |

---

## 2. 目标数据模型

### 2.1 新增核心表

```sql
-- 店铺（加盟商）
CREATE TABLE stores (
  id                SERIAL PRIMARY KEY,
  code              TEXT UNIQUE NOT NULL,          -- 'ST01'，订单号/幂等键里用
  name              JSON NOT NULL,                 -- 三语名称
  status            TEXT NOT NULL DEFAULT 'ACTIVE',-- ACTIVE | SUSPENDED | CLOSED
  -- 经营参数（现在在全局 store_settings 里的东西，逐个下移到店）
  currency          TEXT NOT NULL DEFAULT 'USD',
  timezone          TEXT NOT NULL DEFAULT 'Asia/Phnom_Penh',
  payment_link      TEXT,
  aba_qr_asset_key  TEXT,
  telegram_staff_group_id  TEXT,                   -- 每店一个员工群
  staff_group_language     TEXT DEFAULT 'en',
  service_fee_minor INTEGER NOT NULL DEFAULT 0,    -- 服务费（可选）
  min_order_minor   INTEGER NOT NULL DEFAULT 0,    -- 最低起送
  delivery_fee_minor INTEGER NOT NULL DEFAULT 0,
  commission_bps    INTEGER NOT NULL DEFAULT 0,    -- 抽成，万分比（100 = 1%）
  commission_fixed_minor INTEGER NOT NULL DEFAULT 0,
  is_accepting_orders BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 加盟商主体（一个老板可能有多个店）
CREATE TABLE merchants (
  id SERIAL PRIMARY KEY, name TEXT NOT NULL, contact TEXT,
  settlement_account TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE stores ADD COLUMN merchant_id INTEGER REFERENCES merchants(id);

-- 员工归属：总部 vs 某店
ALTER TABLE staff ADD COLUMN store_id INTEGER REFERENCES stores(id);   -- NULL = 总部
ALTER TABLE staff ADD COLUMN scope TEXT NOT NULL DEFAULT 'STORE';     -- HQ | STORE

-- 客户归属
ALTER TABLE customers ADD COLUMN store_id INTEGER REFERENCES stores(id);

-- 配送范围（现在只有「房号」，多店必须有范围概念）
CREATE TABLE store_delivery_zones (
  id SERIAL PRIMARY KEY,
  store_id   INTEGER NOT NULL REFERENCES stores(id),
  label      TEXT NOT NULL,             -- 'A 栋' / '3 楼' / '泳池区'
  match_type TEXT NOT NULL,             -- ROOM_PREFIX | ROOM_LIST | AREA
  match_value TEXT NOT NULL,            -- 'A' / '101,102' / 'pool'
  delivery_fee_minor INTEGER,           -- NULL = 用店铺默认
  min_order_minor    INTEGER,
  active BOOLEAN NOT NULL DEFAULT true
);
```

### 2.2 业务表加 `store_id`

需要加 `store_id NOT NULL` 的表（**顺序即迁移顺序**）：

| 表 | 说明 |
|---|---|
| `categories` / `products` / `product_option_groups` / `product_options` / `product_sale_windows` | 菜单与规格 |
| `orders` / `order_items` | 订单（`order_items` 可只冗余，主过滤在 orders） |
| `payment_reviews` / `payment_proofs` | 收款审核 |
| `wallet.wallets` / `wallet.recharge_orders` / `wallet.ledger_entries` | **钱包按店**（见第 3 节） |
| `audit_logs` / `report_deliveries` | 审计与报表 |
| `staff` / `customers` | 归属 |

### 2.3 共享菜品库（可选但强烈建议）

7 家店各配一遍菜单不现实。两种做法：

- **A. 模板 + 覆盖**：`products` 属于总部（`store_id IS NULL` 表示模板），
  各店用 `store_product_overrides(store_id, product_id, price_minor, available, sort_order)`
  覆盖价格与上下架；售卖时间也建在覆盖表上。
- **B. 复制**：总部改完「下发」到各店（各店数据独立，改一次要下发一次）。

推荐 **A**：改一次全店生效，个别店再单独调价；缺点是查询要 `LEFT JOIN` 覆盖表。

---

## 3. 结算：独立结算怎么做

### 3.1 一条订单的钱怎么分

```
订单总额 total
  − 客户用掉的钱包余额（本金/赠送）   ← 这是「已收的钱」，直接进店
  − 服务费/配送费                     ← 归店或归平台，按配置
  = 店铺应结基数
      平台抽成 = 基数 × commission_bps / 10000 + commission_fixed
      店铺应结 = 基数 − 平台抽成
```

### 3.2 账本

**不要**在订单表上直接改数字算钱，用**追加式账本**（和现在钱包的做法一致）：

```sql
CREATE TABLE merchant_ledger_entries (
  id BIGSERIAL PRIMARY KEY,
  store_id     INTEGER NOT NULL REFERENCES stores(id),
  merchant_id  INTEGER REFERENCES merchants(id),
  entry_type   TEXT NOT NULL,      -- ORDER_REVENUE | COMMISSION | REFUND | ADJUSTMENT | PAYOUT
  amount_minor BIGINT NOT NULL,    -- 正数=应结给店，负数=扣回
  currency     TEXT NOT NULL,
  biz_type     TEXT,               -- 'order' | 'refund' | 'settlement'
  biz_id       TEXT,               -- 订单号/结算单号
  idempotency_key TEXT UNIQUE,     -- 每笔只记一次
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- 订单完成（COMPLETED）→ 记 `ORDER_REVENUE` + 负数 `COMMISSION`；
- 退款 → 记负数冲回；
- 结算 → 生成 `settlement_statements`，把「已结算」的部分标记掉；
- 任何时候都能用 `SUM(amount)` 重算「店应结余额」，和钱包一样要有对账函数
  （`reconcile_merchant()`），必须恒为空。

### 3.3 结算单

```sql
CREATE TABLE settlement_statements (
  id BIGSERIAL PRIMARY KEY,
  store_id INTEGER NOT NULL, merchant_id INTEGER,
  period_start DATE NOT NULL, period_end DATE NOT NULL,
  gross_minor BIGINT NOT NULL,          -- 期间营业额
  commission_minor BIGINT NOT NULL,     -- 平台抽成
  refunds_minor BIGINT NOT NULL,
  net_payable_minor BIGINT NOT NULL,    -- 应结给加盟商
  status TEXT NOT NULL DEFAULT 'DRAFT', -- DRAFT | CONFIRMED | PAID
  paid_at TIMESTAMPTZ, paid_reference TEXT,
  statement_no TEXT UNIQUE NOT NULL,    -- 'ST01-2026-10'
  UNIQUE (store_id, period_start, period_end)
);
```

### 3.4 钱包跨店这件事（最关键）

独立结算的前提下，**客户在 A 店充的钱不能在 B 店花掉**，否则 B 店出了货却收不到钱，
谁来垫？三个方案：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A | 钱包按 `(customer_id, store_id)` 建 | 账最干净，天然独立结算 | 客户换店要重新充值/转账 |
| **B（已选定）** | 余额全局，消费时记「B 店应收 A 店」的内部往来 | 客户体验最好 | 需要加盟商之间的清算，最复杂 |
| C | 余额全局，但充值时就指定店铺，消费时校验同店 | 折中 | 客户容易困惑「为什么这里有 10 元却用不了」 |

> **已定：方案 B —— 余额全局通用，店间记内部往来。**
> 迁移最省事（现有 `wallet.wallets` 不用动），但**必须同时把内部往来账做出来**，
> 否则「钱被 A 店收了、货被 B 店出了」这笔账在系统里根本不存在，到结算那天才发现对不平。

### 3.5 方案 B 的内部往来（必须先做，不能后补）

核心：**钱在哪家店收的，就要给消费的那家店挂账。**

1. 每次充值记录「收款店」：`wallet.recharge_orders.store_id`（充值时客户当前绑定/下单的店）；
2. 每次消费记录「消费店」：`orders.store_id`；
3. 消费时若 `充值收款店 ≠ 消费店`，写一条内部往来：

```sql
CREATE TABLE store_interstore_entries (
  id BIGSERIAL PRIMARY KEY,
  from_store_id INTEGER NOT NULL,        -- 欠钱的店（收了钱的那家）
  to_store_id   INTEGER NOT NULL,        -- 应收的店（出了货的那家）
  customer_id   INTEGER NOT NULL,
  amount_minor  BIGINT  NOT NULL,
  currency      TEXT    NOT NULL,
  biz_type      TEXT    NOT NULL,        -- 'order' | 'refund' | 'settlement'
  biz_id        TEXT,                    -- 订单号
  idempotency_key TEXT UNIQUE NOT NULL,  -- 每笔只记一次
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

4. **净额结算**：A 与 B 之间双向的往来在结算时轧差，只结净额；
5. **必须能对账**：`SUM(店内订单金额) + SUM(内部往来净额) == SUM(客户钱包总余额)`。
   这条恒等式要写成 `reconcile_global()` 并纳入日常巡检——方案 B 的风险全在这里；
6. 赠送金跨店消费时同理：赠送是 A 店给的营销成本，客户在 B 店用了，
   A 店应补给 B 店（否则没人愿意发券）。

> 这一步写完，7 家店之间的钱才是「可解释」的。**建议和 P4 结算同批做，
> 不要先上 B 再补往来账**——中间那段时间的对账是补不回来的。

---

## 4. 配送：独立配送

现在订单只有「房号」（酒店/公寓场景），多店之后需要：

1. **配送范围**：`store_delivery_zones` 决定这个店能不能送到某房号/区域；
   下单时按店铺范围校验，超范围 → `409 超出配送范围`。
2. **配送费/最低起送**：按区域或店铺默认，写进订单（现在没有这两个字段）。
3. **配送员**：新角色 `COURIER`（`staff.scope='STORE'`），
   订单状态加 `OUT_FOR_DELIVERY`；或者先不做系统派单，只做「谁送的」记录。
4. **订单号带店前缀**：`ST01-RC20261002000001`，跨店一眼可辨，
   幂等键也要带 `store_id`（现在是 `store_id` 缺失的全局键）。

---

## 5. 权限与隔离（安全上最容易出事的地方）

| 角色 | 范围 | 能做什么 |
|---|---|---|
| `HQ_ADMIN` | 全部店 | 建店、配抽成、看全部报表、结算 |
| `HQ_FINANCE` | 全部店（只读钱） | 看结算单、确认打款 |
| `STORE_MANAGER` | 本店 | 本店菜品/价格/售卖时间、审核本店充值、本店员工 |
| `STORE_STAFF` | 本店 | 接单/出餐/收款审核 |
| `COURIER` | 本店 | 看自己的配送单、标记送达 |

**硬性要求**：
- 每个接口都要有 `store_id` 过滤，**不能只靠前端传的 store_id**——
  必须从会话（员工/客户归属）推导，否则改一个参数就能看别家店的营业额；
- 现有的「自己批自己」防护（`wallet.is_order_owner_staff`）在分店后还要加一层：
  **不能审核别家店的单**；
- 测试必须补一批**跨店越权**用例（A 店员工读/改 B 店数据），
  和现在的 `wallet_security_test.py` 一样，真的发请求。

---

## 6. 迁移路径（不停机，单店 → 多店）

| 阶段 | 内容 | 风险 |
|---|---|---|
| **P0** 打地基 | 建 `stores`/`merchants`，插入「主店」；所有业务表加**可空** `store_id` 并回填默认店 | 低 |
| **P1** 读路径 | 所有查询加 `store_id` 过滤（默认店）；API 从会话推导 store 上下文 | 中：漏一处就是跨店泄露 |
| **P2** 写路径 | 建单/建充值/审计写入带 `store_id`；`store_id` 改 `NOT NULL` | 中 |
| **P3** 配置下移 | `store_settings` 拆分到 `stores`；每店员工群、语言、支付信息 | 中 |
| **P4** 结算 | 商家账本 + 结算单 + 对账函数 + 财务页 | 高：涉及真钱，必须双写校验 |
| **P5** 配送 | 配送范围/配送费/最低起送 + 配送员角色 | 中 |
| **P6** 灰度 | 第 2 家店上线，跑一周对账 | — |

> 建议 P0–P2 一轮做完（数据结构 + 隔离），因为**半隔离状态最危险**：
> 加了 `store_id` 但读路径没过滤，反而是「看起来隔离了、其实没隔离」。

---

## 7. 工作量粗估

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0–P2 | 数据模型 + 全局隔离 + 跨店越权测试 | 5–8 人日 |
| P3 | 配置下移 + 每店员工群 | 2–3 人日 |
| P4 | 结算账本 + 结算单 + 财务页 + 对账 | 5–8 人日 |
| P5 | 配送范围 + 配送费 + 配送员 | 3–4 人日 |
| P6 | 灰度 + 对账 + 文档 | 2–3 人日 |
| **合计** | | **17–26 人日** |

（不含微信/独立 App 端、不含各加盟商的 POS 对接。）

---

## 8. 现在就先做、不会白做的事

这几件事无论最终选哪个方案都要做，可以立刻排期：

1. **订单/充值的幂等键加店铺前缀**（现在没有 store 概念也能先加 `store_code` 占位）；
2. **把 `store_settings` 的读取收敛到一个函数**（`get_store_runtime()`），
   以后换成「按店读」只改一处；
3. **配送范围校验**（先只支持主店），字段先加上；
4. **财务口径按店可切**：日报/统计接口预留 `store_id` 参数（默认主店）；
5. **跨店越权测试框架**先搭起来（等 P0 有第二家店就能跑）。

---

## 9. 待你确认

**已定**：钱包余额 = 全局通用 + 店间内部往来（方案 B，见 3.5）。

仍未定：

1. 一个 bot 服务 7 家店，还是每家一个 bot？
2. 菜单：总部统一维护 + 各店改价（推荐），还是各店完全独立？
3. 抽成：比例（%）还是固定金额？日结还是周结？谁确认打款？
4. 配送：是否要系统派单（配送员 App/机器人接单），还是只记录「谁送的」？
5. 加盟商的登录方式：复用现在的员工后台（加 `scope`），还是需要独立的加盟商后台账号体系？
