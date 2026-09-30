# Tea Cafe 技术设计文档

> 产品名称：Tea Cafe　|　状态：技术基线与部署状态　|　更新：2026-09-30。项目由空仓库启动；代码已实现并部署至 VPS，Telegram 与 ABA 业务验收待配置。

| 项目项 | 值 |
|---|---|
| GitHub 仓库 | `git@github.com:leishen8806/fooddelivly.git` |
| 工作目录 | `E:\TeaCafe` |
| 部署 VPS | `159.223.92.104`（Docker 服务运行中，PostgreSQL 15.19，迁移 `3c7d8e9f012a`） |
| 顾客 Mini App | `https://food.workline.ink/` |
| 管理后台 | `https://food.workline.ink/admin` |
| DNS | 按项目方提供的信息，`food.workline.ink` 已解析到 `159.223.92.104` |

## 1. 设计目标与边界

为单一商家提供 Telegram Mini App 顾客点餐、Telegram Bot 凭证收集/通知，以及店长 Web 管理后台。首版付款核验由店员在 ABA 实际收款记录中人工完成；ABA API、支付回调和自动对账都属于待商户资格确认后的后续工作。

## 2. 建议架构

```text
Telegram 客户端
  ├─ Mini App（顾客菜单/结账/订单） ───────── HTTPS ─┐
  ├─ Bot 私聊（顾客上传付款截图） ─ Telegram API ──┤ API 服务 ─ PostgreSQL
  └─ 员工 Telegram 群组（订单按钮） ─ Telegram API ─┘        └─ 私有凭证访问
普通浏览器 ─ /admin 登录 + 会话 Cookie ─ HTTPS ───────────────┘
```

### 建议技术栈（新仓库默认）

- 后端：Python + FastAPI；同一进程/服务挂载 Aiogram 3 Bot webhook，减少服务数量。
- 前端：TypeScript + React + Vite，一套前端分 `/`（Telegram Mini App 顾客点餐）、`/admin/login`（后台登录）和 `/admin`（店员后台）。顾客登录使用 Telegram 官方 JS bridge；后台浏览器登录使用独立员工会话。
- 数据库：PostgreSQL；金额以最小货币单位整数存储，商品与订单分表，订单明细写入价格/名称快照。
- 部署：单一 Docker Compose 或单机容器部署；HTTPS 反向代理、PostgreSQL 持久卷和每日备份。首版不引入 Redis、微服务、消息队列或独立搜索服务。

最终依赖版本和部署平台在实施前再锁定；此仓库目前无既有框架、依赖或部署基线。

## 3. 认证与员工授权

### 3.1 顾客 Mini App 自动登录

使用 Bot 私聊中的 Mini App 菜单按钮（或配置好的 Main Mini App/直接链接）打开应用。不可把依赖自动身份的主流程放在会返回空 `initData` 的 keyboard button 或 inline mode 启动方式上。非 Telegram 浏览器直接访问时展示“请从商家 Telegram Bot 打开”，不提供模拟身份降级。

登录步骤与校验方式沿用 Mini App 规范：

1. 前端加载 Telegram 官方 `telegram-web-app.js`，调用 `ready()`，读取原始字符串 `Telegram.WebApp.initData`。不把 `initDataUnsafe` 当作身份依据。
2. 前端 `POST /api/v1/auth/telegram`，发送原始 `initData`，不发送客户端自报的用户 ID 作为认证依据。
3. 后端按 Telegram 文档解析字段、排序生成 data-check-string，并使用 Bot Token 派生的 `WebAppData` HMAC-SHA-256 密钥校验 `hash`；使用常量时间比较。
4. 校验 `auth_date` 在配置的有效窗口内（建议 5 分钟，允许少量时钟偏差）；拒绝无效、缺少 user 或过期数据。
5. 从已校验的 `user` 读取 Telegram 数字 ID。按 ID 查找/创建顾客记录并更新允许保存的展示名、用户名、语言等资料；不以 username 作为主键。
6. 后端返回 `Secure; HttpOnly; SameSite=Lax` 会话 Cookie（前后端同源部署），后续 API 从 Cookie 取会话。Mini App 不在 `localStorage` 保存长期访问 token。
7. 会话到期后在下次 Mini App 打开时重新用新的 `initData` 登录。退出/撤销会话时服务端删除会话记录。

