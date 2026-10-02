-- 钱包端到端验证所需的最小种子数据（**只用于本地/测试库，不要在生产库执行**）
--
--   psql -d <db> -f tests/seed_wallet_fixture.sql
--
-- 约定：客户 id=1（telegram 555000111）、员工 id=1 MANAGER（999000111）、
--       id=2 STAFF（999000222）、员工群 -1001234567890。
-- 这些 id 与 tests/wallet_api_e2e.py 里的常量一一对应，改一边要改另一边。
-- 可重复执行（ON CONFLICT / setval 都做了幂等处理）。

INSERT INTO store_settings (currency, timezone, telegram_staff_group_id, staff_group_language, payment_link)
VALUES ('USD', 'Asia/Phnom_Penh', '-1001234567890', 'en', 'https://pay.example.com/aba')
ON CONFLICT DO NOTHING;

INSERT INTO customers (id, telegram_user_id, display_name, language_code, preferred_language)
VALUES (1, '555000111', 'Somchai', 'en', 'en'),
       -- 第二个客户：安全用例要验证「A 不能读/改 B 的充值单」
       (2, '555000222', 'Dara',    'en', 'en'),
       -- 第三个客户 = 员工 manager1 本人的 Telegram 账号：
       -- 安全用例要验证「员工不能审核自己的充值单」
       (3, '999000111', 'Manager own account', 'en', 'en'),
       -- 第四个客户：钱包**永远为空**，专供「余额不足」用例。
       -- 用客户 1 会被多轮套件越充越多，那个用例就不成立了。
       (4, '555000444', 'Empty wallet', 'en', 'en')
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff (id, telegram_user_id, login_name, password_hash, role, active)
VALUES (1, '999000111', 'manager1', 'x', 'MANAGER', true),
       (2, '999000222', 'staff1',   'x', 'STAFF',   true),
       -- 非 MANAGER 的在职员工：安全用例验证调账/退款权限
       (3, '999000333', 'staff2',   'x', 'STAFF',   true)
ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, active = EXCLUDED.active;

-- 让后续 INSERT 不会撞上写死的 id
SELECT setval(pg_get_serial_sequence('customers', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM customers), 1000));
SELECT setval(pg_get_serial_sequence('staff', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM staff), 1000));

-- 一个可下单的商品（验证「原有订单流程没被钱包改动破坏」）
-- 注意 active 必须显式给 true：`categories.active` 没有 server_default，
-- 不写就是 NULL，而 /api/v1/menu 会过滤掉未启用的分类 —— 菜单会变成空的。
INSERT INTO categories (id, name, sort_order, active)
VALUES (1, '{"en": "Drinks"}', 0, true)
ON CONFLICT (id) DO UPDATE SET active = true, sort_order = EXCLUDED.sort_order;
INSERT INTO products (id, category_id, name, price_minor, currency, available, sort_order)
VALUES (1, 1, '{"en": "Milk Tea"}', 500, 'USD', true, 0)
ON CONFLICT (id) DO UPDATE SET sort_order = EXCLUDED.sort_order, available = true;
SELECT setval(pg_get_serial_sequence('categories', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM categories), 100));
SELECT setval(pg_get_serial_sequence('products', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM products), 100));

-- 充值赠送规则：满 $10 固定送 $2（30 天）；满 $50 送 10%
INSERT INTO wallet.recharge_rules
  (id, name, currency, min_amount, max_amount, bonus_type, bonus_value,
   bonus_valid_days, priority, active)
VALUES (1, '满$10送$2', 'USD', 1000, 4999, 'fixed',   200,  30, 10, true),
       (2, '满$50送10%', 'USD', 5000, NULL, 'percent', 1000, 30,  5, true)
ON CONFLICT (id) DO UPDATE
  SET min_amount = EXCLUDED.min_amount, max_amount = EXCLUDED.max_amount,
      bonus_type = EXCLUDED.bonus_type, bonus_value = EXCLUDED.bonus_value,
      bonus_valid_days = EXCLUDED.bonus_valid_days, active = EXCLUDED.active;
SELECT setval('wallet.recharge_rules_id_seq', 100);

-- 多商家：把本夹具创建的员工/客户绑到主店（代表「门店账号」而不是总部）。
-- id < 9000 是为了不碰 seed_second_store.sql 建的二号店账号（9001/9002/9100），
-- 这样两个种子脚本谁先谁后都不会互相覆盖。
UPDATE staff SET store_id = (SELECT id FROM stores WHERE code = 'MAIN')
 WHERE store_id IS NULL AND id < 9000;
UPDATE customers SET store_id = (SELECT id FROM stores WHERE code = 'MAIN')
 WHERE store_id IS NULL AND id < 9000;

-- 多商家：把 store_settings 的配置同步到主店。
-- 迁移 g0123456789f 会把「当时已存在」的 store_settings 复制进主店；但本夹具
-- 是在迁移**之后**才插入 store_settings 的，所以新库上主店是空的——
-- 群 ID、收款链接都拿不到，群里的审核按钮会因群不匹配被忽略。
-- 生产库不存在这个问题（迁移时 store_settings 已有数据），夹具要自洽。
UPDATE stores s SET
       currency = ss.currency,
       timezone = ss.timezone,
       payment_link = ss.payment_link,
       aba_qr_asset_key = ss.aba_qr_asset_key,
       telegram_staff_group_id = ss.telegram_staff_group_id,
       staff_group_language = ss.staff_group_language,
       is_accepting_orders = ss.is_accepting_orders,
       business_hours = ss.business_hours,
       min_order_minor = ss.min_order_minor,
       delivery_fee_minor = ss.delivery_fee_minor,
       service_fee_minor = ss.service_fee_minor
  FROM store_settings ss
 WHERE s.code = 'MAIN';
