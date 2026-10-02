#!/usr/bin/env python3
"""店铺经营参数的端到端测试（需要跑着的服务）。

    python tests/operating_settings_e2e.py http://127.0.0.1:8000

验证这些设置**真的在下单时生效**（不是摆设）：
  * 暂停接单 -> 409
  * 营业时间外 -> 409（按店铺时区）
  * 未达最低起送 -> 409（按商品小计，不含费用）
  * 配送费/服务费计入总额，且订单里能查到金额构成
  * 起止时间相同 -> 422；非 MANAGER 改设置 -> 403
跑完会把设置恢复原样。
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
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def client(kind: str, sub: int) -> httpx.Client:
    cookie = "admin_session_token" if kind == "staff" else "session_token"
    return httpx.Client(base_url=BASE, timeout=25, headers={"Origin": "http://localhost:5173"},
                        cookies={cookie: jwt.encode({"sub": str(sub), "type": kind}, SECRET,
                                                    algorithm="HS256")})


def main() -> None:
    manager, cust = client("staff", 1), client("customer", 1)
    staff = client("staff", 2)
    original = manager.get("/api/v1/admin/settings").json()

    def patch(**changes) -> httpx.Response:
        return manager.patch("/api/v1/admin/settings", json={**original, **changes})

    def order(tag: str) -> httpx.Response:
        return cust.post("/api/v1/orders",
                         json={"room_number": "OPS", "items": [{"product_id": 1, "quantity": 1}]},
                         headers={"Idempotency-Key": f"ops-{tag}-{RUN}"})

    try:
        # 基线：默认不限制，起送 0、无费用
        patch(is_accepting_orders=True, business_hours=[], min_order_minor=0,
              delivery_fee_minor=0, service_fee_minor=0)
        base = order("base")
        check("基线可下单", base.status_code == 200, f"{base.status_code} {base.text[:60]}")
        base_total = base.json().get("total_minor") if base.status_code == 200 else None
        check("基线订单带金额构成字段",
              base.status_code == 200 and base.json().get("subtotal_minor") == base_total,
              f'subtotal={base.json().get("subtotal_minor") if base.status_code == 200 else "-"}')

        # 1) 暂停接单
        patch(is_accepting_orders=False)
        r = order("paused")
        check("暂停接单 -> 409", r.status_code == 409 and "暂停" in r.text,
              f"{r.status_code} {r.text[:60]}")

        # 2) 营业时间外
        patch(is_accepting_orders=True, business_hours=[{"start": "03:00", "end": "03:30"}])
        r = order("hours")
        check("营业时间外 -> 409", r.status_code == 409 and "营业时间" in r.text,
              f"{r.status_code} {r.text[:70]}")
        check("设置里能看到营业时间",
              manager.get("/api/v1/admin/settings").json()["business_hours"] == [{"start": "03:00", "end": "03:30"}])
        check("起止时间相同 -> 422",
              patch(business_hours=[{"start": "08:00", "end": "08:00"}]).status_code == 422)
        check("时间格式错误 -> 422",
              patch(business_hours=[{"start": "25:99", "end": "26:00"}]).status_code == 422)

        # 3) 最低起送（按商品小计判断）
        patch(business_hours=[], min_order_minor=(base_total or 100) * 4)
        r = order("minorder")
        check("未达最低起送 -> 409", r.status_code == 409 and "最低起送" in r.text,
              f"{r.status_code} {r.text[:70]}")

        # 4) 配送费 / 服务费计入总额
        patch(min_order_minor=0, delivery_fee_minor=150, service_fee_minor=50)
        r = order("fees")
        body = r.json() if r.status_code == 200 else {}
        check("含费用下单 200", r.status_code == 200, f"{r.status_code} {r.text[:60]}")
        check("总额 = 小计 + 配送费 + 服务费",
              body.get("total_minor") == (base_total or 0) + 200
              and body.get("delivery_fee_minor") == 150 and body.get("service_fee_minor") == 50,
              f'subtotal={body.get("subtotal_minor")} total={body.get("total_minor")} '
              f'delivery={body.get("delivery_fee_minor")} service={body.get("service_fee_minor")}')
        if r.status_code == 200:
            detail = cust.get(f"/api/v1/orders/{body['public_code']}").json()
            check("订单详情能查到费用构成",
                  detail.get("delivery_fee_minor") == 150 and detail.get("service_fee_minor") == 50,
                  f'delivery={detail.get("delivery_fee_minor")} service={detail.get("service_fee_minor")}')

        # 5) 权限
        check("STAFF 改设置 -> 403", staff.patch("/api/v1/admin/settings", json=original).status_code == 403)
        check("未登录改设置 -> 401",
              httpx.patch(f"{BASE}/api/v1/admin/settings", json=original, timeout=20).status_code == 401)
        check("客户改设置 -> 401",
              cust.patch("/api/v1/admin/settings", json=original).status_code == 401)
    finally:
        # 无论成败都恢复，避免污染其它用例
        manager.patch("/api/v1/admin/settings", json=original)

    fails = [x for x in RESULTS if not x[1]]
    print("\n" + "=" * 60)
    print(f"经营参数用例: {len(RESULTS)}, 失败: {len(fails)}")
    for name, _, detail in fails:
        print("  FAIL", name, "|", detail)
    if fails:
        sys.exit(1)
    print("ALL OPERATING SETTINGS TESTS PASSED")


if __name__ == "__main__":
    main()
