#!/usr/bin/env python3
"""菜品售卖时间 + 规格/附加选择的端到端测试（需要跑着的服务）。

    python tests/menu_options_e2e.py http://127.0.0.1:8000

重点验证**服务端强制**（前端藏按钮不算数）：
  * 窗口外下单 -> 409，即使绕过界面直接打接口；
  * 必选规格没选 -> 422；
  * 伪造别的菜品的选项 / 不存在的组 -> 422；
  * **价格由服务端算**：请求里塞 price 字段不会被采纳；
  * 订单里保存的是下单时的名称与加价快照。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from jose import jwt
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SECRET = os.getenv("JWT_SECRET", "")
MANAGER_ID, CUSTOMER_ID = 1, 1
RUN = os.getpid()
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def client(kind: str, sub: int) -> httpx.Client:
    cookie = "admin_session_token" if kind == "staff" else "session_token"
    token = jwt.encode({"sub": str(sub), "type": kind}, SECRET, algorithm="HS256")
    return httpx.Client(base_url=BASE, timeout=30,
                        headers={"Origin": "http://localhost:5173"},
                        cookies={cookie: token})


def main() -> None:
    manager, cust = client("staff", MANAGER_ID), client("customer", CUSTOMER_ID)
    tz = ZoneInfo("Asia/Phnom_Penh")
    now = datetime.now(tz)

    menu = cust.get("/api/v1/menu").json()
    products = [p for cat in menu for p in cat["products"]]
    if not products:
        raise SystemExit("菜单为空，请先灌种子数据")
    product = products[0]
    pid, base = product["id"], product["price_minor"]
    check("菜单接口带出售卖时间字段", "sale_windows" in product and "orderable" in product)
    check("菜单接口带出规格分组", isinstance(product.get("option_groups"), list))

    # ---- 1) 售卖时间：配一个已经过去的时间段 -> 不能下单 ----
    past_start = (now - timedelta(hours=3)).strftime("%H:%M")
    past_end = (now - timedelta(hours=2)).strftime("%H:%M")
    r = manager.put(f"/api/v1/admin/products/{pid}/sale-windows",
                    json={"windows": [{"start": past_start, "end": past_end}]})
    check("MANAGER 设置售卖时间 200", r.status_code == 200, f"{r.status_code} {r.text[:60]}")

    menu = cust.get("/api/v1/menu").json()
    product = [p for cat in menu for p in cat["products"] if p["id"] == pid][0]
    check("窗口外 orderable=false", product["orderable"] is False, str(product["orderable"]))
    check("窗口外给出下次开售时间", bool(product["next_sale_start"]), str(product["next_sale_start"]))
    check("窗口外仍有 sale_windows 描述",
          product["sale_windows"] == [{"start": past_start, "end": past_end, "overnight": False}],
          str(product["sale_windows"]))

    out_of_window = cust.post("/api/v1/orders",
                              json={"room_number": "T1", "items": [{"product_id": pid, "quantity": 1}]},
                              headers={"Idempotency-Key": f"opt-window-{RUN}"})
    check("窗口外直接打接口下单 -> 409", out_of_window.status_code == 409,
          f"{out_of_window.status_code} {out_of_window.text[:70]}")

    check("起止时间相同 -> 422",
          manager.put(f"/api/v1/admin/products/{pid}/sale-windows",
                      json={"windows": [{"start": "08:00", "end": "08:00"}]}).status_code == 422)
    check("非 MANAGER 改售卖时间 -> 403",
          client("staff", 2).put(f"/api/v1/admin/products/{pid}/sale-windows",
                                 json={"windows": []}).status_code == 403)

    # 恢复全天可售
    manager.put(f"/api/v1/admin/products/{pid}/sale-windows", json={"windows": []})
    menu = cust.get("/api/v1/menu").json()
    product = [p for cat in menu for p in cat["products"] if p["id"] == pid][0]
    check("清空时间段后恢复全天可售", product["orderable"] is True and product["sale_windows"] == [])

    # ---- 2) 规格 / 附加选择 ----
    groups_payload = {"groups": [
        {"name": {"en": "Size", "zh-CN": "规格"}, "kind": "SPEC", "required": True,
         "options": [{"name": {"en": "Medium", "zh-CN": "中杯"}, "price_delta_minor": 0, "is_default": True},
                     {"name": {"en": "Large", "zh-CN": "大杯"}, "price_delta_minor": 200}]},
        {"name": {"en": "Toppings", "zh-CN": "附加"}, "kind": "ADDON", "multi_select": True,
         "max_select": 2,
         "options": [{"name": {"en": "Pearl", "zh-CN": "珍珠"}, "price_delta_minor": 100},
                     {"name": {"en": "Coconut jelly", "zh-CN": "椰果"}, "price_delta_minor": 150},
                     {"name": {"en": "Sold out", "zh-CN": "售罄"}, "price_delta_minor": 0, "active": False}]},
    ]}
    r = manager.put(f"/api/v1/admin/products/{pid}/option-groups", json=groups_payload)
    check("MANAGER 保存规格/附加 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")

    menu = cust.get("/api/v1/menu").json()
    product = [p for cat in menu for p in cat["products"] if p["id"] == pid][0]
    groups = product["option_groups"]
    check("菜单带出两个分组", len(groups) == 2, str([g["name_text"] for g in groups]))
    check("单选组标记正确", groups[0]["multi_select"] is False and groups[0]["required"] is True)
    check("多选组上限正确", groups[1]["max_select"] == 2, str(groups[1]["max_select"]))
    check("停用的选项不下发",
          [o["name_text"] for o in groups[1]["options"]] == ["Pearl", "Coconut jelly"],
          str([o["name_text"] for o in groups[1]["options"]]))
    size_id, addon_id = groups[0]["id"], groups[1]["id"]
    medium_id = groups[0]["options"][0]["id"]
    large_id = groups[0]["options"][1]["id"]
    pearl_id = groups[1]["options"][0]["id"]

    # 必选没选
    r = cust.post("/api/v1/orders",
                  json={"room_number": "T2", "items": [{"product_id": pid, "quantity": 1}]},
                  headers={"Idempotency-Key": f"opt-missing-{RUN}"})
    check("必选规格没选 -> 422", r.status_code == 422, f"{r.status_code} {r.text[:70]}")

    # 伪造分组
    r = cust.post("/api/v1/orders",
                  json={"room_number": "T2", "items": [{"product_id": pid, "quantity": 1,
                        "options": {"selections": [{"group_id": 999999, "option_id": medium_id}]}}]},
                  headers={"Idempotency-Key": f"opt-fake-{RUN}"})
    check("伪造分组 -> 422（原因：分组不属于该菜品）",
          r.status_code == 422 and "not available for this product" in r.text,
          f"{r.status_code} {r.text[:80]}")

    # 单选组里选两个
    r = cust.post("/api/v1/orders",
                  json={"room_number": "T2", "items": [{"product_id": pid, "quantity": 1,
                        "options": {"selections": [{"group_id": size_id, "option_id": medium_id},
                                                   {"group_id": size_id, "option_id": large_id}]}}]},
                  headers={"Idempotency-Key": f"opt-two-{RUN}"})
    check("单选组选两个 -> 422（原因：只能选一个）",
          r.status_code == 422 and "Only one choice" in r.text, f"{r.status_code} {r.text[:80]}")

    # ---- 3) 正常下单：大杯 + 珍珠，数量 2，并且试图塞一个伪造价格 ----
    expected_unit = base + 200 + 100
    r = cust.post("/api/v1/orders",
                  json={"room_number": "T3", "items": [{
                        "product_id": pid, "quantity": 2,
                        "unit_price_minor": 1,      # 伪造价格：服务端必须忽略
                        "price_minor": 1,
                        "line_total_minor": 1,
                        "options": {"selections": [{"group_id": size_id, "option_id": large_id},
                                                   {"group_id": addon_id, "option_id": pearl_id}]}}]},
                  headers={"Idempotency-Key": f"opt-price-{RUN}"})
    check("带规格下单 200", r.status_code == 200, f"{r.status_code} {r.text[:90]}")
    total = r.json().get("total_minor") if r.status_code == 200 else None
    check(f"总价由服务端算：({base}+200+100)×2 = {expected_unit * 2}", total == expected_unit * 2,
          f"got {total}")

    if r.status_code == 200:
        code = r.json()["public_code"]
        # 明细走详情接口（列表接口只返回概要）
        detail = cust.get(f"/api/v1/orders/{code}")
        check("客户能查订单详情", detail.status_code == 200, str(detail.status_code))
        items = detail.json().get("items") or []
        check("订单详情带明细", len(items) == 1, str(len(items)))
        selections = (items[0].get("options") or {}).get("selections") if items else None
        check("订单里保存了选项快照（含加价）",
              bool(selections) and {x["price_delta_minor"] for x in selections} == {200, 100},
              str(selections)[:130])
        check("快照里带名称（后续改名不影响历史订单）",
              bool(selections) and all(x.get("option_name") for x in selections),
              str([x.get("option_name") for x in selections])[:110] if selections else "")
        check("明细单价 = 基础价 + 加价",
              items and items[0]["unit_price_minor"] == expected_unit,
              str(items[0]["unit_price_minor"]) if items else "")

    # 多选超过上限（3 个不同的附加，只有 2 个启用） -> 目前只有 2 个启用项，构造 3 个会命中未知选项
    unknown = cust.post("/api/v1/orders",
                        json={"room_number": "T4", "items": [{"product_id": pid, "quantity": 1,
                              "options": {"selections": [{"group_id": size_id, "option_id": medium_id},
                                                         {"group_id": addon_id, "option_id": 999999}]}}]},
                        headers={"Idempotency-Key": f"opt-unknown-{RUN}"})
    check("传了不存在的选项 -> 422（原因：选项不可用）",
          unknown.status_code == 422 and "not available in group" in unknown.text,
          f"{unknown.status_code} {unknown.text[:80]}")

    # 清空规格，避免影响其它用例
    manager.put(f"/api/v1/admin/products/{pid}/option-groups", json={"groups": []})
    menu = cust.get("/api/v1/menu").json()
    product = [p for cat in menu for p in cat["products"] if p["id"] == pid][0]
    check("清空后菜单不再带规格", product["option_groups"] == [])

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"菜品端到端用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL MENU/OPTION E2E TESTS PASSED")


if __name__ == "__main__":
    main()
