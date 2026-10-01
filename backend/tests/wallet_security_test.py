#!/usr/bin/env python3
"""钱包 / 充值 —— 安全边界测试（**真的发攻击请求**，不是读代码下的结论）。

用法（需要先起服务 + 灌 tests/seed_wallet_fixture.sql）：
    python tests/wallet_security_test.py http://127.0.0.1:8000

覆盖的攻击面：
  1. 未认证访问所有钱包端点
  2. 越权：客户访问员工端点、STAFF 访问 MANAGER 端点
  3. 水平越权（IDOR）：客户 A 读/取消客户 B 的充值单
  4. 金额篡改：0 / 负数 / 超限 / 非整数 / 字符串
  5. 幂等键跨客户串单
  6. Telegram 回调伪造：金额白名单、非员工点审核、错群点审核
  7. Webhook 密钥
  8. 凭证（截图）跨客户重复使用
  9. 自审（员工审自己的充值单）
 10. 订单余额支付的金额篡改与余额不足
 11. 出餐后退款必须走 MANAGER 专用接口，不能用「取消」

退出码非 0 表示有安全项没挡住。
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv
from jose import jwt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT, ".env"))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
JWT_SECRET = os.environ["JWT_SECRET"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ORIGIN = "http://localhost:5173"

A_ID, A_TG = 1, "555000111"          # 客户 A（也是 seed 里的客户）
B_ID, B_TG = 2, "555000222"          # 客户 B
MANAGER = (1, "999000111")
STAFF = (2, "999000222")
GROUP_ID = "-1001234567890"
OUTSIDER_TG = "999000999"            # 在群里但没有 staff 记录的人

RUN = os.getpid()  # 每次运行独立的幂等键后缀，保证套件可反复跑
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def token(sub: int, kind: str) -> str:
    return jwt.encode({"sub": str(sub), "type": kind}, JWT_SECRET, algorithm="HS256")


def client(kind: str, sub: int) -> httpx.Client:
    cookie = "session_token" if kind == "customer" else "admin_session_token"
    return httpx.Client(base_url=BASE, timeout=20, headers={"Origin": ORIGIN},
                        cookies={cookie: token(sub, kind)})


def anon() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=20, headers={"Origin": ORIGIN})


def webhook(update: dict) -> httpx.Response:
    return httpx.post(f"{BASE}/api/v1/telegram/webhook", json=update, timeout=20,
                      headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})


def cleanup(customer: httpx.Client, admin: httpx.Client) -> int:
    """清掉上一轮遗留的未完成充值单，保证本套件可反复运行。"""
    cleaned = 0
    for row in customer.get("/api/v1/wallet/recharges").json()["recharges"]:
        if row["status"] == "awaiting_proof":
            customer.post(f"/api/v1/wallet/recharges/{row['id']}/cancel")
            cleaned += 1
        elif row["status"] == "under_review":
            admin.post(f"/api/v1/admin/recharges/{row['id']}/reject",
                       json={"reason": "security test cleanup"})
            cleaned += 1
    return cleaned


def main() -> None:
    a, b = client("customer", A_ID), client("customer", B_ID)
    manager, staff = client("staff", MANAGER[0]), client("staff", STAFF[0])
    own = client("customer", 3)          # 员工 manager1 自己的客户账号
    # customer 3 的遗留单只能用**别人**清：manager1 审不了自己的单
    cleaned = cleanup(a, manager) + cleanup(b, manager) + cleanup(own, staff)
    print(f"清理遗留未完成单: {cleaned} 张")

    print("\n[1] 未认证访问")
    for method, path in [("GET", "/api/v1/wallet"), ("GET", "/api/v1/wallet/ledger"),
                         ("GET", "/api/v1/wallet/recharges"),
                         ("POST", "/api/v1/wallet/recharges"),
                         ("GET", "/api/v1/admin/recharges"),
                         ("GET", f"/api/v1/admin/wallets/{A_ID}"),
                         ("POST", f"/api/v1/admin/wallets/{A_ID}/adjust")]:
        r = anon().request(method, path, json={}, headers={"Idempotency-Key": "anon-attack-1"})
        check(f"未登录 {method} {path} -> 401", r.status_code == 401,
              f"{r.status_code} {r.text[:60]}")

    print("\n[2] 越权（客户 -> 员工端点）")
    for method, path in [("GET", "/api/v1/admin/recharges"),
                         ("GET", f"/api/v1/admin/wallets/{A_ID}"),
                         ("POST", f"/api/v1/admin/wallets/{A_ID}/adjust"),
                         ("POST", "/api/v1/admin/wallet/maintenance")]:
        r = a.request(method, path, json={"bucket": "principal", "delta_minor": 999999,
                                          "reason": "escalate"})
        check(f"客户身份 {method} {path} 被拒", r.status_code in (401, 403),
              f"{r.status_code} {r.text[:60]}")

    print("\n[3] STAFF 不能做 MANAGER 的事")
    r = staff.post(f"/api/v1/admin/wallets/{A_ID}/adjust",
                   json={"bucket": "principal", "delta_minor": 100, "reason": "x"})
    check("STAFF 调账 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:50]}")
    r = staff.post("/api/v1/admin/wallet/maintenance")
    check("STAFF 跑运维任务 -> 403", r.status_code == 403, str(r.status_code))

    print("\n[4] 水平越权（IDOR）：B 动 A 的充值单")
    order = a.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                   headers={"Idempotency-Key": f"sec-idor-{RUN}"}).json()
    status_before = a.get(f"/api/v1/wallet/recharges/{order['id']}").json()["status"]
    r = b.get(f"/api/v1/wallet/recharges/{order['id']}")
    check("B 读 A 的充值单 -> 404", r.status_code == 404, f"{r.status_code} {r.text[:50]}")
    r = b.post(f"/api/v1/wallet/recharges/{order['id']}/cancel")
    check("B 取消 A 的充值单 -> 403/404", r.status_code in (403, 404),
          f"{r.status_code} {r.text[:60]}")
    status_after = a.get(f"/api/v1/wallet/recharges/{order['id']}").json()["status"]
    check("A 的单未被 B 改动", status_after == status_before == "awaiting_proof",
          f"{status_before} -> {status_after}")

    print("\n[5] 金额篡改")
    # 注意：HTTP 头的值只能是 ASCII，幂等键里不能塞中文
    for amount, label, key in [(0, "0", "zero"), (-1000, "负数", "negative"),
                               (10 ** 15, "天文数字", "huge"),
                               (10_000_001, "超过 API 上限", "over-cap")]:
        r = a.post("/api/v1/wallet/recharges", json={"amount_minor": amount},
                   headers={"Idempotency-Key": f"sec-amt-{key}"})
        check(f"充值金额 {label} -> 422", r.status_code == 422, f"{r.status_code} {r.text[:50]}")
    for payload, label, key in [({"amount_minor": "1000"}, "字符串", "str"),
                                ({"amount_minor": True}, "布尔", "bool")]:
        r = a.post("/api/v1/wallet/recharges", json=payload,
                   headers={"Idempotency-Key": f"sec-amt-{key}"})
        check(f"充值金额传{label} -> 422（严格整数）", r.status_code == 422,
              f"{r.status_code} {r.text[:60]}")
    r = a.post("/api/v1/wallet/recharges", json={"amount_minor": 50},
               headers={"Idempotency-Key": f"sec-amt-low-{RUN}"})
    check("低于数据库最小额 -> 400", r.status_code == 400, f"{r.status_code} {r.text[:50]}")
    r = manager.post(f"/api/v1/admin/wallets/{A_ID}/adjust",
                     json={"bucket": "principal", "delta_minor": 10 ** 18, "reason": "x"})
    check("调账天文数字 -> 422", r.status_code == 422, str(r.status_code))
    r = manager.post(f"/api/v1/admin/wallets/{A_ID}/adjust",
                     json={"bucket": "bonus; DROP TABLE wallet.wallets", "delta_minor": 1, "reason": "x"})
    check("调账 bucket 注入 -> 422", r.status_code == 422, str(r.status_code))

    print("\n[6] 幂等键跨客户不串单")
    key = f"sec-shared-idem-{RUN}"
    a_order = a.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                     headers={"Idempotency-Key": key}).json()
    b_order = b.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                     headers={"Idempotency-Key": key}).json()
    check("同一幂等键在两个客户下各自建单",
          a_order["id"] != b_order["id"] and b_order["order_no"] != a_order["order_no"],
          f'{a_order["order_no"]} vs {b_order["order_no"]}')

    print("\n[7] Telegram 回调伪造")
    before = len(manager.get("/api/v1/admin/recharges").json())
    fake_amount = {"callback_query": {"id": "sec-cb-1", "from": {"id": int(A_TG)},
                                      "message": {"message_id": 501,
                                                  "chat": {"id": int(A_TG), "type": "private"}},
                                      "data": "wamount_999999"}}
    webhook(fake_amount)
    after = len(manager.get("/api/v1/admin/recharges").json())
    check("伪造金额档位 wamount_999999 不建单", after == before, f"{before} -> {after}")

    r = webhook({"callback_query": {"id": "sec-cb-2", "from": {"id": int(OUTSIDER_TG)},
                                    "message": {"message_id": 502,
                                                "chat": {"id": int(GROUP_ID), "type": "supergroup"}},
                                    "data": f"walletok_{a_order['id']}"}})
    state = [x for x in manager.get("/api/v1/admin/recharges").json() if x["id"] == a_order["id"]][0]
    check("非员工点「确认到账」无效",
          r.status_code == 200 and state["status"] != "credited",
          f'{r.status_code} status={state["status"]}')

    r = webhook({"callback_query": {"id": "sec-cb-3", "from": {"id": int(STAFF[1])},
                                    "message": {"message_id": 503,
                                                "chat": {"id": int(STAFF[1]), "type": "private"}},
                                    "data": f"walletok_{a_order['id']}"}})
    state = [x for x in manager.get("/api/v1/admin/recharges").json() if x["id"] == a_order["id"]][0]
    check("私聊里伪装的审核回调无效", state["status"] != "credited", state["status"])

    print("\n[8] Webhook 密钥")
    r = httpx.post(f"{BASE}/api/v1/telegram/webhook", json={"message": {}}, timeout=20,
                   headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"})
    check("错误的 webhook 密钥 -> 401", r.status_code == 401, str(r.status_code))
    r = httpx.post(f"{BASE}/api/v1/telegram/webhook", json={"message": {}}, timeout=20)
    check("缺少 webhook 密钥 -> 401", r.status_code == 401, str(r.status_code))

    print("\n[9] 凭证（截图）反作弊")
    a2 = a.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                headers={"Idempotency-Key": f"sec-proof-a-{RUN}"}).json()
    b2 = b.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                headers={"Idempotency-Key": f"sec-proof-b-{RUN}"}).json()

    def send_proof(tg: str, text: str, file_id: str, unique_id: str) -> httpx.Response:
        webhook({"message": {"from": {"id": int(tg), "language_code": "en"},
                             "chat": {"id": int(tg), "type": "private"}, "text": text}})
        return webhook({"message": {"from": {"id": int(tg)},
                                    "chat": {"id": int(tg), "type": "private"},
                                    "photo": [{"file_id": file_id, "file_unique_id": unique_id,
                                               "width": 90, "height": 90}]}})

    send_proof(A_TG, f"/start rc_{a2['order_no']}", f"SEC_F{RUN}", f"SEC_UNIQ_SHARED_{RUN}")
    send_proof(B_TG, f"/start rc_{b2['order_no']}", f"SEC_F2_{RUN}", f"SEC_UNIQ_SHARED_{RUN}")
    rows = {x["id"]: x for x in manager.get("/api/v1/admin/recharges").json()}
    check("同一张截图不能跨客户复用",
          rows[a2["id"]]["proof_count"] == 1 and rows[b2["id"]]["proof_count"] == 0,
          f'A={rows[a2["id"]]["proof_count"]} B={rows[b2["id"]]["proof_count"]}')

    print("\n[10] 自审：员工不能审核自己名下的充值单")
    # customer 3 的 telegram_user_id 就是 manager1 本人的账号
    own_order = own.post("/api/v1/wallet/recharges", json={"amount_minor": 1000},
                         headers={"Idempotency-Key": f"sec-self-approve-{RUN}"}).json()
    webhook({"message": {"from": {"id": int(MANAGER[1]), "language_code": "en"},
                         "chat": {"id": int(MANAGER[1]), "type": "private"},
                         "text": f"/start rc_{own_order['order_no']}"}})
    webhook({"message": {"from": {"id": int(MANAGER[1])},
                         "chat": {"id": int(MANAGER[1]), "type": "private"},
                         "photo": [{"file_id": f"SEC_SELF_{RUN}", "file_unique_id": f"SEC_SELF_U{RUN}",
                                    "width": 90, "height": 90}]}})
    ready = [x for x in manager.get("/api/v1/admin/recharges").json()
             if x["id"] == own_order["id"]][0]
    check("自己的充值单已进入 under_review", ready["status"] == "under_review", ready["status"])
    r = manager.post(f"/api/v1/admin/recharges/{own_order['id']}/approve", json={})
    check("员工审核自己的单 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:70]}")
    r = manager.post(f"/api/v1/admin/recharges/{own_order['id']}/reject",
                     json={"reason": "自己驳回自己"})
    check("员工驳回自己的单 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:70]}")
    r = manager.post(f"/api/v1/admin/recharges/{own_order['id']}/received",
                     json={"amount_minor": 99999})
    check("员工改自己单的实收 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:70]}")
    state = [x for x in manager.get("/api/v1/admin/recharges").json()
             if x["id"] == own_order["id"]][0]
    check("自己的单最终未被入账", state["status"] == "under_review", state["status"])

    # customer 3 的账户就是 manager1 自己的，调账同样必须被拒
    r = manager.post(f"/api/v1/admin/wallets/{3}/adjust",
                     json={"bucket": "principal", "delta_minor": 100000, "reason": "给自己加钱"})
    check("MANAGER 给自己账户调账 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:60]}")

    # 用自己账号下单并用余额支付，然后给自己退款。
    # 备款只能走「别人审核我的充值单」——自己给自己调账已被拒绝（见上一条）。
    own_wallet = own.get("/api/v1/wallet").json()
    if own_wallet["available_minor"] < 1000:
        own_recharge = own.post("/api/v1/wallet/recharges", json={"amount_minor": 2000},
                                headers={"Idempotency-Key": f"sec-self-fund-{RUN}"}).json()
        webhook({"message": {"from": {"id": int(MANAGER[1]), "language_code": "en"},
                             "chat": {"id": int(MANAGER[1]), "type": "private"},
                             "text": f"/start rc_{own_recharge['order_no']}"}})
        webhook({"message": {"from": {"id": int(MANAGER[1])},
                             "chat": {"id": int(MANAGER[1]), "type": "private"},
                             "photo": [{"file_id": f"SEC_SELF_F{RUN}",
                                        "file_unique_id": f"SEC_SELF_FU{RUN}",
                                        "width": 90, "height": 90}]}})
        r = staff.post(f"/api/v1/admin/recharges/{own_recharge['id']}/approve", json={})
        check("别的员工可以审核经理本人的充值单", r.status_code == 200,
              f"{r.status_code} {r.text[:60]}")
    pay = own.post("/api/v1/orders",
                   json={"room_number": "SEC3", "items": [{"product_id": 1, "quantity": 1}],
                         "pay_with_wallet": True},
                   headers={"Idempotency-Key": f"sec-self-refund-{RUN}"})
    if pay.status_code == 200:
        own_order_id = [o for o in manager.get("/api/v1/admin/orders").json()
                        if o["public_code"] == pay.json()["public_code"]][0]["id"]
        r = manager.post(f"/api/v1/admin/orders/{own_order_id}/refund",
                         json={"reason": "给自己退款"})
        check("MANAGER 给自己的订单退款 -> 403", r.status_code == 403,
              f"{r.status_code} {r.text[:70]}")
    else:
        # 余额不够就先入账一笔再试（正常情况下前面已备款）
        check("MANAGER 给自己的订单退款 -> 403", False, f"准备订单失败 {pay.status_code} {pay.text[:60]}")

    print("\n[11] 订单余额支付：金额与退款权限")
    # 先给 A 备一点余额（MANAGER 调账，走正规路径）
    adjust = manager.post(f"/api/v1/admin/wallets/{A_ID}/adjust",
                          json={"bucket": "principal", "delta_minor": 20000, "reason": "安全测试备款"})
    check("MANAGER 给客户调账 200", adjust.status_code == 200, adjust.text[:60])
    r = a.post("/api/v1/orders",
               json={"room_number": "SEC1", "items": [{"product_id": 1, "quantity": 2}],
                     "pay_with_wallet": True},
               headers={"Idempotency-Key": f"sec-order-1-{RUN}"})
    check("余额支付下单 200", r.status_code == 200, f"{r.status_code} {r.text[:60]}")
    paid = r.json()
    check("支付金额由服务端计算（1000 分）",
          paid["total_minor"] == 1000, str(paid["total_minor"]))
    order_id = [o for o in manager.get("/api/v1/admin/orders").json()
                if o["public_code"] == paid["public_code"]][0]["id"]
    r = staff.post(f"/api/v1/admin/orders/{order_id}/refund", json={"reason": "x"})
    check("STAFF 调退款接口 -> 403", r.status_code == 403, f"{r.status_code} {r.text[:50]}")
    r = manager.post(f"/api/v1/admin/orders/{order_id}/refund",
                     json={"reason": "测试退款", "amount_minor": 999999})
    check("退款金额超过订单 -> 422", r.status_code == 422, str(r.status_code))
    r = manager.post(f"/api/v1/admin/orders/{order_id}/refund", json={"reason": "客户取消"})
    check("MANAGER 退款 200", r.status_code == 200, f"{r.status_code} {r.text[:70]}")
    r = manager.post(f"/api/v1/admin/orders/{order_id}/refund", json={"reason": "再来一次"})
    check("重复退款不重复退钱",
          r.status_code == 409 or r.json().get("refunded_amount_minor") == paid["total_minor"],
          f'{r.status_code} {r.text[:70]}')

    print("\n[12] 出餐后不能用「取消」把钱退回去")
    r = a.post("/api/v1/orders",
               json={"room_number": "SEC2", "items": [{"product_id": 1, "quantity": 1}],
                     "pay_with_wallet": True},
               headers={"Idempotency-Key": f"sec-order-2-{RUN}"})
    oid = [o for o in manager.get("/api/v1/admin/orders").json()
           if o["public_code"] == r.json()["public_code"]][0]["id"]
    for target in ("ACCEPTED", "READY", "DELIVERED"):
        manager.post(f"/api/v1/admin/orders/{oid}/status", json={"status": target})
    cur = [o for o in manager.get("/api/v1/admin/orders").json() if o["id"] == oid][0]
    check("先把订单推到 DELIVERED", cur["order_status"] == "DELIVERED", cur["order_status"])
    r = manager.post(f"/api/v1/admin/orders/{oid}/status",
                     json={"status": "CANCELLED", "reason": "出餐后偷偷退款"})
    check("DELIVERED 的钱包单不能取消退款 -> 409", r.status_code == 409,
          f"{r.status_code} {r.text[:70]}")

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 66)
    print(f"安全用例: {len(RESULTS)}, 未挡住: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL SECURITY CHECKS PASSED")


if __name__ == "__main__":
    main()
