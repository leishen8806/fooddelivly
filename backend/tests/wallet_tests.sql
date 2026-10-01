-- ============================================================================
--  Tea+Cafe 钱包充值 —— 端到端验收测试
--  用法: psql -h <host> -U postgres -d <db> -v ON_ERROR_STOP=1 -f tests.sql
--  所有用例必须 PASS，最后输出 ALL TESTS PASSED
-- ============================================================================
\set ON_ERROR_STOP on

DROP TABLE IF EXISTS public.test_results;
CREATE TABLE public.test_results (
  seq    SERIAL PRIMARY KEY,
  name   TEXT NOT NULL,
  ok     BOOLEAN NOT NULL,
  detail TEXT
);

-- 清空数据（TRUNCATE 不触发行级不可变触发器）
TRUNCATE wallet.ledger_entries, wallet.order_payments, wallet.recharge_proofs,
         wallet.recharge_orders, wallet.bonus_lots, wallet.wallets, wallet.audit_log,
         wallet.recharge_rules RESTART IDENTITY CASCADE;

-- 测试夹具 1：客户（wallet.* 对 public.customers 有外键，id 必须真实存在）
INSERT INTO public.customers (id, telegram_user_id, display_name, preferred_language)
VALUES (1001, 'tg-test-1001', 'test 1001', 'en'), (1099, 'tg-test-1099', 'test 1099', 'en'), (2001, 'tg-test-2001', 'test 2001', 'en'), (2002, 'tg-test-2002', 'test 2002', 'en'), (3001, 'tg-test-3001', 'test 3001', 'en'), (4001, 'tg-test-4001', 'test 4001', 'en'), (5001, 'tg-test-5001', 'test 5001', 'en'), (6001, 'tg-test-6001', 'test 6001', 'en'), (7001, 'tg-test-7001', 'test 7001', 'en'), (7777, 'tg-test-7777', 'test 7777', 'en'), (8001, 'tg-test-8001', 'test 8001', 'en'), (8002, 'tg-test-8002', 'test 8002', 'en'), (9101, 'tg-test-9101', 'test 9101', 'en'), (9501, 'tg-test-9501', 'test 9501', 'en')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('public.customers', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM public.customers), 100000));

-- 测试夹具 2：员工（权限直接取自应用已有的 public.staff）
--   审核口径与订单收款一致：**任意在职员工**可确认充值到账（wallet.is_reviewer）
--   调账 / 退款更敏感：**仅 MANAGER**（wallet.can_adjust）
--   8001 MANAGER 在职，同时又是充值客户 -> 验证「不能审核自己的单」
--   9001 MANAGER 在职（财务）          9002 STAFF 在职（可审核、不可调账）
--   9003 STAFF  已停用（既不能审核、也不能调账）
INSERT INTO public.staff (id, telegram_user_id, login_name, password_hash, role, active)
VALUES (8001, 'tg-test-8001', 'test_staff_8001', 'x', 'MANAGER', true), (9001, 'tg-test-9001', 'test_staff_9001', 'x', 'MANAGER', true), (9002, 'tg-test-9002', 'test_staff_9002', 'x', 'STAFF', true), (9003, 'tg-test-9003', 'test_staff_9003', 'x', 'STAFF', false)
ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, active = EXCLUDED.active;
SELECT setval(pg_get_serial_sequence('public.staff', 'id'),
              GREATEST((SELECT COALESCE(max(id), 1) FROM public.staff), 100000));

-- 规则 1: 1000~2999 固定送 200，赠送金 30 天有效
-- 规则 2: >=3000 送 10%
-- 规则 3: 100~999 送 10%，赠送金永久有效（bonus_valid_days IS NULL）
INSERT INTO wallet.recharge_rules
  (id, name, currency, min_amount, max_amount, bonus_type, bonus_value,
   bonus_valid_days, priority, active)
