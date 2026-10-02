-- 多商家测试的前置数据：一号店（MAIN，迁移自动创建）+ 二号店（ST02）
-- 幂等，可反复执行。用于 tests/multi_store_e2e.py。
INSERT INTO merchants (code, name) VALUES ('M02', 'Franchisee Two')
ON CONFLICT (code) DO NOTHING;

INSERT INTO stores (code, merchant_id, name, currency, timezone,
                    commission_bps, commission_fixed_minor, settlement_cycle)
SELECT 'ST02', m.id, '{"en": "Branch Two", "zh-CN": "二号店"}'::json, 'USD', 'Asia/Phnom_Penh',
       800, 50, 'WEEKLY'
  FROM merchants m WHERE m.code = 'M02'
ON CONFLICT (code) DO NOTHING;

-- 二号店把 1 号菜改成 7.00（总部模板价是 5.00），用于验证「菜单与下单共用覆盖逻辑」
INSERT INTO store_product_overrides (store_id, product_id, price_minor, sort_order)
SELECT s.id, p.id, 700, 0
  FROM stores s, products p
 WHERE s.code = 'ST02' AND p.id = 1
ON CONFLICT (store_id, product_id) DO UPDATE SET price_minor = EXCLUDED.price_minor;

-- 测试客户绑回主店，保证用例可重复
UPDATE customers SET store_id = (SELECT id FROM stores WHERE code = 'MAIN') WHERE id = 1;

-- 二号店的员工与客户（固定 id，方便测试直接签 JWT）
INSERT INTO staff (id, login_name, password_hash, role, active, store_id)
VALUES (9001, 'st02manager', 'x', 'MANAGER', true, (SELECT id FROM stores WHERE code = 'ST02')),
       (9002, 'st02staff',   'x', 'STAFF',   true, (SELECT id FROM stores WHERE code = 'ST02'))
ON CONFLICT (id) DO UPDATE SET store_id = EXCLUDED.store_id, active = true;

INSERT INTO customers (id, telegram_user_id, display_name, language_code, preferred_language, store_id)
VALUES (9100, '555009100', 'Branch Two Customer', 'en', 'en',
        (SELECT id FROM stores WHERE code = 'ST02'))
ON CONFLICT (id) DO UPDATE SET store_id = EXCLUDED.store_id;