自动登录只证明 Telegram 客户端提供了有效 Telegram 用户上下文，不证明顾客支付、房间身份或订单真实性。店员角色由数据库中的授权记录控制，绝不从 `initData` 字段推断。

### 3.2 后台浏览器登录

员工可直接在普通浏览器打开 `https://food.workline.ink/admin`；无会话时进入 `/admin/login`。后台身份不得依赖 Mini App `initData`。

**待定的实现输入：**登录标识采用用户名还是邮箱/手机号，首位店长账号如何安全初始化。建议用独立员工账号和密码登录、邀请员工设置密码、首位店长通过一次性服务器端初始化；密码只存安全哈希，登录成功后下发 `Secure; HttpOnly` 会话 Cookie。实现前需确定具体登录标识和初始化方式。

### 3.3 多语言与本地化

- 支持 `zh-CN`（简体中文）、`en`（英语）、`km`（高棉语）；UI 文案使用稳定 key，由前端本地化资源解析，不把翻译文本用作逻辑判断。
- 首次访问读取 Telegram 验证后 `user.language_code`，按支持语言映射；不支持的语言回退英语。Mini App 和 `/admin` 均可手动切换并保存偏好；后台每位员工可独立选择语言。
- 创建订单时保存 `customer_language` 快照。Bot 私聊通知使用该快照；员工群组使用门店 `staff_group_language` 配置；API 和日志中的状态仍使用固定枚举值。
- 三语完整基础文案位于 `docs/ui/locales.json`。构建时须检查所有 locale key 集合一致、无空译文；代码审查要求新增用户可见文案同时补齐三种语言。
- 商品名称/描述是可编辑内容，存为 locale map；缺失商品译文时使用英语并在管理界面标注缺失。不要对菜名、备注或用户输入做机器翻译后覆盖原文。
- 日期、数字和金额按语言/币种 locale 格式展示，金额存储和统计口径不随显示语言改变。高棉语使用支持 Unicode shaping 的字体；测试 Khmer 字符组合、字体回退、行高和响应式布局。

### 3.3 Telegram 群组权限

- 配置一个订单操作群组 `telegram_staff_group_id`；订单 Bot 消息仅发送订单号、房间号、商品摘要、金额和当前状态。顾客付款图片留在私聊/后台，不转发到群组。
- Bot callback 同时验证来源 `chat_id` 属于配置群组、点击人的 Telegram ID 绑定到有效 `STAFF`/`MANAGER` 账号、当前订单状态允许该操作。仅是 Telegram 群管理员不构成 Tea Cafe 员工授权。
- 群组和后台复用同一后端状态转换服务；数据库事务检查当前状态和支付状态，首个合法操作成功，重复/并发 callback 幂等拒绝或返回当前状态。
- 成功后更新群组订单卡的状态和下一步按钮，并私聊顾客状态变化；所有操作写 `order_events`，记录执行员工、来源群组和时间。
- 按钮顺序：`接单` → `确认支付` → `出餐` → `已配送` → `已完成`。`确认支付` 仅在订单已接单且付款凭证已提交时可用；员工仍需核验 ABA 实际到账。

## 4. 核心数据模型

