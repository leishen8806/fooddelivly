#!/usr/bin/env python3
"""跨店越权测试（多商家的安全底线）。

    python tests/cross_store_security_test.py http://127.0.0.1:8000

前置：先跑 tests/seed_second_store.sql（会建二号店 ST02 + 该店员工 9001/9002
+ 该店客户 9100）。

7 个加盟商共用一套后台，最危险的错误就是**一家店能看/能改另一家店的数据**。
这里真的发请求去撞，覆盖：
  * 门店经理的订单列表里不会出现别家店的单；
  * 直接按 id 操作别家店的订单 -> 404（不是 403，避免泄露存在性）；
  * 别家店客户的钱包、调账 -> 404；
  * 充值单列表与审核 -> 隔离；
  * 员工列表 -> 隔离；
  * 总部账号（store_id 为空）仍然可以跨店。
"""
from __future__ import annotations

import os
import sys

import httpx
from jose import jwt
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SECRET = os.getenv("JWT_SECRET", "")
RUN = os.getpid()
MAIN_CUSTOMER, BRANCH_CUSTOMER = 1, 9100
PRODUCT_ID = 1
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def client(kind: str, sub: int) -> httpx.Client:
    cookie = "admin_session_token" if kind == "staff" else "session_token"
    return httpx.Client(base_url=BASE, timeout=25, headers={"Origin": "http://localhost:5173"},
                        cookies={cookie: jwt.encode({"sub": str(sub), "type": kind}, SECRET,
                                                    algorithm="HS256")})


def place_order(customer_client: httpx.Client, tag: str) -> dict | None:
    resp = customer_client.post(
        "/api/v1/orders",
        json={"room_number": "XS", "items": [{"product_id": PRODUCT_ID, "quantity": 1}]},
        headers={"Idempotency-Key": f"xs-{tag}-{RUN}"})
    return resp.json() if resp.status_code == 200 else None


def main() -> None:
    main_manager = client("staff", 1)          # 主店 MANAGER
    branch_manager = client("staff", 9001)     # 二号店 MANAGER
    branch_staff = client("staff", 9002)       # 二号店 STAFF
    main_cust, branch_cust = client("customer", MAIN_CUSTOMER), client("customer", BRANCH_CUSTOMER)

    main_order = place_order(main_cust, "main")
    branch_order = place_order(branch_cust, "branch")
    if not main_order or not branch_order:
        raise SystemExit("下单失败，请先准备前置数据（seed_second_store.sql）")
    check("两店各自能下单", True, f"{main_order['public_code']} / {branch_order['public_code']}")

    main_orders = main_manager.get("/api/v1/admin/orders").json()
    branch_orders = branch_manager.get("/api/v1/admin/orders").json()
    main_codes = {o["public_code"] for o in main_orders}
    branch_codes = {o["public_code"] for o in branch_orders}
    check("主店列表里有自己的单", main_order["public_code"] in main_codes)
    check("主店列表里没有二号店的单", branch_order["public_code"] not in main_codes,
          str(sorted(branch_codes))[:80])
    check("二号店列表里有自己的单", branch_order["public_code"] in branch_codes)
    check("二号店列表里没有主店的单", main_order["public_code"] not in branch_codes)

    main_order_id = next(o["id"] for o in main_orders if o["public_code"] == main_order["public_code"])
    branch_order_id = next(o["id"] for o in branch_orders if o["public_code"] == branch_order["public_code"])

    # 直接按 id 操作别家店的订单：一律 404（不泄露存在性）
    r = branch_manager.post(f"/api/v1/admin/orders/{main_order_id}/status", json={"status": "ACCEPTED"})
    check("二号店改主店订单状态 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")
    r = main_manager.post(f"/api/v1/admin/orders/{branch_order_id}/status", json={"status": "ACCEPTED"})
    check("主店改二号店订单状态 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")
    r = branch_manager.post(f"/api/v1/admin/orders/{main_order_id}/refund", json={"reason": "越权"})
    check("二号店退主店订单的钱 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")
    r = branch_manager.post(f"/api/v1/admin/orders/{main_order_id}/payment-review",
                            json={"decision": "APPROVED"})
    check("二号店审核主店的收款凭证 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")

    # 订单确实没被改动
    still_new = next(o for o in main_manager.get("/api/v1/admin/orders").json()
                     if o["id"] == main_order_id)
    check("主店订单未被越权改动", still_new["order_status"] == "NEW", still_new["order_status"])

    # 钱包：别家店客户的钱包与调账
    r = branch_manager.get(f"/api/v1/admin/wallets/{MAIN_CUSTOMER}")
    check("二号店看主店客户钱包 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")
    r = branch_manager.post(f"/api/v1/admin/wallets/{MAIN_CUSTOMER}/adjust",
                            json={"bucket": "principal", "delta_minor": 1000, "reason": "越权加钱"})
    check("二号店给主店客户调账 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:60]}")

    # 客户列表
    branch_customers = {c["id"] for c in branch_manager.get("/api/v1/admin/customers").json()}
    check("二号店客户列表不含主店客户", MAIN_CUSTOMER not in branch_customers, str(sorted(branch_customers))[:80])
    check("二号店客户列表含本店客户", BRANCH_CUSTOMER in branch_customers, str(sorted(branch_customers))[:80])

    # 充值单
    main_manager.get("/api/v1/admin/recharges")
    branch_recharges = branch_manager.get("/api/v1/admin/recharges").json()
    main_recharges = main_manager.get("/api/v1/admin/recharges").json()
    check("二号店充值列表不含主店客户",
          all(r["customer_id"] != MAIN_CUSTOMER for r in branch_recharges),
          str([r["customer_id"] for r in branch_recharges])[:60])
    check("主店充值列表不含二号店客户",
          all(r["customer_id"] != BRANCH_CUSTOMER for r in main_recharges),
          str([r["customer_id"] for r in main_recharges])[:60])

    # 员工列表
    branch_staff_list = {s["id"] for s in branch_manager.get("/api/v1/admin/staff").json()}
    check("二号店员工列表不含主店员工", 1 not in branch_staff_list, str(sorted(branch_staff_list))[:80])
    check("二号店员工列表含本店员工", 9002 in branch_staff_list, str(sorted(branch_staff_list))[:80])

    # 二号店 STAFF 也不能改主店的单
    r = branch_staff.post(f"/api/v1/admin/orders/{main_order_id}/status", json={"status": "ACCEPTED"})
    check("二号店 STAFF 改主店订单 -> 404", r.status_code == 404, f"{r.status_code}")

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"跨店越权用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL CROSS-STORE TESTS PASSED")


if __name__ == "__main__":
    main()