VALUES (1, '满$10送$2',   'USD', 1000, 2999, 'fixed',   200,  30,   10, true),
       (2, '大额10%',     'USD', 3000, NULL, 'percent', 1000, 30,    5, true),
       (3, '小额10%永久', 'USD', 100,  999,  'percent', 1000, NULL,  1, true);
SELECT setval('wallet.recharge_rules_id_seq', 100);

-- 便捷断言
CREATE OR REPLACE FUNCTION public.t_ok(p_name TEXT, p_ok BOOLEAN, p_detail TEXT DEFAULT '')
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO public.test_results (name, ok, detail) VALUES (p_name, p_ok, p_detail);
END $$;

-- ===========================================================================
-- T1  创建充值单：金额/赠送快照/订单号/有效期
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(1001, 'USD', 1000, 'idem-t1');
  PERFORM public.t_ok('T1.1 订单创建', o.id IS NOT NULL);
  PERFORM public.t_ok('T1.2 订单号格式 RC+日期+8位', o.order_no ~ '^RC[0-9]{16}$', o.order_no);
  PERFORM public.t_ok('T1.3 命中满10送2规则', o.bonus_amount = 200, o.bonus_amount::text);
  PERFORM public.t_ok('T1.4 初始状态 awaiting_proof', o.status = 'awaiting_proof', o.status::text);
  PERFORM public.t_ok('T1.5 有有效期', o.expires_at > now());
END $$;

-- T1.8 赠送金永久有效（规则 bonus_valid_days IS NULL）
DO $$
DECLARE o wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(1001, 'USD', 500, 'idem-t1b');
  PERFORM public.t_ok('T1.8 小额规则赠送 10% = $0.5', o.bonus_amount = 50, o.bonus_amount::text);
  PERFORM public.t_ok('T1.9 永久赠送无到期时间', o.bonus_expire_at IS NULL);
END $$;

-- T1.6 幂等：同 idempotency_key 不产生第二张单
DO $$
DECLARE a wallet.recharge_orders; b wallet.recharge_orders; n INT;
BEGIN
  a := wallet.start_recharge(1001, 'USD', 1000, 'idem-t1');
  b := wallet.start_recharge(1001, 'USD', 1000, 'idem-t1');
  SELECT count(*) INTO n FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  PERFORM public.t_ok('T1.6 下单幂等（同 key 返回同单）', a.id = b.id AND n = 1, n::text);
END $$;