| 表 | 关键字段 | 说明 |
|---|---|---|
| `customers` | `id`, `telegram_user_id UNIQUE`, `display_name`, `username`, `created_at` | Telegram 验证后的顾客身份 |
| `staff` | `id`, `telegram_user_id UNIQUE`, `login_name UNIQUE`, `password_hash`, `role`, `active` | 后台浏览器账号与 Bot 群组身份绑定；`MANAGER` / `STAFF` 权限。登录标识/bootstrap 方式待确认 |
| `categories` | `id`, `name`, `sort_order`, `active` | 菜单分类 |
| `products` | `id`, `category_id`, `name`, `description`, `price_minor`, `currency`, `image_key`, `available` | 商品当前配置 |
| `orders` | `id`, `public_code UNIQUE`, `customer_id`, `room_number`, `order_status`, `payment_status`, `currency`, `total_minor`, `telegram_group_message_id`, timestamps | `public_code` 使用不可预测随机值，不暴露自增 ID；群组消息 ID 用于编辑订单卡 |
| `order_items` | `order_id`, `product_id`, `product_name_snapshot`, `unit_price_minor`, `quantity`, `options_json`, `line_total_minor` | 订单创建时的商品快照 |
| `payment_proofs` | `id`, `order_id`, `telegram_file_id`, `submitted_by`, `submitted_at`, `review_status` | Bot 收到的付款图片元数据；员工在 Bot 或后台查看 |
| `payment_reviews` | `id`, `order_id`, `staff_id`, `decision`, `reason`, `created_at` | 追加式人工核验历史，不覆盖旧记录 |
| `order_events` | `id`, `order_id`, `actor_type`, `actor_id`, `event`, `from_state`, `to_state`, `created_at` | 状态与审计事件 |
| `store_settings` | `currency`, `timezone`, `aba_qr_asset_key`, `payment_link`, `telegram_staff_group_id`, `open_hours`, `delivery_mode` | 单商家门店配置；收款和员工操作群组由店长配置 |

金额使用整数最小单位与 ISO 币种代码，例如 USD cents、KHR riel；小数位从币种配置计算。数据库事务内再次读取商品价格、计算总额、写入订单和明细，避免客户端篡改或并发下单产生错误总额。

付款图片可先保留 Telegram `file_id`，只通过授权 Bot/后台代理查看；不公开 Telegram 文件 URL 或 Bot Token。上线前验证图片访问、保留周期及备份方式。若需下载到本地私有卷，应限制 MIME/文件大小并纳入备份和清理策略。

## 5. 付款和订单处理

### 5.1 MVP 人工核验

1. `POST /api/v1/orders` 在数据库事务中创建订单，初始 `order_status=NEW`、`payment_status=UNPAID`；Bot 将新单发到配置员工群组并附“接单”按钮。
2. 后端返回应付金额、门店配置的 QR 图片或支付链接、Bot deep link 和随机 `public_code`。只有 ABA 商户确认可用的支付介质才能出现在生产配置中。
3. 顾客点“发送付款凭证”打开 `https://t.me/<bot>?start=pay_<public_code>`。Bot 校验发起人 Telegram ID 与订单 `customer_id` 一致，再接收照片并写 `payment_proofs`，将付款状态置为 `PROOF_SUBMITTED`；订单状态仍独立维护。
4. 员工在管理后台或群组点“接单”，订单状态变为 `ACCEPTED`。群组的“确认支付”按钮仅在订单已接单且有凭证后可用；员工核对 ABA 实际入账后确认，事务内将付款状态改为 `PAID_CONFIRMED`、订单状态改为 `PREPARING`。拒绝凭证需写原因并通知顾客补交。
5. 员工在群组依序点“出餐”（`PREPARING→READY`）、“已配送”（`READY→DELIVERED`）、“已完成”（`DELIVERED→COMPLETED`）。每个动作验证操作者/群组/来源状态，更新原群组消息并通知顾客。
6. 截图上传、OCR 结果或顾客声明不能独立改变付款为已确认；顾客图片不发到操作群组。

### 5.2 ABA 接入边界

ABA 官方公开资料说明其商户服务提供静态 KHQR，ABA Merchant App 可生成 QR，POS 支持动态 QR；具体商户资格、API、支付链接、回调和自动查询能力需向 ABA/PayWay 申请确认。文档不把静态二维码等同于带金额的订单支付，也不假设普通账户可调用 API。

