#!/usr/bin/env python3
"""钱包 / 充值 API + Telegram webhook 端到端验证。

前提：数据库已 `alembic upgrade head` 并灌好夹具，服务已启动：
    uvicorn main:app --port 8099
用法：
    python tests/wallet_api_e2e.py http://127.0.0.1:8099
环境：需要能读到仓库根目录的 .env（JWT_SECRET / WEBHOOK_SECRET）。

它做的事：
  1. 用 JWT_SECRET 自己签一个客户会话和一个员工会话（绕开前端登录，仅本地验证）。
  2. 客户侧 API：查余额 -> 建充值单 -> 幂等重复提交 -> 限额 -> 取消。
  3. 员工侧 API：列表 / 审核通过到账 / 重复审核幂等 / 调账（STAFF 无权限）。
  4. 直接给 /api/v1/telegram/webhook 灌 Telegram update，走一遍机器人真实链路：
     /wallet -> 点档位建单 -> deep link -> 发截图 -> 重复图被拒 -> 群里确认到账。

断言全部基于「本次运行前后余额的增量」，所以在同一个库上可以反复跑。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from jose import jwt

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
JWT_SECRET = os.environ["JWT_SECRET"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ORIGIN = "http://localhost:5173"

CUSTOMER_ID = 1
CUSTOMER_TG = "555000111"
MANAGER_ID, MANAGER_TG = 1, "999000111"
STAFF_ID, STAFF_TG = 2, "999000222"
GROUP_ID = "-1001234567890"

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


def webhook(update: dict) -> httpx.Response:
    return httpx.post(f"{BASE}/api/v1/telegram/webhook", json=update, timeout=20,
                      headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})


def msg(text: str) -> dict:
    return {"message": {"from": {"id": int(CUSTOMER_TG), "language_code": "en"},
                        "chat": {"id": int(CUSTOMER_TG), "type": "private"}, "text": text}}


def photo(file_id: str, unique_id: str) -> dict:
    return {"message": {"from": {"id": int(CUSTOMER_TG)},
                        "chat": {"id": int(CUSTOMER_TG), "type": "private"},
                        "photo": [{"file_id": file_id, "file_unique_id": unique_id,
                                   "width": 90, "height": 90}]}}


def cb(data: str, sender_tg: str, chat_id: int, kind: str, message_id: int) -> dict:
    return {"callback_query": {"id": f"cb-{data}-{message_id}", "from": {"id": int(sender_tg)},
                               "message": {"message_id": message_id,
                                           "chat": {"id": chat_id, "type": kind}},
                               "data": data}}


def cleanup(cust: httpx.Client, admin: httpx.Client) -> int:
    """把上一次运行遗留的未完成充值单清掉。

    `max_open_orders` 默认 3——这是产品的真实限制，不是测试问题；
    测试要能反复跑就必须自己收拾现场（awaiting_proof 取消，under_review 驳回）。
    """
    rows = cust.get("/api/v1/wallet/recharges").json()["recharges"]
    cleaned = 0
    for row in rows:
        if row["status"] == "awaiting_proof":
            cust.post(f"/api/v1/wallet/recharges/{row['id']}/cancel")
            cleaned += 1
        elif row["status"] == "under_review":
            admin.post(f"/api/v1/admin/recharges/{row['id']}/reject",
                       json={"reason": "e2e cleanup"})
            cleaned += 1
    return cleaned


def main() -> None:
    run = os.getpid()
    # 机器人的幂等键 = 「承载按钮的那条消息」的身份（chat:message:a{金额}）。
    # 消息 id 必须每次运行都不同，否则第二次运行会命中幂等、返回上一轮那张单
    # ——那正是设计要的行为，测试不能去撞它。
    mid = 1000 + (run % 9000)
    cust = client("customer", CUSTOMER_ID)
    admin = client("staff", MANAGER_ID)   # MANAGER
    staff = client("staff", STAFF_ID)     # STAFF（可审到账、不可调账）

    print(f"清理遗留未完成充值单: {cleanup(cust, admin)} 张")
    start = cust.get("/api/v1/wallet").json()
    p0, b0 = start["principal_minor"], start["bonus_minor"]
    print(f"起始余额: 本金 {p0} / 赠送 {b0}")

    print("\n[1] 客户侧：钱包概览")
    check("GET /wallet 200", bool(start), json.dumps(start, default=str)[:140])
    check("返回档位与限额", bool(start["presets_minor"]) and start["min_recharge_minor"] == 100,
          str(start.get("presets_minor")))

    print("\n[2] 建充值单 + 幂等 + 限额")
    hdr = {"Idempotency-Key": f"e2e-{run}-a"}
    r1 = cust.post("/api/v1/wallet/recharges", json={"amount_minor": 1000}, headers=hdr)
    check("POST /wallet/recharges 200", r1.status_code == 200, f"{r1.status_code} {r1.text[:110]}")
    o1 = r1.json()
    check("赠送按规则快照 = 200", o1["bonus_amount_minor"] == 200, str(o1["bonus_amount_minor"]))
    check("初始状态 awaiting_proof", o1["status"] == "awaiting_proof", o1["status"])
    check("带收款链接与机器人深链", bool(o1["payment_link"]) and bool(o1["bot_deeplink"]),
          o1["bot_deeplink"] or "")

    r2 = cust.post("/api/v1/wallet/recharges", json={"amount_minor": 1000}, headers=hdr)
    check("同一 Idempotency-Key 返回同一张单", r2.json()["id"] == o1["id"],
          f'{r2.json()["id"]} vs {o1["id"]}')

    r3 = cust.post("/api/v1/wallet/recharges", json={"amount_minor": 50},
                   headers={"Idempotency-Key": f"e2e-{run}-low"})
    check("低于最小额被拒 400", r3.status_code == 400, str(r3.status_code))
    check("错误文案是业务文案（不是「系统繁忙」）",
          "最小" in r3.json().get("detail", ""), r3.json().get("detail", "")[:60])

    o2 = cust.post("/api/v1/wallet/recharges", json={"amount_minor": 5000},
                   headers={"Idempotency-Key": f"e2e-{run}-b"}).json()
    check("第二张单赠送 10% = 500", o2["bonus_amount_minor"] == 500, str(o2["bonus_amount_minor"]))

    # 前端「我的充值单」列表要能直接拿到去机器人发截图的深链
    listed = cust.get("/api/v1/wallet/recharges").json()["recharges"]
    open_row = [r for r in listed if r["id"] == o1["id"]][0]
    check("列表里未完成的单带 bot_deeplink", bool(open_row.get("bot_deeplink")),
          str(open_row.get("bot_deeplink")))
    check("列表字段齐全（前端渲染依赖）",
          all(key in open_row for key in ("order_no", "amount_minor", "bonus_amount_minor",
                                          "status", "created_at", "proof_count")),
          ",".join(sorted(open_row.keys()))[:120])

    print("\n[3] 取消充值单（幂等）")
    rc = cust.post(f"/api/v1/wallet/recharges/{o2['id']}/cancel")
    check("取消未付款单 200", rc.status_code == 200 and rc.json()["status"] == "cancelled", rc.text[:90])
    rc2 = cust.post(f"/api/v1/wallet/recharges/{o2['id']}/cancel")
    check("重复取消幂等", rc2.status_code == 200 and rc2.json()["status"] == "cancelled", rc2.text[:70])

    print("\n[4] 员工侧：先补凭证，再确认到账")
    webhook(msg(f"/start rc_{o1['order_no']}"))          # deep link 把单接到「等凭证」
    r = webhook(photo(f"FILE-{run}-1", f"UNIQ-{run}-1"))
    after_proof = admin.get("/api/v1/admin/recharges",
                            params={"status": "under_review"}).json()
    check("webhook 发截图后进入 under_review",
          r.status_code == 200 and any(x["id"] == o1["id"] for x in after_proof),
          f"http={r.status_code}")

    r = admin.post(f"/api/v1/admin/recharges/{o1['id']}/approve", json={})
    check("MANAGER 审核通过 200", r.status_code == 200, f"{r.status_code} {r.text[:110]}")
    check("实收 = 订单金额", r.json().get("received_amount_minor") == 1000,
          str(r.json().get("received_amount_minor")))
    r = admin.post(f"/api/v1/admin/recharges/{o1['id']}/approve", json={})
    check("重复审核幂等（已 credited 直接返回）",
          r.status_code == 200 and r.json()["status"] == "credited", r.text[:70])

    w = cust.get("/api/v1/wallet").json()
    check("本金 +1000 / 赠送 +200",
          (w["principal_minor"], w["bonus_minor"]) == (p0 + 1000, b0 + 200), json.dumps(w, default=str))

    ledger = cust.get("/api/v1/wallet/ledger").json()["entries"]
    # 账本里的 biz_id 记的是**订单号**（转账备注用的那个），不是数字主键
    mine = [e for e in ledger if e["biz_id"] == o1["order_no"]]
    check("账本为该单记 2 条入账流水", len(mine) == 2 and all(e["direction"] == 1 for e in mine),
          str(len(mine)))
    check("流水带余额快照", all(e["balance_after_minor"] is not None for e in mine))

    print("\n[5] 员工侧：权限与调账")
    r = staff.post(f"/api/v1/admin/wallets/{CUSTOMER_ID}/adjust",
                   json={"bucket": "principal", "delta_minor": 100, "reason": "test"})
    check("STAFF 不能调账 403", r.status_code == 403, f"{r.status_code} {r.text[:70]}")
    r = admin.post(f"/api/v1/admin/wallets/{CUSTOMER_ID}/adjust",
                   json={"bucket": "principal", "delta_minor": 500, "reason": "客服补偿"})
    check("MANAGER 可调账", r.status_code == 200 and r.json()["balance_after_minor"] == p0 + 1500,
          f"{r.status_code} {r.text[:90]}")
    r = admin.get(f"/api/v1/admin/wallets/{CUSTOMER_ID}")
    check("员工可查客户钱包与账本", r.status_code == 200 and len(r.json()["ledger"]) >= 3,
          str(r.status_code))

    # 管理端「充值管理」表格依赖这些字段：用户ID / 名称 / Telegram / 当前余额
    listed = admin.get("/api/v1/admin/recharges").json()
    row = [x for x in listed if x["id"] == o1["id"]][0]
    wallet_total = cust.get("/api/v1/wallet").json()["total_minor"]
    check("管理端列表带客户ID/名称/Telegram/当前余额",
          row["customer_id"] == CUSTOMER_ID
          and row["telegram_user_id"] == CUSTOMER_TG
          and "username" in row
          and row["balance_minor"] == wallet_total,
          f'customer={row["customer_id"]} tg={row["telegram_user_id"]} '
          f'balance={row["balance_minor"]} vs {wallet_total}')
    check("余额拆分（本金+赠送=合计）",
          row["principal_minor"] + row["bonus_minor"] == row["balance_minor"],
          f'{row["principal_minor"]}+{row["bonus_minor"]}={row["balance_minor"]}')

    print("\n[6] 机器人链路：/wallet -> 点档位 -> 发截图 -> 群里确认到账")
    before_ids = {x["id"] for x in admin.get("/api/v1/admin/recharges").json()}
    r = webhook(msg("/wallet"))
    check("webhook /wallet 200", r.status_code == 200 and r.json() == {"ok": True}, r.text[:60])

    r = webhook(cb("wamount_2000", CUSTOMER_TG, int(CUSTOMER_TG), "private", mid))
    check("webhook 点档位建单 200", r.status_code == 200, r.text[:60])
    new_rows = [x for x in admin.get("/api/v1/admin/recharges").json() if x["id"] not in before_ids]
    check("机器人建出 1 张 $20 充值单", len(new_rows) == 1 and new_rows[0]["amount_minor"] == 2000,
          str([x["order_no"] for x in new_rows]))
    order = new_rows[0]

    webhook(cb("wamount_2000", CUSTOMER_TG, int(CUSTOMER_TG), "private", mid))
    again = [x for x in admin.get("/api/v1/admin/recharges").json() if x["id"] not in before_ids]
    check("同一条消息重复点击不重复建单", len(again) == 1, str(len(again)))

    r = webhook(photo(f"FILE-{run}-2", f"UNIQ-{run}-2"))
    after = [x for x in admin.get("/api/v1/admin/recharges").json() if x["id"] == order["id"]][0]
    check("收到截图后 under_review 且凭证数 1",
          r.status_code == 200 and after["status"] == "under_review" and after["proof_count"] == 1,
          f'{after["status"]}/{after["proof_count"]}')

    # 重新进入「等待凭证」态，再发一张 file_unique_id 相同的图：唯一索引必须拒绝
    webhook(cb(f"wproof_{order['id']}", CUSTOMER_TG, int(CUSTOMER_TG), "private", 12))
    webhook(photo(f"FILE-{run}-3", f"UNIQ-{run}-2"))
    dup = [x for x in admin.get("/api/v1/admin/recharges").json() if x["id"] == order["id"]][0]
    check("同一张截图不能重复上传（凭证数仍为 1）", dup["proof_count"] == 1, str(dup["proof_count"]))

    r = webhook(cb(f"walletok_{order['id']}", STAFF_TG, int(GROUP_ID), "supergroup", 21))
    check("webhook 群里确认到账 200", r.status_code == 200, r.text[:60])
    final = [x for x in admin.get("/api/v1/admin/recharges").json() if x["id"] == order["id"]][0]
    check("在职 STAFF 可确认到账（与订单收款同口径）", final["status"] == "credited", final["status"])

    r = webhook(cb(f"walletok_{order['id']}", MANAGER_TG, int(GROUP_ID), "supergroup", 22))
    check("重复确认到账不重复入账", r.status_code == 200, r.text[:60])

    w = cust.get("/api/v1/wallet").json()
    # 起始 + $10 单(1000/赠送 200) + 调账 500 + 机器人 $20 单(2000/赠送 200)
    # 注意 $20 命中「满 $10 送 $2」是固定送 200；10% 那档要 >= $50。
    check("最终余额 = 起始 +3500 本金 / +400 赠送",
          (w["principal_minor"], w["bonus_minor"]) == (p0 + 3500, b0 + 400), json.dumps(w, default=str))

    print("\n[7] 已提交凭证的单不能自行取消 / 运维自检")
    rc3 = cust.post(f"/api/v1/wallet/recharges/{o1['id']}/cancel")
    check("已传凭证的单被拒 409", rc3.status_code == 409, f"{rc3.status_code} {rc3.text[:70]}")
    check("提示文案是业务文案", "凭证" in rc3.json().get("detail", ""), rc3.json().get("detail", "")[:70])

    r = admin.post("/api/v1/admin/wallet/maintenance")
    check("运维入口：对账无差异", r.status_code == 200 and r.json().get("healthy") is True,
          r.text[:140])
    r = staff.post("/api/v1/admin/wallet/maintenance")
    check("STAFF 不能跑运维任务 403", r.status_code == 403, str(r.status_code))

    print("\n[7b] 回归：原有「人工转账 + 截图审核」流程没被钱包改动破坏")
    manual = cust.post("/api/v1/orders",
                       json={"room_number": "M1", "items": [{"product_id": 1, "quantity": 1}]},
                       headers={"Idempotency-Key": f"manual-{run}"}).json()
    check("人工转账订单仍是 UNPAID / MANUAL",
          manual["payment_status"] == "UNPAID" and manual["payment_method"] == "MANUAL",
          f'{manual["payment_status"]}/{manual["payment_method"]}')
    check("人工转账订单仍给收款指引与深链",
          bool(manual["bot_deeplink"]), str(manual["bot_deeplink"]))
    webhook(msg(f"/start pay_{manual['public_code']}"))
    webhook(photo(f"MAN-{run}-1", f"MANU-{run}-1"))
    manual_detail = cust.get(f"/api/v1/orders/{manual['public_code']}").json()
    check("截图后进入 PROOF_SUBMITTED",
          manual_detail["payment_status"] == "PROOF_SUBMITTED",
          manual_detail["payment_status"])

    manual_id = [o for o in admin.get("/api/v1/admin/orders").json()
                 if o["public_code"] == manual["public_code"]][0]["id"]
    r = admin.post(f"/api/v1/admin/orders/{manual_id}/status", json={"status": "ACCEPTED"})
    check("人工转账订单接单后**不**跳到制作",
          r.status_code == 200 and r.json()["order_status"] == "ACCEPTED", r.text[:90])
    r = admin.post(f"/api/v1/admin/orders/{manual_id}/payment-review",
                   json={"decision": "APPROVED"})
    check("确认收款后 PAID_CONFIRMED + PREPARING",
          r.status_code == 200 and r.json()["payment_status"] == "PAID_CONFIRMED", r.text[:90])
    r = admin.post(f"/api/v1/admin/orders/{manual_id}/status", json={"status": "READY"})
    check("制作完成后可进入可配送", r.status_code == 200 and r.json()["order_status"] == "READY",
          r.text[:90])
    r = admin.post(f"/api/v1/admin/orders/{manual_id}/status",
                   json={"status": "CANCELLED", "reason": "不该被允许"})
    check("已确认收款的人工转账订单仍禁止取消（退款必须走线下）",
          r.status_code == 409, f"{r.status_code} {r.text[:60]}")

    print("\n[8] 下单用余额抵扣 + 取消自动退款")
    bal = cust.get("/api/v1/wallet").json()["total_minor"]
    pay_key = f"wallet-pay-{run}"
    r = cust.post("/api/v1/orders",
                  json={"room_number": "W1", "items": [{"product_id": 1, "quantity": 2}],
                        "pay_with_wallet": True},
                  headers={"Idempotency-Key": pay_key})
    check("余额支付下单 200", r.status_code == 200, f"{r.status_code} {r.text[:110]}")
    paid = r.json()
    check("下单即 PAID_CONFIRMED", paid["payment_status"] == "PAID_CONFIRMED", paid["payment_status"])
    check("payment_method = WALLET", paid["payment_method"] == "WALLET", paid["payment_method"])
    check("已付清的订单不再给转账信息",
          not paid["payment_link"] and not paid["bot_deeplink"], str(paid["bot_deeplink"]))
    after_pay = cust.get("/api/v1/wallet").json()["total_minor"]
    check("余额被扣 1000", after_pay == bal - 1000, f"{after_pay} vs {bal - 1000}")

    again = cust.post("/api/v1/orders",
                      json={"room_number": "W1", "items": [{"product_id": 1, "quantity": 2}],
                            "pay_with_wallet": True},
                      headers={"Idempotency-Key": pay_key}).json()
    check("重复提交返回同一张单", again["public_code"] == paid["public_code"],
          f'{again["public_code"]} vs {paid["public_code"]}')
    check("重复提交不重复扣款",
          cust.get("/api/v1/wallet").json()["total_minor"] == after_pay,
          str(cust.get("/api/v1/wallet").json()["total_minor"]))

    order_id = [o for o in admin.get("/api/v1/admin/orders").json()
                if o["public_code"] == paid["public_code"]][0]["id"]
    r = admin.post(f"/api/v1/admin/orders/{order_id}/status", json={"status": "ACCEPTED"})
    check("接单后直接进入制作（不必再等收款确认）",
          r.status_code == 200 and r.json()["order_status"] == "PREPARING", r.text[:90])

    r = admin.post(f"/api/v1/admin/orders/{order_id}/status",
                   json={"status": "CANCELLED", "reason": "e2e 取消退款"})
    check("取消钱包订单 200", r.status_code == 200, f"{r.status_code} {r.text[:90]}")
    check("支付状态变为 REFUNDED", r.json().get("payment_status") == "REFUNDED", r.text[:90])
    check("退款回到钱包", cust.get("/api/v1/wallet").json()["total_minor"] == bal,
          str(cust.get("/api/v1/wallet").json()["total_minor"]))

    before_count = len(cust.get("/api/v1/orders").json())
    r = cust.post("/api/v1/orders",
                  json={"room_number": "W2", "items": [{"product_id": 1, "quantity": 99}],
                        "pay_with_wallet": True},
                  headers={"Idempotency-Key": f"wallet-pay-poor-{run}"})
    check("余额不足时 409", r.status_code == 409, f"{r.status_code} {r.text[:80]}")
    check("余额不足时订单没有被创建",
          len(cust.get("/api/v1/orders").json()) == before_count,
          str(len(cust.get("/api/v1/orders").json())))

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 64)
    print(f"端到端用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL E2E TESTS PASSED")


if __name__ == "__main__":
    main()
