#!/usr/bin/env python3
"""Tea+Cafe 钱包 —— 并发正确性测试。

在真实并发连接下验证资金安全：重复审核、重复扣款、超卖、重复下单、重复退款。

用法:
    python3 concurrency_test.py [PGHOST] [PGUSER] [PGDATABASE]
默认: PGHOST=../.pgdata  PGDATABASE=postgres  PGUSER=postgres
"""
import os
import sys
import threading

import psycopg2
import psycopg2.extras

HOST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".pgdata")
USER = sys.argv[2] if len(sys.argv) > 2 else "postgres"
DB = sys.argv[3] if len(sys.argv) > 3 else "postgres"

RESULTS = []


def connect():
    c = psycopg2.connect(host=HOST, user=USER, dbname=DB)
    c.autocommit = False
    return c


def run_many(n, fn):
    """并发跑 n 次 fn(i)，返回 (成功数, 成功结果列表, 异常列表)。"""
    barrier = threading.Barrier(n)
    ok, oks, errs = [], [], []

    def worker(i):
        try:
            barrier.wait(timeout=30)
            r = fn(i)
            oks.append(r)
            ok.append(i)
        except Exception as exc:  # noqa: BLE001
            errs.append("%s: %s" % (type(exc).__name__, str(exc).strip().splitlines()[0]))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return len(ok), oks, errs


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def setup(conn):
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE wallet.ledger_entries, wallet.order_payments, wallet.recharge_proofs,
                     wallet.recharge_orders, wallet.bonus_lots, wallet.wallets, wallet.audit_log,
                     wallet.recharge_rules RESTART IDENTITY CASCADE
        """)
        # 客户夹具：wallet.* 对 public.customers 有外键
        cur.execute("""
            INSERT INTO public.customers (id, telegram_user_id, display_name)
            VALUES (1001, 'tg-conc-1001', 'conc 1001'), (2001, 'tg-conc-2001', 'conc 2001'), (3001, 'tg-conc-3001', 'conc 3001')
            ON CONFLICT (id) DO NOTHING
        """)
        cur.execute("SELECT setval(pg_get_serial_sequence('public.customers','id'), 100000)")
        # 员工夹具：9001 = MANAGER（可审核、可调账）
        cur.execute("""
            INSERT INTO public.staff (id, telegram_user_id, login_name, password_hash, role, active)
            VALUES (9001, 'tg-conc-staff-9001', 'conc_staff_9001', 'x', 'MANAGER', true)
            ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, active = true
        """)
        cur.execute("SELECT setval(pg_get_serial_sequence('public.staff','id'), 100000)")
        cur.execute("""
            INSERT INTO wallet.recharge_rules
              (id, name, currency, min_amount, bonus_type, bonus_value, bonus_valid_days, priority, active)
            VALUES (1, '满$10送$2', 'USD', 1000, 'fixed', 200, 30, 10, true)
        """)
    conn.commit()


def summary(cur, user_id):
    cur.execute("SELECT principal, bonus FROM wallet.get_summary(%s,'USD')", (user_id,))
    r = cur.fetchone()
    return (r[0], r[1]) if r else (0, 0)


# ---------------------------------------------------------------- 场景 1
def scenario_double_approve(conn):
    print("\n[1] 8 个线程同时审核同一张充值单 -> 只能入账一次")
    with conn.cursor() as cur:
        cur.execute("SELECT (wallet.start_recharge(1001,'USD',1000,'c1')).id")
        oid = cur.fetchone()[0]
        cur.execute("SELECT wallet.submit_proof(%s,1001,'f1','u1')", (oid,))
        # 清空余额，便于观察
        cur.execute("DELETE FROM wallet.order_payments")
    conn.commit()

    def approve(i):
        c = connect()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (wallet.approve_recharge(%s,9001)).status", (oid,))
                s = cur.fetchone()[0]
            c.commit()
            return s
        finally:
            c.close()

    n_ok, oks, errs = run_many(8, approve)
    with conn.cursor() as cur:
        p, b = summary(cur, 1001)
        cur.execute("SELECT count(*) FROM wallet.ledger_entries WHERE biz_id LIKE 'RC%'")
        entries = cur.fetchone()[0]
        cur.execute("SELECT status FROM wallet.recharge_orders WHERE id=%s", (oid,))
        st = cur.fetchone()[0]
    check("余额只入账一次 (本金 1000 / 赠送 200)", (p, b) == (1000, 200), "实际 %s" % ((p, b),))
    check("账本只有 2 条入账流水", entries == 2, "实际 %d" % entries)
    check("8 个并发调用全部成功返回（幂等）", n_ok == 8, "成功 %d, 异常 %s" % (n_ok, errs))
    check("订单状态为 credited", st == "credited", st)


# ---------------------------------------------------------------- 场景 2
def scenario_double_spend(conn):
    print("\n[2] 8 个线程同时用同一业务订单号扣款 -> 只能扣一次")
    with conn.cursor() as cur:
        cur.execute("SELECT principal, bonus FROM wallet.get_summary(1001,'USD')")
        p0, b0 = cur.fetchone()

    def pay(i):
        c = connect()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (wallet.spend_balance(1001,'USD',300,'ORDER-X','pay-ORDER-X')).id")
                pid = cur.fetchone()[0]
            c.commit()
            return pid
        finally:
            c.close()

    n_ok, oks, errs = run_many(8, pay)
    with conn.cursor() as cur:
        p, b = summary(cur, 1001)
        cur.execute("SELECT count(*) FROM wallet.ledger_entries WHERE biz_id='ORDER-X'")
        n = cur.fetchone()[0]
        cur.execute("SELECT DISTINCT id FROM wallet.order_payments WHERE biz_id='ORDER-X'")
        pids = [r[0] for r in cur.fetchall()]
    check("只扣一次 300（赠送 200 + 本金 100）", (p, b) == (p0 - 100, b0 - 200),
          "实际 %s -> %s" % ((p0, b0), (p, b)))
    check("账本只有 2 条扣款流水", n == 2, "实际 %d" % n)
    check("8 个并发调用全部成功且返回同一支付记录", n_ok == 8 and len(pids) == 1,
          "成功 %d, 支付记录 %s, 异常 %s" % (n_ok, pids, errs))


# ---------------------------------------------------------------- 场景 3
def scenario_oversell(conn):
    print("\n[3] 余额只够 3 次，8 个线程并发各扣一次 -> 恰好 3 次成功且不为负")

    def pay(i):
        c = connect()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (wallet.spend_balance(2001,'USD',100,'ORDER-C%d','pay-C%d')).id"
                            % (i, i))
                pid = cur.fetchone()[0]
            c.commit()
            return pid
        finally:
            c.close()

    # 给 2001 造 300 余额（无赠送），余额刚好够 3 次 100。
    # 必须走 adjust_balance 而不是直接 UPDATE wallets，否则账本与余额会不一致。
    with conn.cursor() as cur:
        cur.execute("SELECT wallet.adjust_balance(2001,'USD','principal',300,9001,'并发测试预置余额','c3-seed')")
    conn.commit()

    n_ok, oks, errs = run_many(8, pay)
    with conn.cursor() as cur:
        p, b = summary(cur, 2001)
        cur.execute("SELECT count(*) FROM wallet.order_payments WHERE customer_id=2001")
        n = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM wallet.reconcile()")
        drift = cur.fetchone()[0]
    check("恰好 3 次扣款成功", n_ok == 3 and n == 3, "成功 %d, 记录 %d" % (n_ok, n))
    check("余额扣到 0，绝不为负", p == 0, "实际本金 %s" % p)
    check("失败的都是 INSUFFICIENT_FUNDS", all("INSUFFICIENT_FUNDS" in e for e in errs),
          "%d 个异常: %s" % (len(errs), errs[:2]))
    check("对账无差异", drift == 0, "差异 %d" % drift)


# ---------------------------------------------------------------- 场景 4
def scenario_idem_order(conn):
    print("\n[4] 8 个线程用同一 idempotency_key 下单 -> 只能生成一张单")

    def order(i):
        c = connect()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (wallet.start_recharge(3001,'USD',1000,'same-key')).id")
                oid = cur.fetchone()[0]
            c.commit()
            return oid
        finally:
            c.close()

    n_ok, oks, errs = run_many(8, order)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM wallet.recharge_orders WHERE customer_id=3001")
        n = cur.fetchone()[0]
    check("只生成 1 张充值单", n == 1, "实际 %d" % n)
    check("所有调用返回同一订单 id", len(set(oks)) == 1, "不同 id 数 %d, 异常 %s"
          % (len(set(oks)), errs))


# ---------------------------------------------------------------- 场景 5
def scenario_double_refund(conn):
    print("\n[5] 8 个线程并发退款同一笔支付 -> 只能退一次")
    with conn.cursor() as cur:
        cur.execute("SELECT principal, bonus FROM wallet.get_summary(1001,'USD')")
        p0, b0 = cur.fetchone()

    def refund(i):
        c = connect()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT (wallet.refund_payment('ORDER-X',300,'refund-X',9001,'重复退款尝试')).id")
                rid = cur.fetchone()[0]
            c.commit()
            return rid
        finally:
            c.close()

    n_ok, oks, errs = run_many(8, refund)
    with conn.cursor() as cur:
        p, b = summary(cur, 1001)
        cur.execute("SELECT refunded_amount, status FROM wallet.order_payments WHERE biz_id='ORDER-X'")
        ra, st = cur.fetchone()
        cur.execute("SELECT count(*) FROM wallet.reconcile()")
        drift = cur.fetchone()[0]
    check("只退一次 300", (p, b) == (p0 + 100, b0 + 200), "实际 %s -> %s" % ((p0, b0), (p, b)))
    check("退款状态 refunded，退款额 300", (ra, st) == (300, "refunded"), "%s / %s" % (ra, st))
    check("8 个调用全部成功返回", n_ok == 8, "成功 %d, 异常 %s" % (n_ok, errs))
    check("对账无差异", drift == 0, "差异 %d" % drift)


def main():
    conn = connect()
    try:
        setup(conn)
        scenario_double_approve(conn)
        scenario_double_spend(conn)
        scenario_oversell(conn)
        scenario_idem_order(conn)
        scenario_double_refund(conn)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM wallet.reconcile()")
            drift = cur.fetchone()[0]
            cur.execute("""
                SELECT count(*) FROM wallet.wallets
                 WHERE principal < 0 OR bonus < 0 OR frozen < 0
            """)
            neg = cur.fetchone()[0]
        check("最终对账无差异", drift == 0, "差异 %d" % drift)
        check("最终无负余额钱包", neg == 0, "负数钱包 %d" % neg)
    finally:
        conn.close()

    fails = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print("并发用例: %d, 失败: %d" % (len(RESULTS), len(fails)))
    if fails:
        for n, _, d in fails:
            print("  FAIL %s | %s" % (n, d))
        sys.exit(1)
    print("ALL CONCURRENCY TESTS PASSED")


if __name__ == "__main__":
    main()
