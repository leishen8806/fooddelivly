# Tea Cafe 优惠券实现说明

## 发放规则

- **私有发放**：管理后台每次生成一个新的 Telegram Mini App 链接；每个链接只允许第一个用户领取，领取后立即失效。私有链接数量不设上限。
- **广域发放**：发布一条公共链接，在活动有效期内可重复领取；由活动配置控制每个用户最多领取数量和总发放数量。
- 已发布的券规则冻结，只能切换上线或下架。过期、未上线、达到总量上限的券不能领取或结算使用。

## 优惠规则

- 打折券：订单商品小计达到最低消费后按百分比计算，最多减免配置的封顶金额。
- 满减券：订单商品小计达到最低消费后减固定金额，优惠不会超过商品小计。
- 配送费和服务费不参与折扣；钱包优先支付折后订单金额，剩余部分走 ABA。
- 下单时服务端重新读取券规则、锁定用户券并保存订单优惠快照，客户端金额只用于预览。

## Telegram 链接

- 私有：`https://t.me/<BOT_USERNAME>?startapp=claim_private_<token>`
- 广域：`https://t.me/<BOT_USERNAME>?startapp=coupon_public_<token>`
- Mini App 读取 `start_param` 后自动调用领取接口，并显示领取成功或失败提示。

## 主要接口

- 管理端：`GET/POST /api/v1/admin/coupons`、`POST /{id}/publish`、`POST /{id}/status`、`POST /{id}/private-link`。
- 用户端：`GET /api/v1/coupons/mine`、`POST /api/v1/coupons/claim`。
- 下单：`POST /api/v1/orders` 增加 `customer_coupon_id`，折扣由服务端计算。

数据库迁移：`backend/alembic/versions/k0123456789d_coupons.py`。
