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

    # ---- review 发现的几处越权，逐条盯着 ----
    # ① 支付截图：别家店的截图不能读（截图里有金额、账号）。
    #    先真的给主店订单传一张凭证，否则 404 可能只是「本来就没有凭证」。
    tg_secret = os.getenv("WEBHOOK_SECRET", "")
    webhook_headers = {"X-Telegram-Bot-Api-Secret-Token": tg_secret}
    main_manager.post(f"/api/v1/admin/orders/{main_order_id}/status", json={"status": "ACCEPTED"})
    httpx.post(f"{BASE}/api/v1/telegram/webhook", timeout=25, headers=webhook_headers,
               json={"message": {"from": {"id": 555000111, "language_code": "en"},
                                 "chat": {"id": 555000111, "type": "private"},
                                 "text": f"/start pay_{main_order['public_code']}"}})
    httpx.post(f"{BASE}/api/v1/telegram/webhook", timeout=25, headers=webhook_headers,
               json={"message": {"from": {"id": 555000111},
                                 "chat": {"id": 555000111, "type": "private"},
                                 "photo": [{"file_id": f"XS_{RUN}", "file_unique_id": f"XSU_{RUN}",
                                            "width": 90, "height": 90}]}})
    proof_scope = branch_manager.get(f"/api/v1/admin/orders/{main_order_id}/payment-proof")
    check("二号店读主店支付截图 -> 404", proof_scope.status_code == 404,
          f"{proof_scope.status_code} {proof_scope.text[:60]}")
    own_proof = main_manager.get(f"/api/v1/admin/orders/{main_order_id}/payment-proof")
    check("主店读本店支付截图不是 404（确实是门店校验而不是一律拒绝）",
          own_proof.status_code != 404, f"{own_proof.status_code} {own_proof.text[:60]}")

    # ② 财务统计：本店下单只影响本店数字
    def volume(client_) -> int:
        return client_.get("/api/v1/admin/analytics/").json()["order_volume"]

    main_before, branch_before = volume(main_manager), volume(branch_manager)
    place_order(branch_cust, "branch2")
    place_order(branch_cust, "branch3")
    check("二号店新增订单只加二号店的统计", volume(branch_manager) == branch_before + 2,
          f"{branch_before} -> {volume(branch_manager)}")
    check("主店统计不受二号店影响", volume(main_manager) == main_before,
          f"{main_before} -> {volume(main_manager)}")

    # ③ 门店设置：改本店不影响别店
    main_settings = main_manager.get("/api/v1/admin/settings").json()
    branch_settings = branch_manager.get("/api/v1/admin/settings").json()   # 自己那份
    check("主店设置能读到自己的门店码", main_settings.get("store_code") == "MAIN",
          str(main_settings.get("store_code")))
    check("二号店读到自己那份设置", branch_settings.get("store_code") == "ST02",
          str(branch_settings.get("store_code")))
    # 基于**自己**的原值改，而不是把主店的整份配置合并过来
    # （合并会顺带把主店的群 ID 抄到二号店，导致日报重复投递）
    branch_manager.patch("/api/v1/admin/settings",
                         json={**branch_settings, "payment_link": "https://pay.example.com/st02"})
    branch_now = branch_manager.get("/api/v1/admin/settings").json()
    main_now = main_manager.get("/api/v1/admin/settings").json()
    check("二号店改自己的收款链接生效",
          branch_now.get("payment_link") == "https://pay.example.com/st02",
          str(branch_now.get("payment_link")))
    check("主店收款链接没被覆盖", main_now.get("payment_link") == main_settings.get("payment_link"),
          f'{main_settings.get("payment_link")} -> {main_now.get("payment_link")}')
    check("二号店的群 ID 没有被主店配置污染",
          branch_now.get("telegram_staff_group_id") == branch_settings.get("telegram_staff_group_id"),
          f'{branch_settings.get("telegram_staff_group_id")} -> {branch_now.get("telegram_staff_group_id")}')
    check("二号店不能用 ?store=MAIN 改别家店",
          branch_manager.get("/api/v1/admin/settings?store=MAIN").status_code == 404,
          str(branch_manager.get("/api/v1/admin/settings?store=MAIN").status_code))
    # 完整恢复
    branch_manager.patch("/api/v1/admin/settings", json=branch_settings)

    # ④ 店员管理：不能建别店的员工，也不能改别店员工
    r = branch_manager.post("/api/v1/admin/staff",
                            json={"login_name": f"xs_cross_{RUN}", "password": "xs-password-1234",
                                  "role": "STAFF", "store_id": 1})
    check("二号店建主店员工 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:60]}")
    check("二号店改主店员工 -> 404",
          branch_manager.patch("/api/v1/admin/staff/1", json={"role": "STAFF"}).status_code == 404,
          str(branch_manager.patch("/api/v1/admin/staff/1", json={"role": "STAFF"}).status_code))

    # ⑤ 审计日志：看不到别店的设置变更
    main_manager.patch("/api/v1/admin/settings", json=main_settings)
    branch_audit = branch_manager.get("/api/v1/admin/audit-logs").json()
    leaked = [i for i in branch_audit["items"]
              if (i.get("details") or {}).get("store_code") == "MAIN"]
    check("二号店审计里看不到主店的操作", not leaked,
          f"{len(leaked)} 条 / 共 {branch_audit['total']}")

    # 运维任务：过期清理按店，对账只给总部
    hq_manager = client("staff", 9003)      # store_id 为空 = 总部
    r = main_manager.post("/api/v1/admin/wallet/maintenance", json={})
    body = r.json() if r.status_code == 200 else {}
    check("门店经理运维只覆盖本店", r.status_code == 200 and body.get("scope") == "STORE:1",
          f"{r.status_code} {body.get('scope')}")
    check("门店账号拿不到全局对账", body.get("reconcile_drift") is None,
          str(body.get("reconcile_drift"))[:40])
    r2 = branch_manager.post("/api/v1/admin/wallet/maintenance", json={})
    check("二号店运维只覆盖二号店", r2.status_code == 200 and r2.json().get("scope") == "STORE:2",
          f"{r2.status_code} {r2.json().get('scope') if r2.status_code == 200 else r2.text[:40]}")
    r3 = hq_manager.post("/api/v1/admin/wallet/maintenance", json={})
    check("总部可以跨店运维并对账",
          r3.status_code == 200 and r3.json().get("scope") == "ALL"
          and r3.json().get("reconcile_drift") is not None,
          f"{r3.status_code} {r3.json().get('scope') if r3.status_code == 200 else r3.text[:40]}")

    # 新建员工的归属默认继承创建者（否则默认变成总部账号 = 静默提权）
    created = main_manager.post("/api/v1/admin/staff",
                                json={"login_name": f"xs_{RUN}", "password": "xs-password-1234",
                                      "role": "STAFF"})
    check("新建员工成功", created.status_code == 200, f"{created.status_code} {created.text[:60]}")
    if created.status_code == 200:
        staff_list = main_manager.get("/api/v1/admin/staff").json()
        new_row = next((s for s in staff_list if s["login_name"] == f"xs_{RUN}"), None)
        check("新员工归属继承创建者门店（不是总部）",
              new_row is not None and new_row.get("store_id") == 1,
              str(new_row.get("store_id") if new_row else "未找到"))

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
