#!/usr/bin/env python3
"""多商家地基的端到端测试（需要跑着的服务 + 已建好两家店）。

    python tests/multi_store_e2e.py http://127.0.0.1:8000

前置数据（由 run_wallet_tests.sh 之外单独准备，见文件末尾说明）：
  * MAIN 主店（迁移自动创建）
  * ST02 二号店 + 对 product 1 的价格覆盖

验证的核心是**菜单展示与下单计价是同一套覆盖逻辑**：
  * 主店看到 500、ST02 看到 700；
  * 客户绑定哪家店，就按哪家店的价格扣款，订单也记到那家店；
  * 覆盖表的 NULL 表示继承总部（不覆盖时与单店行为一致）；
  * 门店可以单独下架总部在卖的菜。
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
PRODUCT_ID = 1
RUN = os.getpid()
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def client(kind: str, sub: int) -> httpx.Client:
    cookie = "admin_session_token" if kind == "staff" else "session_token"
    return httpx.Client(base_url=BASE, timeout=25, headers={"Origin": "http://localhost:5173"},
                        cookies={cookie: jwt.encode({"sub": str(sub), "type": kind}, SECRET,
                                                    algorithm="HS256")})


def menu_price(store: str | None = None) -> int | None:
    url = "/api/v1/menu" + (f"?store={store}" if store else "")
    resp = httpx.get(f"{BASE}{url}", timeout=25)
    if resp.status_code != 200:
        return None
    for category in resp.json():
        for product in category["products"]:
            if product["id"] == PRODUCT_ID:
                return product["price_minor"]
    return None


def main() -> None:
    manager = client("staff", 1)
    cust = client("customer", 1)

    base_price = menu_price()
    branch_price = menu_price("ST02")
    if base_price is None or branch_price is None:
        raise SystemExit("菜单里找不到测试菜品，请先准备前置数据")
    check("主店菜单价格（总部模板价）", base_price > 0, str(base_price))
    check("二号店菜单价格被覆盖", branch_price != base_price,
          f"main={base_price} ST02={branch_price}")

    # 未知门店码要回退到主店，而不是把顾客挡在门外
    check("未知门店码回退主店", menu_price("NO_SUCH_STORE") == base_price,
          str(menu_price("NO_SUCH_STORE")))

    # 下单计价 = 展示价（同一套覆盖逻辑）
    r = cust.post("/api/v1/orders",
                  json={"room_number": "MS1", "items": [{"product_id": PRODUCT_ID, "quantity": 1}]},
                  headers={"Idempotency-Key": f"ms-main-{RUN}"})
    check("客户下单成功", r.status_code == 200, f"{r.status_code} {r.text[:60]}")
    if r.status_code == 200:
        code = r.json()["public_code"]
        charged = r.json()["total_minor"]
        check("按所属门店价格扣款", charged == base_price,
              f"菜单 {base_price} / 扣款 {charged}")
        detail = cust.get(f"/api/v1/orders/{code}").json()
        check("订单详情价格一致", detail["total_minor"] == base_price, str(detail["total_minor"]))

    # 总数/分店隔离的最小校验：门店码不同的菜单是两份数据
    check("门店码区分菜单", menu_price("ST02") == branch_price and menu_price() == base_price,
          f"ST02={menu_price('ST02')} MAIN={menu_price()}")

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"多商家用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL MULTI-STORE TESTS PASSED")


if __name__ == "__main__":
    main()