-- T1.7 金额低于下限被拒
DO $$
BEGIN
  BEGIN
    PERFORM wallet.start_recharge(1001, 'USD', 50, 'idem-t1-low');
    PERFORM public.t_ok('T1.7 低于最小充值额被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T1.7 低于最小充值额被拒', SQLERRM LIKE '%AMOUNT_TOO_SMALL%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T2  上传凭证
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; ord BIGINT;
BEGIN
  SELECT id INTO ord FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  o := wallet.submit_proof(ord, 1001, 'file_abc', 'uniq_abc', 'image/jpeg', 1024,
                           's3://proof/abc.jpg', repeat('a', 64), 'FT20261001');
  PERFORM public.t_ok('T2.1 上传后转 under_review', o.status = 'under_review', o.status::text);
  PERFORM public.t_ok('T2.2 proof_count = 1', o.proof_count = 1, o.proof_count::text);
  PERFORM public.t_ok('T2.3 记录转账流水号', o.pay_reference = 'FT20261001', o.pay_reference);
END $$;

-- T2.4 同一张图复用被拒（反作弊）
DO $$
DECLARE ord BIGINT;
BEGIN
  SELECT id INTO ord FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  BEGIN
    PERFORM wallet.submit_proof(ord, 1001, 'file_abc', 'uniq_abc');
    PERFORM public.t_ok('T2.4 重复凭证图片被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T2.4 重复凭证图片被拒', SQLERRM LIKE '%PROOF_DUPLICATE%', SQLERRM);
  END;
END $$;

-- T2.5 非本人不能上传
DO $$
DECLARE ord BIGINT;
BEGIN
  SELECT id INTO ord FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  BEGIN
    PERFORM wallet.submit_proof(ord, 2002, 'file_x', 'uniq_x');
    PERFORM public.t_ok('T2.5 他人代传凭证被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T2.5 他人代传凭证被拒', SQLERRM LIKE '%NOT_ORDER_OWNER%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T3  审核通过入账
-- ===========================================================================
DO $$
DECLARE ord BIGINT; w RECORD;
BEGIN
  SELECT id INTO ord FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  PERFORM wallet.approve_recharge(ord, 9001, NULL, 'ok');
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T3.1 本金入账 $10', w.principal = 1000, w.principal::text);
  PERFORM public.t_ok('T3.2 赠送入账 $2', w.bonus = 200, w.bonus::text);
  PERFORM public.t_ok('T3.3 赠送有到期时间', w.bonus_expire_at IS NOT NULL);
END $$;

-- T3.4 重复审核不重复入账（幂等）
DO $$
DECLARE ord BIGINT; w RECORD;
BEGIN
  SELECT id INTO ord FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t1';
  PERFORM wallet.approve_recharge(ord, 9001, NULL, 'again');
  PERFORM wallet.approve_recharge(ord, 9001, NULL, 'again2');
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T3.4 重复审核不重复入账', w.principal = 1000 AND w.bonus = 200,
                      jsonb_build_object('principal', w.principal, 'bonus', w.bonus)::text);
END $$;

-- T3.5 账本流水条数与内容
DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM wallet.ledger_entries WHERE customer_id = 1001;
  PERFORM public.t_ok('T3.5 恰好 2 条流水（本金+赠送）', n = 2, n::text);
END $$;

-- ===========================================================================
-- T4  权限
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; o2 wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(1001, 'USD', 1000, 'idem-t4');
  PERFORM wallet.submit_proof(o.id, 1001, 'file_t4', 'uniq_t4');

  BEGIN
    PERFORM wallet.approve_recharge(o.id, 9003, NULL);
    PERFORM public.t_ok('T4.1 已停用员工无审核权', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T4.1 已停用员工无审核权', SQLERRM LIKE '%NOT_REVIEWER%', SQLERRM);
  END;

  -- 8001 是 MANAGER，同时也是本单的用户 -> 自审必须被拒
  o2 := wallet.start_recharge(8001, 'USD', 1000, 'idem-t4-self');
  PERFORM wallet.submit_proof(o2.id, 8001, 'file_t4s', 'uniq_t4s');
  BEGIN
    PERFORM wallet.approve_recharge(o2.id, 8001, NULL);
    PERFORM public.t_ok('T4.2 不能审核自己的充值单', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T4.2 不能审核自己的充值单',
                        SQLERRM LIKE '%SELF_APPROVE_FORBIDDEN%', SQLERRM);
  END;

  -- 非员工（普通客户）尝试审核
  BEGIN
    PERFORM wallet.approve_recharge(o.id, 1001, NULL);
    PERFORM public.t_ok('T4.5 普通客户无审核权', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T4.5 普通客户无审核权', SQLERRM LIKE '%NOT_REVIEWER%', SQLERRM);
  END;

  PERFORM wallet.reject_recharge(o.id, 9001, '凭证模糊，请重新上传');
  SELECT * INTO o FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t4';
  PERFORM public.t_ok('T4.3 驳回后状态正确', o.status = 'rejected' AND o.reject_reason IS NOT NULL);

  BEGIN
    PERFORM wallet.reject_recharge(o.id, 9001, NULL);
    PERFORM public.t_ok('T4.4 驳回必须填原因', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T4.4 驳回必须填原因', SQLERRM LIKE '%ORDER_STATE%' OR SQLERRM LIKE '%REASON_REQUIRED%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- ===========================================================================
-- T4b 权限口径（与本项目 public.staff 对齐）
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(1099, 'USD', 1000, 'idem-t4b');
  PERFORM wallet.submit_proof(o.id, 1099, 'file_t4b', 'uniq_t4b');

  PERFORM wallet.approve_recharge(o.id, 9002, NULL);          -- 在职 STAFF
  SELECT * INTO o FROM wallet.recharge_orders WHERE idempotency_key = 'idem-t4b';
  PERFORM public.t_ok('T4.6 在职 STAFF 可确认充值到账', o.status = 'credited', o.status::text);
  PERFORM public.t_ok('T4.7 到账金额 = 本金 1000 + 赠送 200',
                      (wallet.get_summary(1099, 'USD')).total = 1200,
                      (wallet.get_summary(1099, 'USD')).total::text);

  BEGIN
    PERFORM wallet.adjust_balance(1099, 'USD', 'principal', 100, 9003, 'x', 'adj-t4b');
    PERFORM public.t_ok('T4.8 已停用员工不能调账', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T4.8 已停用员工不能调账', SQLERRM LIKE '%NOT_REVIEWER%', SQLERRM);
  END;
END $$;

-- T5  实收金额与订单不一致：按实收重算赠送
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; w RECORD;
BEGIN
  o := wallet.start_recharge(2001, 'USD', 5000, 'idem-t5');   -- $50 -> 命中「大额10%」
  PERFORM public.t_ok('T5.1 按比例规则算赠送 $5', o.bonus_amount = 500, o.bonus_amount::text);
  PERFORM wallet.submit_proof(o.id, 2001, 'file_t5', 'uniq_t5');
  PERFORM wallet.approve_recharge(o.id, 9001, 1000, '实收 $10');
  SELECT * INTO w FROM wallet.get_summary(2001, 'USD');
  PERFORM public.t_ok('T5.2 按实收 $10 入本金', w.principal = 1000, w.principal::text);
  PERFORM public.t_ok('T5.3 赠送按实收重算（$10 档 -> $2）', w.bonus = 200, w.bonus::text);
END $$;

-- ===========================================================================
-- T6  余额支付（赠送优先扣）
-- ===========================================================================
DO $$
DECLARE p wallet.order_payments; w RECORD;
BEGIN
  -- user 1001 现有 本金1000 + 赠送200，支付 500
  p := wallet.spend_balance(1001, 'USD', 500, 'ORD-0001', 'pay-ORD-0001', '买奶茶');
  PERFORM public.t_ok('T6.1 先扣赠送 200', p.bonus_used = 200, p.bonus_used::text);
  PERFORM public.t_ok('T6.2 再扣本金 300', p.principal_used = 300, p.principal_used::text);
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T6.3 余额更新为 700', w.total = 700, w.total::text);
END $$;

-- T6.4 支付幂等
DO $$
DECLARE p wallet.order_payments; w RECORD;
BEGIN
  p := wallet.spend_balance(1001, 'USD', 500, 'ORD-0001', 'pay-ORD-0001');
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T6.4 同订单重复支付不重复扣款', w.total = 700, w.total::text);
END $$;

-- T6.5 余额不足
DO $$
BEGIN
  BEGIN
    PERFORM wallet.spend_balance(1001, 'USD', 999999, 'ORD-0002', 'pay-ORD-0002');
    PERFORM public.t_ok('T6.5 余额不足被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T6.5 余额不足被拒', SQLERRM LIKE '%INSUFFICIENT_FUNDS%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T7  退款回钱包（按原拆分）
-- ===========================================================================
DO $$
DECLARE p wallet.order_payments; w RECORD;
BEGIN
  p := wallet.refund_payment('ORD-0001', 500, 'refund-ORD-0001', 9001, '客户取消');
  PERFORM public.t_ok('T7.1 退款状态 refunded', p.status = 'refunded', p.status);
  PERFORM public.t_ok('T7.2 退回赠送 200', p.refunded_bonus = 200, p.refunded_bonus::text);
  PERFORM public.t_ok('T7.3 退回本金 300', p.refunded_principal = 300, p.refunded_principal::text);
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T7.4 余额恢复 1200', w.total = 1200, w.total::text);
END $$;

-- T7.5 部分退款按比例退回
DO $$
DECLARE p wallet.order_payments; o wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(3001, 'USD', 1000, 'idem-t7');
  PERFORM wallet.submit_proof(o.id, 3001, 'file_t7', 'uniq_t7');
  PERFORM wallet.approve_recharge(o.id, 9001, NULL);      -- 1000 本金 + 200 赠送 = 1200
  PERFORM wallet.spend_balance(3001, 'USD', 600, 'ORD-0003', 'pay-ORD-0003');
  p := wallet.refund_payment('ORD-0003', 300, 'refund-ORD-0003', 9001, '部分退');
  PERFORM public.t_ok('T7.5 部分退款 50/50 拆分', p.refunded_bonus = 100 AND p.refunded_principal = 200,
                      jsonb_build_object('b', p.refunded_bonus, 'p', p.refunded_principal)::text);
  PERFORM public.t_ok('T7.6 部分退款状态', p.status = 'partially_refunded', p.status);
END $$;

-- T7.7 超额退款被拒
DO $$
BEGIN
  BEGIN
    PERFORM wallet.refund_payment('ORD-0003', 999, 'refund-over', 9001, 'x');
    PERFORM public.t_ok('T7.7 超额退款被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T7.7 超额退款被拒', SQLERRM LIKE '%BAD_REFUND%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T8  管理员调账
-- ===========================================================================
DO $$
DECLARE w RECORD;
BEGIN
  PERFORM wallet.adjust_balance(1001, 'USD', 'principal', 500, 9001, '线下补偿', 'adj-1');
  SELECT * INTO w FROM wallet.get_summary(1001, 'USD');
  PERFORM public.t_ok('T8.1 调账入账 500', w.total = 1700, w.total::text);

  BEGIN
    PERFORM wallet.adjust_balance(1001, 'USD', 'principal', 500, 9001, '', 'adj-2');
    PERFORM public.t_ok('T8.2 调账必须填原因', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T8.2 调账必须填原因', SQLERRM LIKE '%REASON_REQUIRED%', SQLERRM);
  END;

  BEGIN
    PERFORM wallet.adjust_balance(1001, 'USD', 'principal', 500, 9002, 'x', 'adj-3');
    PERFORM public.t_ok('T8.3 非 MANAGER 不能调账', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T8.3 非 MANAGER 不能调账', SQLERRM LIKE '%NOT_REVIEWER%', SQLERRM);
  END;

  BEGIN
    PERFORM wallet.adjust_balance(1001, 'USD', 'principal', -99999999, 9001, '扣光', 'adj-4');
    PERFORM public.t_ok('T8.4 调账不能把余额扣成负数', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T8.4 调账不能把余额扣成负数', SQLERRM LIKE '%INSUFFICIENT_FUNDS%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T9  账本不可篡改 + 对账一致
-- ===========================================================================
DO $$
BEGIN
  BEGIN
    UPDATE wallet.ledger_entries SET amount = 999999 WHERE id = (SELECT min(id) FROM wallet.ledger_entries);
    PERFORM public.t_ok('T9.1 账本禁止 UPDATE', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T9.1 账本禁止 UPDATE', SQLERRM LIKE '%LEDGER_IMMUTABLE%', SQLERRM);
  END;

  BEGIN
    DELETE FROM wallet.ledger_entries WHERE id = (SELECT min(id) FROM wallet.ledger_entries);
    PERFORM public.t_ok('T9.2 账本禁止 DELETE', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T9.2 账本禁止 DELETE', SQLERRM LIKE '%LEDGER_IMMUTABLE%', SQLERRM);
  END;
END $$;

DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM wallet.reconcile();
  PERFORM public.t_ok('T9.3 对账：余额 == 账本累加', n = 0, n::text);
END $$;

-- ===========================================================================
-- T10  风控限额
-- ===========================================================================
DO $$
DECLARE i INT; o wallet.recharge_orders;
BEGIN
  FOR i IN 1..5 LOOP
    BEGIN
      o := wallet.start_recharge(4001, 'USD', 1000, 'idem-t10-' || i);
    EXCEPTION WHEN others THEN
      EXIT;
    END;
  END LOOP;
  PERFORM public.t_ok('T10.1 未完成充值单数量受限 (<=3)',
    (SELECT count(*) FROM wallet.recharge_orders
      WHERE customer_id = 4001 AND status IN ('awaiting_proof','under_review')) <= 3);
END $$;

DO $$
BEGIN
  BEGIN
    PERFORM wallet.start_recharge(5001, 'USD', 60000, 'idem-t10-big');
    PERFORM public.t_ok('T10.2 超过单笔上限被拒', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T10.2 超过单笔上限被拒', SQLERRM LIKE '%AMOUNT_TOO_LARGE%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T11  过期处理
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; n INT;
BEGIN
  o := wallet.start_recharge(6001, 'USD', 1000, 'idem-t11');
  UPDATE wallet.recharge_orders SET expires_at = now() - interval '1 hour' WHERE id = o.id;
  n := wallet.expire_stale_orders();
  PERFORM public.t_ok('T11.1 过期单被批量置为 expired', n >= 1, n::text);
  BEGIN
    PERFORM wallet.submit_proof(o.id, 6001, 'file_t11', 'uniq_t11');
    PERFORM public.t_ok('T11.2 过期单不能上传凭证', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T11.2 过期单不能上传凭证', SQLERRM LIKE '%ORDER_EXPIRED%' OR SQLERRM LIKE '%ORDER_STATE%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T12  赠送金到期清零
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; w RECORD; cleared BIGINT;
BEGIN
  o := wallet.start_recharge(7001, 'USD', 1000, 'idem-t12');
  PERFORM wallet.submit_proof(o.id, 7001, 'file_t12', 'uniq_t12');
  PERFORM wallet.approve_recharge(o.id, 9001, NULL);
  -- 把该用户的赠送批次改成已过期（真实场景由时间自然流逝触发）
  UPDATE wallet.bonus_lots SET expire_at = now() - interval '1 minute' WHERE customer_id = 7001;
  cleared := wallet.expire_bonus(7001, 'USD');
  SELECT * INTO w FROM wallet.get_summary(7001, 'USD');
  PERFORM public.t_ok('T12.1 到期赠送批次被清零', cleared = 200 AND w.bonus = 0,
                      jsonb_build_object('cleared', cleared, 'bonus', w.bonus)::text);
  PERFORM public.t_ok('T12.2 本金不受影响', w.principal = 1000, w.principal::text);
  PERFORM public.t_ok('T12.3 批次剩余归零', NOT EXISTS (
      SELECT 1 FROM wallet.bonus_lots WHERE customer_id = 7001 AND remaining > 0));
END $$;

-- ===========================================================================
-- T13  赠送金批次：永久赠送与限时赠送共存（这是单到期日模型会算错的地方）
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; w RECORD; lots RECORD;
BEGIN
  -- 1) 先给一笔「永久」赠送 100
  PERFORM wallet.adjust_balance(8002, 'USD', 'bonus', 100, 9001, '永久赠送', 't13-perm');
  -- 2) 再通过充值拿到一笔 30 天有效的赠送 200
  o := wallet.start_recharge(8002, 'USD', 1000, 'idem-t13');
  PERFORM wallet.submit_proof(o.id, 8002, 'file_t13', 'uniq_t13');
  PERFORM wallet.approve_recharge(o.id, 9001, NULL);

  SELECT * INTO w FROM wallet.get_summary(8002, 'USD');
  PERFORM public.t_ok('T13.1 赠送合计 300', w.bonus = 300, w.bonus::text);
  PERFORM public.t_ok('T13.2 展示的到期时间 = 最近批次（限时那笔）',
                      w.bonus_expire_at IS NOT NULL AND w.bonus_expire_at > now(),
                      COALESCE(w.bonus_expire_at::text, 'NULL'));
  PERFORM public.t_ok('T13.3 永久批次没有被改成会过期', EXISTS (
      SELECT 1 FROM wallet.bonus_lots WHERE customer_id = 8002 AND expire_at IS NULL AND remaining = 100));

  -- 3) 扣款应优先扣「快过期」的批次
  PERFORM wallet.spend_balance(8002, 'USD', 150, 'ORD-T13', 'pay-ORD-T13');
  SELECT remaining INTO lots FROM wallet.bonus_lots
   WHERE customer_id = 8002 AND expire_at IS NOT NULL;
  PERFORM public.t_ok('T13.4 先扣限时批次（200-150=50）', lots.remaining = 50, lots.remaining::text);
  SELECT remaining INTO lots FROM wallet.bonus_lots
   WHERE customer_id = 8002 AND expire_at IS NULL;
  PERFORM public.t_ok('T13.5 永久批次未被动用（仍 100）', lots.remaining = 100, lots.remaining::text);

  -- 4) 让限时批次到期：只清限时，永久保留
  UPDATE wallet.bonus_lots SET expire_at = now() - interval '1 minute'
   WHERE customer_id = 8002 AND expire_at IS NOT NULL;
  PERFORM wallet.expire_bonus(8002, 'USD');
  SELECT * INTO w FROM wallet.get_summary(8002, 'USD');
  PERFORM public.t_ok('T13.6 到期只清限时批次 50，永久 100 保留',
                      w.bonus = 100, w.bonus::text);
  PERFORM public.t_ok('T13.7 永久批次的展示到期时间回到 NULL',
                      w.bonus_expire_at IS NULL, COALESCE(w.bonus_expire_at::text, 'NULL'));
END $$;

-- T13.8 批次剩余额之和必须等于钱包赠送余额
DO $$
DECLARE n INT;
BEGIN
  SELECT count(*) INTO n FROM wallet.reconcile() WHERE bucket = 'bonus_lots';
  PERFORM public.t_ok('T13.8 对账：批次合计 == 赠送余额', n = 0, n::text);
END $$;

-- ===========================================================================
-- T14  审核前暂存「实收金额」 + 禁止驳回自己的单
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; w RECORD; r wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(9101, 'USD', 1000, 'idem-t14');
  PERFORM wallet.submit_proof(o.id, 9101, 'file_t14', 'uniq_t14');

  -- 非审核员不能改实收
  BEGIN
    PERFORM wallet.set_received_amount(o.id, 9101, 1200);
    PERFORM public.t_ok('T14.1 非审核员不能改实收金额', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T14.1 非审核员不能改实收金额', SQLERRM LIKE '%NOT_REVIEWER%', SQLERRM);
  END;

  -- 管理员暂存实收 1200
  r := wallet.set_received_amount(o.id, 9001, 1200);
  PERFORM public.t_ok('T14.2 实收金额已暂存', r.pending_received_amount = 1200,
                      COALESCE(r.pending_received_amount::text, 'NULL'));

  -- 暂存的实收会覆盖订单金额，且可跨进程/重启存活（存在表里，不是内存）
  r := wallet.approve_recharge(o.id, 9001, NULL);
  SELECT * INTO w FROM wallet.get_summary(9101, 'USD');
  PERFORM public.t_ok('T14.3 按暂存实收入账 1200', w.principal = 1200, w.principal::text);
  PERFORM public.t_ok('T14.4 赠送按实收 1200 重算', w.bonus = 200, w.bonus::text);
  PERFORM public.t_ok('T14.5 received_amount 落库为终值', r.received_amount = 1200,
                      COALESCE(r.received_amount::text, 'NULL'));

  -- 已入账的单不能再改实收
  BEGIN
    PERFORM wallet.set_received_amount(o.id, 9001, 500);
    PERFORM public.t_ok('T14.6 已入账单不能改实收', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T14.6 已入账单不能改实收', SQLERRM LIKE '%ORDER_STATE%', SQLERRM);
  END;
END $$;

-- T14.7 不能驳回自己的充值单（8001 是 MANAGER）
DO $$
DECLARE o wallet.recharge_orders;
BEGIN
  o := wallet.start_recharge(8001, 'USD', 1000, 'idem-t14-self');
  PERFORM wallet.submit_proof(o.id, 8001, 'file_t14s', 'uniq_t14s');
  BEGIN
    PERFORM wallet.reject_recharge(o.id, 8001, '我自己驳回试试');
    PERFORM public.t_ok('T14.7 不能驳回自己的充值单', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T14.7 不能驳回自己的充值单',
                        SQLERRM LIKE '%SELF_APPROVE_FORBIDDEN%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- T15  用户自行取消充值单
-- ===========================================================================
DO $$
DECLARE o wallet.recharge_orders; o2 wallet.recharge_orders;
BEGIN
  -- 占满未完成单额度（max_open_orders 默认 3）
  PERFORM wallet.start_recharge(9501, 'USD', 1000, 'idem-t15-a');
  PERFORM wallet.start_recharge(9501, 'USD', 1000, 'idem-t15-b');
  o := wallet.start_recharge(9501, 'USD', 1000, 'idem-t15-c');

  BEGIN
    PERFORM wallet.start_recharge(9501, 'USD', 1000, 'idem-t15-d');
    PERFORM public.t_ok('T15.1 未完成单占满后被限制', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T15.1 未完成单占满后被限制', SQLERRM LIKE '%TOO_MANY_OPEN_ORDERS%', SQLERRM);
  END;

  -- 取消一张后应恢复可下单
  o := wallet.cancel_recharge(o.id, 9501);
  PERFORM public.t_ok('T15.2 取消后状态为 cancelled', o.status = 'cancelled', o.status::text);

  o2 := wallet.start_recharge(9501, 'USD', 1000, 'idem-t15-d');
  PERFORM public.t_ok('T15.3 取消后可再次下单', o2.id IS NOT NULL);

  -- 幂等
  o2 := wallet.cancel_recharge(o.id, 9501);
  PERFORM public.t_ok('T15.4 重复取消幂等', o2.status = 'cancelled');

  -- 不能取消别人的单
  BEGIN
    PERFORM wallet.cancel_recharge(o2.id, 7777);
    PERFORM public.t_ok('T15.5 不能取消别人的单', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T15.5 不能取消别人的单', SQLERRM LIKE '%NOT_ORDER_OWNER%', SQLERRM);
  END;

  -- 已提交凭证的单不能自行取消
  BEGIN
    PERFORM wallet.submit_proof(o2.id, 9501, 'file_t15', 'uniq_t15');
    PERFORM wallet.cancel_recharge(o2.id, 9501);
    PERFORM public.t_ok('T15.6 已提交凭证不能自行取消', false, '未报错');
  EXCEPTION WHEN others THEN
    PERFORM public.t_ok('T15.6 已提交凭证不能自行取消', SQLERRM LIKE '%ORDER_STATE%', SQLERRM);
  END;
END $$;

-- ===========================================================================
-- 汇总
-- ===========================================================================
DO $$
DECLARE fails INT; total INT;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE NOT ok) INTO total, fails FROM public.test_results;
  RAISE NOTICE '--------------------------------------------';
  RAISE NOTICE '用例总数: %, 失败: %', total, fails;
  IF fails > 0 THEN
    RAISE EXCEPTION 'TEST FAILED: % 个用例未通过（见 test_results 表）', fails;
  END IF;
  RAISE NOTICE 'ALL TESTS PASSED (% 项)', total;
END $$;

\echo '==== 失败用例（应为空）===='
SELECT seq, name, detail FROM public.test_results WHERE NOT ok ORDER BY seq;
\echo '==== 全部用例 ===='
SELECT seq, name, ok FROM public.test_results ORDER BY seq;
