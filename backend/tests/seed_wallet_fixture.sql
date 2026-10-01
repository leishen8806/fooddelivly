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
VALUES (1, '555000111', 'Somchai', 'en', 'en')
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff (id, telegram_user_id, login_name, password_hash, role, active)
VALUES (1, '999000111', 'manager1', 'x', 'MANAGER', true),
       (2, '999000222', 'staff1',   'x', 'STAFF',   true)
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