后续若取得官方接口：为每个订单建立唯一支付尝试；校验 ABA 签名/回调身份、金额、币种和商户号；以 ABA 交易号设唯一约束并幂等处理；成功回调更新支付记录，但保留对账差异告警与人工复核能力。严禁从截图 OCR 自动确认入账。

## 6. API 草案

所有顾客和后台 API 需要会话 Cookie；员工 API 额外需要 `STAFF`/`MANAGER` 角色。Bot webhook 独立验证 Telegram secret token。

商品图片和 ABA 收款二维码由店长通过 `POST /api/v1/admin/uploads` 上传，限制 PNG/JPEG/WebP、单图最大 5 MB；文件写入持久化 `uploaded_images` 卷，返回的同源图片地址保存到 `products.image_key` 或 `store_settings.aba_qr_asset_key`。这类展示图片可公开读取；顾客付款凭证仍走 Bot 私聊及授权后台，不存入该公开图片目录。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/v1/auth/telegram` | 校验 Mini App `initData`、建立会话 |
| `POST` | `/api/v1/auth/logout` | 撤销当前会话 |
| `GET` | `/api/v1/me` | 当前顾客/员工身份与可用角色 |
| `POST` | `/api/v1/admin/auth/login` | 后台员工账号登录（具体登录标识待确认） |
| `POST` | `/api/v1/admin/auth/logout` | 撤销后台员工会话 |
| `GET` | `/api/v1/admin/me` | 当前后台员工身份和角色 |
| `GET` | `/api/v1/menu` | 返回可售菜单和分类 |
| `POST` | `/api/v1/orders` | 创建订单；请求含商品 ID/数量/选项、房间号，不含可信总额 |
| `GET` | `/api/v1/orders` | 顾客仅取本人订单；员工可加状态过滤 |
| `GET` | `/api/v1/orders/{public_code}` | 读取授权范围内订单详情 |
| `GET` | `/api/v1/admin/products` | 员工查询商品 |
| `POST` | `/api/v1/admin/uploads` | 店长上传商品或 ABA 二维码图片（multipart/form-data） |
| `GET` | `/api/v1/uploads/{filename}` | 展示已上传的商品/收款二维码图片 |
| `POST` | `/api/v1/admin/products` | 店长创建商品 |
| `PATCH` | `/api/v1/admin/products/{id}` | 店长编辑、上下架商品 |
| `GET` | `/api/v1/admin/analytics?from=&to=` | 按门店时区返回聚合统计 |
| `POST` | `/api/v1/admin/orders/{id}/payment-review` | 员工人工确认或拒绝，并记录原因 |
| `PATCH` | `/api/v1/admin/orders/{id}/status` | 更新履约状态，校验状态机 |
| `GET/POST/PATCH` | `/api/v1/admin/staff`、`/api/v1/admin/staff/{id}` | 店长查询、授权或停用员工 |
| `POST` | `/api/v1/telegram/webhook` | 接收 Bot 私聊图片、群组消息和状态按钮 callback；群组按钮走共享状态服务 |

订单创建支持 `Idempotency-Key`；后台状态操作用数据库事务和当前状态条件更新，避免双击/重复 webhook 重复确认。

## 7. 管理后台界面

- **Orders：**状态计数、时间筛选、订单号/房间搜索、商品/总额、付款凭证预览、确认/拒绝和履约状态操作。
- **Products：**分类、图片、价格、规格、售罄/上下架。
- **Analytics：**订单量、订单额、已确认收款、待核验金额、取消订单；明确标注统计日期和币种。
- **Admin login：**普通浏览器直达 `/admin`；未登录跳转 `/admin/login`。后台会话与顾客 Telegram 会话分开。
- **Staff/Settings：**仅店长可管理后台员工登录名、员工 Telegram ID/角色、操作群组 ID、营业设置、币种和 ABA 收款资料。
- 从后台确认付款时需二次确认操作和展示金额/订单号；群组 Bot 同样校验支付凭证状态、员工权限和群组来源；操作落同一审计记录。

完整 UI 屏幕/状态清单与交互矩阵见 [`UI-DESIGN-HANDOFF.md`](ui/UI-DESIGN-HANDOFF.md)，对应静态 SVG 包含顾客 Mini App、浏览器后台登录、管理后台、员工群组操作和跨角色交接。它们不是已实现页面或浏览器验证结果；图中样例数据不能当作真实商品/统计口径。

## 8. 安全与可靠性

- Telegram 登录只信任服务端 HMAC 验证后的 `initData`；检查 `auth_date` 时效，敏感操作不接受客户端 `initDataUnsafe`。
- Bot webhook 设置并校验 `secret_token`；Bot Token 与 ABA 密钥仅存服务器 Secret，不写入源码、前端构建产物或日志。
- Cookie 使用 `Secure`、`HttpOnly`、`SameSite=Lax`；API 限制 CORS 来源，变更请求校验 Origin/CSRF；同源反代优先。
- 所有订单、商品、付款确认 API 做对象级授权，顾客只能读取/修改自己允许的订单操作；员工操作检查数据库角色。
- 商品状态和金额服务端计算，关键金额写入订单明细快照；状态转换集中校验并审计。
- 验证图片类型和大小；凭证只给订单本人/授权员工访问，文件链接不得公开或出现在分析日志。
- PostgreSQL 每日备份并定期验证恢复；Bot webhook 和业务请求按 Telegram 更新 ID/幂等键去重。

## 9. 部署与运行

- 工作目录：`E:\TeaCafe`。VPS `159.223.92.104` 已运行独立 Docker Compose 服务和 PostgreSQL 15.19；Alembic 当前为 `3c7d8e9f012a`。`https://food.workline.ink/health`、菜单 API 与 `/admin` 已通过 HTTPS 验证；Caddy 自动 TLS 证书已签发。
- `https://food.workline.ink/` 和 `/admin` 当前可访问；Telegram Bot Token/用户名仍未配置，因此 Telegram `initData` 登录、Bot webhook 和真实点餐流程尚未验收。不得把静态页面可访问描述为 Bot 点餐已上线。
- 配置 Telegram 员工订单操作群组；Bot webhook 必须校验 secret token，并在每个群组 callback 上校验群组 ID、员工身份及订单当前状态。
- 源码仓库：`git@github.com:leishen8806/fooddelivly.git`。部署时从该仓库构建并发布。
- 单个应用服务提供 API、后台静态资源和 Telegram webhook；反向代理终止 TLS；PostgreSQL 独立持久化。
- Telegram Bot 配置 webhook URL 与 secret token；Mini App URL 配置在 BotFather/`setChatMenuButton`。
- 基础监控：服务健康、数据库连接、Bot webhook 错误、新单通知失败、备份结果；不要记录 Bot token、Cookie、完整付款凭证内容。
- 上线前配置 `.env` 示例（不得包含真实密钥）、数据库迁移、备份/恢复说明、Telegram webhook 设置步骤和 ABA 人工核验操作流程。

## 10. 建议实现顺序

1. Mini App `initData` 校验与身份会话、顾客菜单和商品管理。
2. 购物车、房间号、订单创建和价格快照。
3. Bot deep link、付款图片关联、员工订单列表和人工核验。
4. 订单履约状态、管理后台订单操作和通知。
5. 订单/收款统计、权限/审计、部署备份及 Telegram 真机验收。
6. 与 ABA 确认商户接口后再评估动态 QR/支付链接和自动对账。

## 参考资料

- [Telegram Mini Apps：启动方式、`initData` 与 HMAC 校验](https://core.telegram.org/bots/webapps)
- [Telegram Bot API：Bot webhook 和 Mini App 接口](https://core.telegram.org/bots/api)
- [ABA Bank：Payment Collection Service](https://www.ababank.com/payment-collection/)
- [ABA Bank：ABA Merchant App 与静态/动态 QR](https://www.ababank.com/payway-mobile/)
