"""HTTP 级与调度级集成测试：订单取消 / 超时 / 缺货升级 / 退款权限。

补 `order_lifecycle_db_test.py`（直接调用路由函数、绕过鉴权）没覆盖的部分：

  * 经真实 ASGI 栈（鉴权 Cookie、Origin 校验、角色依赖、门店隔离）调用接口；
  * 未付款超时：取消 + 通知顾客 + 更新群卡片、到期前提醒只发一次、被拒订单从拒绝时刻重新计时；
  * 已付款缺货：顾客长时间不处理时，一次提醒（半程）+ 一次升级（全程），幂等；
  * 混合支付订单删品后的付款状态（ABA 部分仍有效，不能标成 REFUNDED）。

运行（需已 `alembic upgrade head` 的 PostgreSQL；Telegram 全程用 mock，不联网）：

    RUN_DB_TESTS=1 DATABASE_URL=postgresql+asyncpg://... JWT_SECRET=... \
        python -m unittest tests/order_lifecycle_integration_test.py -v
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("BOT_TOKEN", "123:test")

ORIGIN = "http://localhost:5173"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LifecycleIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        global models, httpx, jwt, select, delete
        import httpx
        from jose import jwt
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        import models

        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.secret = os.environ["JWT_SECRET"]
        self.tag = uuid.uuid4().hex[:8]
        self.order_ids: list[int] = []
        self.coupon_ids: list[int] = []

        async with self.sessions() as db:
            self.store_a = models.Store(code=f"IA{self.tag}", name={"en": "Int A"}, status="ACTIVE",
                                         telegram_staff_group_id="-100111", staff_group_language="en")
            self.store_b = models.Store(code=f"IB{self.tag}", name={"en": "Int B"}, status="ACTIVE")
            db.add_all([self.store_a, self.store_b])
            await db.flush()
            mk = lambda name, role, store: models.Staff(  # noqa: E731
                login_name=f"{name}-{self.tag}", password_hash="unused", role=role, active=True, store_id=store.id)
            self.manager_a, self.staff_a, self.manager_b = (
                mk("ma", "MANAGER", self.store_a), mk("sa", "STAFF", self.store_a), mk("mb", "MANAGER", self.store_b))
            mkc = lambda n: models.Customer(  # noqa: E731
                telegram_user_id=f"it-{n}-{self.tag}", display_name=n, store_id=self.store_a.id,
                preferred_language="en")
            self.cust1, self.cust2 = mkc("c1"), mkc("c2")
            db.add_all([self.manager_a, self.staff_a, self.manager_b, self.cust1, self.cust2])
            await db.flush()
            await db.commit()
            self.ids = {k: getattr(self, k).id for k in
                        ("store_a", "store_b", "manager_a", "staff_a", "manager_b", "cust1", "cust2")}
            self.cust1_tg = self.cust1.telegram_user_id

        # Telegram 全程 mock：记录调用，不联网
        self.send = AsyncMock(return_value=1)
        self.update_card = AsyncMock(return_value=True)
        import telegram_service
        self._patches = [patch.object(telegram_service, "send_bot_message", self.send),
                         patch.object(telegram_service, "update_order_message", self.update_card)]
        for p in self._patches:
            p.start()

        from main import app
        from database import get_db

        async def _db():
            async with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.get_db = get_db

    async def asyncTearDown(self):
        if not hasattr(self, "engine"):
            return
        for p in getattr(self, "_patches", []):
            p.stop()
        self.app.dependency_overrides.pop(self.get_db, None)
        staff_ids = [self.ids[k] for k in ("manager_a", "staff_a", "manager_b")]
        async with self.sessions() as db:
            oids = self.order_ids
            if oids:
                await db.execute(delete(models.PaymentReview).where(models.PaymentReview.order_id.in_(oids)))
                await db.execute(delete(models.OrderRefund).where(models.OrderRefund.order_id.in_(oids)))
                await db.execute(delete(models.OrderIssue).where(models.OrderIssue.order_id.in_(oids)))
                await db.execute(delete(models.OrderEvent).where(models.OrderEvent.order_id.in_(oids)))
                await db.execute(delete(models.CustomerCoupon).where(models.CustomerCoupon.used_order_id.in_(oids)))
                await db.execute(delete(models.OrderItem).where(models.OrderItem.order_id.in_(oids)))
                await db.execute(delete(models.Order).where(models.Order.id.in_(oids)))
            if self.coupon_ids:
                await db.execute(delete(models.CustomerCoupon).where(models.CustomerCoupon.coupon_id.in_(self.coupon_ids)))
                await db.execute(delete(models.CouponCampaign).where(models.CouponCampaign.id.in_(self.coupon_ids)))
            await db.execute(delete(models.AuditLog).where(models.AuditLog.actor_staff_id.in_(staff_ids)))
            await db.commit()
        # 钱包账本是只增不删的（trigger 保护）：用过钱包的客户及其门店、员工无法清理，
        # 留在测试库里即可（编码带随机后缀，不会互相干扰）。
        try:
            async with self.sessions() as db:
                await db.execute(delete(models.Customer).where(models.Customer.id.in_([self.ids["cust1"], self.ids["cust2"]])))
                await db.execute(delete(models.Staff).where(models.Staff.id.in_(staff_ids)))
                await db.execute(delete(models.Store).where(models.Store.id.in_([self.ids["store_a"], self.ids["store_b"]])))
                await db.commit()
        except Exception:  # noqa: BLE001 - 见上方说明
            pass
        await self.engine.dispose()

    # ------------------------------------------------------------------ helpers
    def client(self, who: str | None, kind: str = "staff") -> "httpx.AsyncClient":
        cookies = {}
        if who:
            cookie = "admin_session_token" if kind == "staff" else "session_token"
            cookies[cookie] = jwt.encode({"sub": str(self.ids[who]), "type": kind}, self.secret, algorithm="HS256")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                                 headers={"Origin": ORIGIN}, cookies=cookies)

    async def set_store(self, **fields):
        async with self.sessions() as db:
            store = await db.get(models.Store, self.ids["store_a"])
            for key, value in fields.items():
                setattr(store, key, value)
            await db.commit()

    async def make_order(self, *, order_status="NEW", payment_status="UNPAID", payment_method="MANUAL",
                         customer="cust1", total=1000, wallet_paid=0, external_paid=0, age_minutes=0,
                         items=((200, "Unavailable"), (800, "Remaining")), coupon_id=None,
                         group_message_id=None, with_issue_on_first=False, issue_age_minutes=0,
                         created_at=None, removed=0, paid_confirmed=None):
        async with self.sessions() as db:
            order = models.Order(
                store_id=self.ids["store_a"], public_code=f"IT{uuid.uuid4().hex[:8].upper()}",
                customer_id=self.ids[customer], room_number="IT", order_status=order_status,
                payment_status=payment_status, payment_method=payment_method, currency="USD",
                customer_language="en", total_minor=total, subtotal_minor=total,
                wallet_paid_minor=wallet_paid, external_due_minor=external_paid or (total - wallet_paid),
                paid_confirmed_minor=wallet_paid + external_paid if paid_confirmed is None else paid_confirmed,
                removed_minor=removed, coupon_id=coupon_id,
                telegram_group_message_id=group_message_id, created_at=created_at or (_now() - timedelta(minutes=age_minutes)))
            db.add(order)
            await db.flush()
            rows = []
            for price, name in items:
                rows.append(models.OrderItem(order_id=order.id, product_id=None, product_name_snapshot={"en": name},
                                             unit_price_minor=price, quantity=1, line_total_minor=price))
            db.add_all(rows)
            await db.flush()
            issue_id = None
            if with_issue_on_first:
                issue = models.OrderIssue(store_id=self.ids["store_a"], order_id=order.id, order_item_id=rows[0].id,
                                          issue_type="OUT_OF_STOCK", status="OPEN",
                                          created_at=_now() - timedelta(minutes=issue_age_minutes))
                db.add(issue)
                await db.flush()
                issue_id = issue.id
            await db.commit()
            self.order_ids.append(order.id)
            return {"id": order.id, "code": order.public_code, "items": [r.id for r in rows], "issue_id": issue_id}

    async def order_state(self, order_id: int):
        async with self.sessions() as db:
            order = await db.get(models.Order, order_id)
            return order.order_status, order.payment_status

    async def events(self, order_id: int) -> list[str]:
        async with self.sessions() as db:
            rows = (await db.execute(select(models.OrderEvent.event).where(
                models.OrderEvent.order_id == order_id))).scalars().all()
            return list(rows)

    def sent_to(self, chat: str) -> list:
        return [c for c in self.send.await_args_list if str(c.args[0]) == str(chat)]

    # ------------------------------------------------------- HTTP: customer cancel
    async def test_http_customer_cancel_returns_coupon_and_blocks_others(self):
        async with self.sessions() as db:
            campaign = models.CouponCampaign(
                store_id=self.ids["store_a"], name="IT", coupon_type="FIXED", distribution_mode="PUBLIC",
                fixed_discount_minor=100, starts_at=_now() - timedelta(days=1), ends_at=_now() + timedelta(days=1),
                status="ACTIVE", currency="USD")
            db.add(campaign)
            await db.flush()
            cc = models.CustomerCoupon(coupon_id=campaign.id, customer_id=self.ids["cust1"], status="USED",
                                       used_at=_now())
            db.add(cc)
            await db.commit()
            self.coupon_ids.append(campaign.id)
            cc_id = cc.id
        order = await self.make_order(coupon_id=cc_id)
        async with self.sessions() as db:
            (await db.get(models.CustomerCoupon, cc_id)).used_order_id = order["id"]
            await db.commit()

        path = f"/api/v1/orders/{order['code']}/cancel"
        async with self.client(None) as anon:
            self.assertEqual((await anon.post(path)).status_code, 401)
        async with self.client("cust2", "customer") as other:
            self.assertEqual((await other.post(path)).status_code, 404)
        self.assertEqual(await self.order_state(order["id"]), ("NEW", "UNPAID"))

        async with self.client("cust1", "customer") as owner:
            resp = await owner.post(path)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(await self.order_state(order["id"]), ("CANCELLED", "UNPAID"))
        async with self.sessions() as db:
            coupon = await db.get(models.CustomerCoupon, cc_id)
            self.assertEqual(coupon.status, "AVAILABLE")
            self.assertIsNone(coupon.used_order_id)

    async def test_http_customer_cannot_cancel_accepted_or_proof_submitted(self):
        accepted = await self.make_order(order_status="ACCEPTED")
        proof = await self.make_order(payment_status="PROOF_SUBMITTED")
        async with self.client("cust1", "customer") as owner:
            for order in (accepted, proof):
                resp = await owner.post(f"/api/v1/orders/{order['code']}/cancel")
                self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual((await self.order_state(accepted["id"]))[0], "ACCEPTED")
        self.assertEqual((await self.order_state(proof["id"]))[1], "PROOF_SUBMITTED")

    # ------------------------------------------------- HTTP: refunds & shortage
    async def test_http_refund_endpoints_enforce_role_and_store_isolation(self):
        order = await self.make_order(order_status="CANCELLED", payment_status="PAID_CONFIRMED",
                                      external_paid=1000)
        async with self.sessions() as db:
            refund = models.OrderRefund(store_id=self.ids["store_a"], order_id=order["id"], amount_minor=1000,
                                        method="ABA_MANUAL", status="PENDING", note="it")
            db.add(refund)
            await db.commit()
            rid = refund.id
        listing, complete = "/api/v1/admin/order-refunds", f"/api/v1/admin/order-refunds/{rid}/complete"

        async with self.client(None) as anon:
            self.assertEqual((await anon.get(listing)).status_code, 401)
        async with self.client("staff_a") as waiter:
            self.assertEqual((await waiter.get(listing)).status_code, 403)
            self.assertEqual((await waiter.post(complete, json={"reference": "x"})).status_code, 403)
        async with self.client("manager_b") as other_store:
            ids = [r["id"] for r in (await other_store.get(listing)).json()]
            self.assertNotIn(rid, ids)
            self.assertEqual((await other_store.post(complete, json={"reference": "x"})).status_code, 404)
        async with self.client("manager_a") as manager:
            self.assertIn(rid, [r["id"] for r in (await manager.get(listing)).json()])
            self.assertEqual((await manager.post(complete, json={"reference": "  "})).status_code, 422)
            ok = await manager.post(complete, json={"reference": "ABA-123"})
            self.assertEqual(ok.status_code, 200, ok.text)
            again = await manager.post(complete, json={"reference": "ABA-123"})
            self.assertEqual(again.status_code, 409)
        async with self.sessions() as db:
            row = await db.get(models.OrderRefund, rid)
            self.assertEqual((row.status, row.reference, row.done_by), ("DONE", "ABA-123", self.ids["manager_a"]))

    async def test_http_accept_blocked_while_shortage_is_open(self):
        order = await self.make_order(with_issue_on_first=True)
        path = f"/api/v1/admin/orders/{order['id']}/status"
        async with self.client("manager_a") as manager:
            blocked = await manager.post(path, json={"status": "ACCEPTED"})
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertEqual((await self.order_state(order["id"]))[0], "NEW")
            async with self.sessions() as db:
                (await db.get(models.OrderIssue, order["issue_id"])).status = "RESOLVED"
                await db.commit()
            allowed = await manager.post(path, json={"status": "ACCEPTED"})
            self.assertEqual(allowed.status_code, 200, allowed.text)

    async def test_http_other_store_cannot_report_shortage(self):
        order = await self.make_order()
        path = f"/api/v1/admin/orders/{order['id']}/shortage"
        async with self.client("manager_b") as other_store:
            resp = await other_store.post(path, json={"order_item_id": order["items"][0]})
            self.assertEqual(resp.status_code, 404, resp.text)
        async with self.client("manager_a") as manager:
            resp = await manager.post(path, json={"order_item_id": order["items"][0]})
            self.assertEqual(resp.status_code, 201, resp.text)
            dup = await manager.post(path, json={"order_item_id": order["items"][0]})
            self.assertEqual(dup.status_code, 409)

    # ------------------------------------------ scheduler: unpaid timeout + notices
    async def expire(self) -> int:
        from order_lifecycle import expire_unpaid_orders
        async with self.sessions() as db:
            return await expire_unpaid_orders(db)

    async def test_unpaid_expiry_cancels_and_notifies_customer_and_group(self):
        await self.set_store(unpaid_order_timeout_minutes=30)
        stale = await self.make_order(age_minutes=40, group_message_id="777")
        fresh = await self.make_order(age_minutes=10)
        await self.expire()
        self.assertEqual((await self.order_state(stale["id"]))[0], "CANCELLED")
        self.assertEqual((await self.order_state(fresh["id"]))[0], "NEW")
        self.assertTrue(any(e.startswith("ORDER_CANCELLED") for e in await self.events(stale["id"])))
        self.assertEqual(len(self.sent_to(self.cust1_tg)), 1, "顾客应收到一条取消通知")
        cards = [c for c in self.update_card.await_args_list if c.args[0] == "-100111" and str(c.args[1]) == "777"]
        self.assertEqual(len(cards), 1, "员工群订单卡片应被更新一次")
        before = len(self.send.await_args_list)
        await self.expire()                          # 幂等：不会再次取消/通知
        self.assertEqual(len(self.send.await_args_list), before)

    async def test_unpaid_expiry_disabled_when_timeout_is_zero(self):
        await self.set_store(unpaid_order_timeout_minutes=0)
        old = await self.make_order(age_minutes=600)
        await self.expire()
        self.assertEqual((await self.order_state(old["id"]))[0], "NEW")
        self.assertEqual(self.send.await_count, 0)

    async def test_rejected_order_timeout_restarts_from_rejection(self):
        await self.set_store(unpaid_order_timeout_minutes=30)
        recent = await self.make_order(payment_status="REJECTED", age_minutes=180)
        old = await self.make_order(payment_status="REJECTED", age_minutes=180)
        async with self.sessions() as db:
            db.add_all([
                models.PaymentReview(store_id=self.ids["store_a"], order_id=recent["id"],
                                     staff_id=self.ids["manager_a"], decision="REJECTED",
                                     created_at=_now() - timedelta(minutes=5)),
                models.PaymentReview(store_id=self.ids["store_a"], order_id=old["id"],
                                     staff_id=self.ids["manager_a"], decision="REJECTED",
                                     created_at=_now() - timedelta(minutes=40))])
            await db.commit()
        await self.expire()
        self.assertEqual((await self.order_state(recent["id"]))[0], "NEW", "刚被拒绝的订单不应立即超时")
        self.assertEqual((await self.order_state(old["id"]))[0], "CANCELLED")

    async def test_expiry_reminder_is_sent_once(self):
        await self.set_store(unpaid_order_timeout_minutes=30, unpaid_order_reminder_minutes=5)
        order = await self.make_order(age_minutes=27)
        await self.expire()
        self.assertEqual(len(self.sent_to(self.cust1_tg)), 1)
        self.assertEqual((await self.order_state(order["id"]))[0], "NEW")
        await self.expire()
        self.assertEqual(len(self.sent_to(self.cust1_tg)), 1, "提醒只能发一次")
        self.assertEqual((await self.events(order["id"])).count("EXPIRY_REMINDER_SENT"), 1)

    # ------------------------------------------ scheduler: paid-shortage escalation
    async def test_unanswered_paid_shortage_reminds_then_escalates_once(self):
        await self.set_store(shortage_customer_response_timeout_minutes=20)
        half = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", external_paid=1000,
                                     with_issue_on_first=True, issue_age_minutes=12)
        full = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", external_paid=1000,
                                     customer="cust2", with_issue_on_first=True, issue_age_minutes=25)
        fresh = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", external_paid=1000,
                                      with_issue_on_first=True, issue_age_minutes=2)
        await self.expire()
        self.assertEqual(await self.events(half["id"]), ["SHORTAGE_CUSTOMER_REMINDER_SENT"])
        self.assertEqual(await self.events(full["id"]), ["SHORTAGE_ESCALATED"])
        self.assertEqual(await self.events(fresh["id"]), [])
        group_msgs = self.sent_to("-100111")
        self.assertEqual(len(group_msgs), 1, "只有升级的订单会通知员工群")
        sent_before = len(self.send.await_args_list)
        await self.expire()
        self.assertEqual(len(self.send.await_args_list), sent_before, "重复调度不得重复提醒/升级")
        # 升级不会静默取消或退款
        self.assertEqual(await self.order_state(full["id"]), ("PREPARING", "PAID_CONFIRMED"))
        async with self.sessions() as db:
            count = (await db.execute(select(models.OrderRefund).where(
                models.OrderRefund.order_id == full["id"]))).scalars().all()
            self.assertEqual(count, [])

    async def test_shortage_escalation_off_when_timeout_is_zero(self):
        await self.set_store(shortage_customer_response_timeout_minutes=0)
        order = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", external_paid=1000,
                                      with_issue_on_first=True, issue_age_minutes=600)
        await self.expire()
        self.assertEqual(await self.events(order["id"]), [])

    # ------------------------------------------- mixed payment shortage status
    async def test_mixed_order_item_removal_keeps_partially_refunded_status(self):
        """钱包 300 + ABA 700。删掉 300 的缺货商品：钱包退 300，ABA 部分仍有效，
        订单必须是 PARTIALLY_REFUNDED，不能被标成 REFUNDED。"""
        from routers.orders import OrderIssueResolution, resolve_customer_order_issue
        from wallet_service import adjust_balance, get_payment, spend_up_to
        async with self.sessions() as db:
            await adjust_balance(db, customer_id=self.ids["cust1"], bucket="principal", delta_minor=300,
                                 staff_id=self.ids["manager_a"], reason="it", idem=f"it:{uuid.uuid4().hex}")
            await db.commit()
        order = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED",
                                      payment_method="MIXED", wallet_paid=300, external_paid=700,
                                      items=((300, "Unavailable"), (700, "Remaining")), with_issue_on_first=True)
        async with self.sessions() as db:
            await spend_up_to(db, customer_id=self.ids["cust1"], max_amount_minor=300, biz_id=order["code"],
                              idem=f"it-spend:{order['code']}")
            await db.commit()
        async with self.sessions() as db:
            with patch("order_lifecycle.notify_issue_resolved", new=AsyncMock()), \
                 patch("order_lifecycle.notify_cancelled", new=AsyncMock()):
                await resolve_customer_order_issue(
                    order["code"], order["issue_id"], OrderIssueResolution(resolution="REMOVE_ITEM"),
                    customer_id=self.ids["cust1"], db=db)
        async with self.sessions() as db:
            row = await db.get(models.Order, order["id"])
            payment = await get_payment(db, order["code"])
            refunds = (await db.execute(select(models.OrderRefund).where(
                models.OrderRefund.order_id == order["id"]))).scalars().all()
            self.assertEqual(int(payment["refunded_amount"]), 300)
            self.assertEqual([(r.method, r.amount_minor, r.status) for r in refunds], [("WALLET", 300, "DONE")])
            self.assertEqual(row.total_minor, 700)
            self.assertEqual(row.order_status, "PREPARING", "删品不应改变履约状态")
            self.assertEqual(row.payment_status, "PARTIALLY_REFUNDED")


    # ------------------------------------------------ finance: refund metrics
    async def noon_today(self, days_ago: int = 0) -> datetime:
        """门店时区「今天中午」：落在日报窗口（到 22:30）与统计日内，且不依赖运行时刻。"""
        from zoneinfo import ZoneInfo
        await self.set_store(timezone="Asia/Phnom_Penh", currency="USD")
        tz = ZoneInfo("Asia/Phnom_Penh")
        day = datetime.now(tz).date() - timedelta(days=days_ago)
        return datetime.combine(day, datetime.min.time().replace(hour=12), tz).astimezone(timezone.utc)

    async def add_refund(self, order_id, amount, method, status, *, done_at=None, requires_approval=False):
        async with self.sessions() as db:
            db.add(models.OrderRefund(store_id=self.ids["store_a"], order_id=order_id, amount_minor=amount,
                                      method=method, status=status, done_at=done_at,
                                      requires_approval=requires_approval))
            await db.commit()

    async def build_refund_scenario(self):
        """四张订单覆盖所有口径；前三张的 (已收 - 留存 - 退款记录) 都为 0。"""
        noon, old = await self.noon_today(), await self.noon_today(days_ago=3)
        o1 = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", total=800,
                                   external_paid=1000, removed=200, created_at=noon)
        o2 = await self.make_order(order_status="PREPARING", payment_status="PARTIALLY_REFUNDED",
                                   payment_method="WALLET", total=350, wallet_paid=500, removed=150, created_at=noon)
        o3 = await self.make_order(order_status="CANCELLED", payment_status="PAID_CONFIRMED", total=600,
                                   external_paid=600, created_at=noon)
        o4 = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", total=600,
                                   external_paid=1000, removed=400, created_at=old)
        async with self.sessions() as db:   # 只有 o1 在期内有「审核通过」的 ABA 收款
            db.add(models.PaymentReview(store_id=self.ids["store_a"], order_id=o1["id"],
                                        staff_id=self.ids["manager_a"], decision="APPROVED", created_at=noon))
            await db.commit()
        await self.add_refund(o1["id"], 200, "ABA_MANUAL", "DONE", done_at=noon)
        await self.add_refund(o2["id"], 100, "WALLET", "DONE", done_at=noon)
        await self.add_refund(o2["id"], 50, "WALLET", "PENDING", requires_approval=True)
        await self.add_refund(o3["id"], 600, "ABA_MANUAL", "PENDING")
        await self.add_refund(o4["id"], 400, "ABA_MANUAL", "DONE", done_at=old)   # 3 天前完成，不在今天
        return noon

    async def analytics_today(self, who: str = "manager_a") -> dict:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Phnom_Penh")).date().isoformat()
        async with self.client(who) as manager:
            resp = await manager.get(f"/api/v1/admin/analytics/?from={today}&to={today}")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    async def test_finance_net_cash_deducts_transferred_aba_refunds_by_done_date(self):
        await self.build_refund_scenario()
        row = next(r for r in (await self.analytics_today())["by_currency"] if r["currency"] == "USD")
        self.assertEqual(row["refund_aba_done_minor"], 200, "只统计今天完成转账的 ABA 退款（3 天前那笔不算）")
        self.assertEqual(row["refund_wallet_done_minor"], 100)
        self.assertEqual(row["refund_pending_count"], 2)
        self.assertEqual(row["refund_pending_minor"], 650, "待处理 = ABA 待转 600 + 钱包待审批 50")
        self.assertEqual(row["shortage_removed_minor"], 350, "取消订单与期外订单不计入删品减少额")
        self.assertEqual(row["external_order_receipts_minor"], 1000)
        self.assertEqual(row["cash_inflow_minor"], 1000, "旧字段语义不变")
        self.assertEqual(row["net_cash_inflow_minor"], 800, "净流入 = 1000 - 已转账 ABA 退款 200；钱包退款与待处理不扣")
        self.assertEqual(row["refund_reconcile_diff_minor"], 0)

    async def test_finance_reconcile_difference_flags_unrecorded_refund(self):
        noon = await self.noon_today()
        # 已收 300、留存 100，应退 200，却只记录了 150
        short = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", total=100,
                                      external_paid=300, created_at=noon)
        await self.add_refund(short["id"], 150, "ABA_MANUAL", "PENDING")
        row = next(r for r in (await self.analytics_today())["by_currency"] if r["currency"] == "USD")
        self.assertEqual(row["refund_reconcile_diff_minor"], 50)

    async def test_finance_refund_metrics_respect_store_isolation(self):
        await self.build_refund_scenario()
        for row in (await self.analytics_today("manager_b"))["by_currency"]:
            self.assertNotEqual(row.get("refund_pending_minor", 0), 650, "他店不应看到本店的待处理退款")
            self.assertEqual(row.get("refund_aba_done_minor", 0), 0)

    async def test_daily_report_shows_refund_lines_with_pending_for_managers(self):
        import daily_report
        from zoneinfo import ZoneInfo
        await self.build_refund_scenario()
        tz = ZoneInfo("Asia/Phnom_Penh")
        async with self.sessions() as db:
            report = await daily_report.build_daily_report(
                db, datetime.now(tz).date(), tz, store_id=self.ids["store_a"])
        self.assertEqual(report["refund_wallet_done_minor"], 100)
        self.assertEqual(report["refund_aba_done_minor"], 200)
        self.assertEqual((report["refund_pending_count"], report["refund_pending_minor"]), (2, 650))
        self.assertEqual(report["shortage_removed_minor"], 350)
        self.assertEqual(report["refund_reconcile_diff_minor"], 0)
        text = daily_report.format_daily_report(report, "en")
        self.assertIn("Pending refunds: 2 (6.50 USD)", text)
        self.assertIn("Order refunds · ABA transferred: 2.00 USD", text)
        self.assertIn("Order refunds · wallet: 1.00 USD", text)
        self.assertNotIn("reconciliation", text, "对账一致时不显示差额警告")
        for language in ("zh-CN", "km"):                       # 三语都有，且不暴露裸 key
            self.assertNotIn("report.daily.", daily_report.format_daily_report(report, language))

    async def test_daily_report_hides_refund_lines_when_nothing_happened(self):
        import daily_report
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Phnom_Penh")
        await self.set_store(timezone="Asia/Phnom_Penh", currency="USD")
        async with self.sessions() as db:
            report = await daily_report.build_daily_report(
                db, datetime.now(tz).date(), tz, store_id=self.ids["store_a"])
        text = daily_report.format_daily_report(report, "en")
        self.assertNotIn("Pending refunds", text)
        self.assertNotIn("Order refunds", text)


    async def test_shortage_resolved_group_message_uses_store_currency(self):
        """群里的处理结果不能写死 USD：KHR 无小数位，金额应按币种格式化。"""
        from order_lifecycle import notify_issue_resolved
        order = await self.make_order(order_status="PREPARING", payment_status="PAID_CONFIRMED", external_paid=1000,
                                      group_message_id="555")
        async with self.sessions() as db:
            row = await db.get(models.Order, order["id"])
            row.currency = "KHR"
            await db.commit()
            await notify_issue_resolved(db, row, None, 4500)
        text = self.send.await_args_list[-1].args[1]
        self.assertNotIn("USD", text)
        self.assertIn("KHR", text)
        self.assertIn("4,500", text.replace(" ", ""), text)


    # ------------------------------------------------ B1: 订单搜索 / 日期 / 隔离
    async def _search(self, who="manager_a", **params):
        async with self.client(who) as c:
            resp = await c.get("/api/v1/admin/orders/search", params=params)
        return resp

    async def _local(self, y, m, d, hh=0, mm=0, ss=0):
        from zoneinfo import ZoneInfo
        await self.set_store(timezone="Asia/Phnom_Penh", currency="USD")
        return datetime(y, m, d, hh, mm, ss, tzinfo=ZoneInfo("Asia/Phnom_Penh")).astimezone(timezone.utc)

    async def test_b1_date_range_covers_whole_end_day_in_store_timezone(self):
        cases = {
            "before": await self._local(2026, 9, 30, 23, 59, 59),
            "start": await self._local(2026, 10, 1, 0, 0, 0),
            "end": await self._local(2026, 10, 2, 23, 59, 59),
            "after": await self._local(2026, 10, 3, 0, 0, 0),
        }
        codes = {}
        for name, ts in cases.items():
            codes[name] = (await self.make_order(created_at=ts))["code"]
        resp = await self._search(**{"from": "2026-10-01", "to": "2026-10-02"})
        self.assertEqual(resp.status_code, 200, resp.text)
        found = {row["public_code"] for row in resp.json()["items"]}
        self.assertIn(codes["start"], found)
        self.assertIn(codes["end"], found)
        self.assertNotIn(codes["before"], found)
        self.assertNotIn(codes["after"], found)
        only_from = {r["public_code"] for r in (await self._search(**{"from": "2026-10-02"})).json()["items"]}
        self.assertIn(codes["end"], only_from)
        self.assertIn(codes["after"], only_from)
        self.assertNotIn(codes["start"], only_from)
        only_to = {r["public_code"] for r in (await self._search(**{"to": "2026-10-01"})).json()["items"]}
        self.assertIn(codes["before"], only_to)
        self.assertIn(codes["start"], only_to)
        self.assertNotIn(codes["end"], only_to)
        self.assertEqual((await self._search(**{"from": "2026-10-05", "to": "2026-10-01"})).status_code, 422)
        self.assertEqual((await self._search(**{"from": "not-a-date"})).status_code, 422)

    async def test_b1_search_by_code_nickname_username_and_escapes_wildcards(self):
        async with self.sessions() as db:
            c1 = await db.get(models.Customer, self.ids["cust1"])
            c2 = await db.get(models.Customer, self.ids["cust2"])
            c1.display_name, c1.username = "Aa_Bb", "tea_lover1"
            c2.display_name, c2.username = "AaxBb", "other_user2"
            await db.commit()
        o1 = await self.make_order(customer="cust1")
        o2 = await self.make_order(customer="cust2")

        def codes(resp):
            self.assertEqual(resp.status_code, 200, resp.text)
            return {r["public_code"] for r in resp.json()["items"]}

        self.assertEqual(codes(await self._search(q=o1["code"][2:7])) & {o1["code"], o2["code"]}, {o1["code"]})
        self.assertEqual(codes(await self._search(q="aa_bb")) & {o1["code"], o2["code"]}, {o1["code"]},
                         "下划线必须按字面匹配，不能当通配符匹配到 AaxBb")
        self.assertEqual(codes(await self._search(q="@tea_lover1")) & {o1["code"], o2["code"]}, {o1["code"]})
        self.assertEqual(codes(await self._search(q="%")) & {o1["code"], o2["code"]}, set(), "百分号不是通配符")
        injected = await self._search(q="' OR 1=1 --")
        self.assertEqual(injected.status_code, 200)
        self.assertEqual(codes(injected) & {o1["code"], o2["code"]}, set())
        item = next(r for r in (await self._search(q=o1["code"])).json()["items"] if r["public_code"] == o1["code"])
        self.assertEqual(item["customer"]["username"], "tea_lover1")
        self.assertEqual(item["customer"]["telegram_chat_url"], "https://t.me/tea_lover1")

    async def test_b1_isolation_roles_and_received_amount(self):
        paid = await self.make_order(order_status="PREPARING", payment_status="PARTIALLY_REFUNDED", external_paid=1000,
                                     total=300)
        unpaid = await self.make_order()
        await self.add_refund(paid["id"], 450, "ABA_MANUAL", "DONE", done_at=_now())
        await self.add_refund(paid["id"], 250, "ABA_MANUAL", "PENDING")
        rows = {r["public_code"]: r for r in (await self._search(limit=100)).json()["items"]}
        self.assertEqual(rows[paid["code"]]["received_minor"], 550, "实收 = 已确认收款 1000 - 已完成退款 450")
        self.assertEqual(rows[paid["code"]]["refund_pending_minor"], 250)
        self.assertEqual(rows[unpaid["code"]]["received_minor"], 0)
        # 他店经理：列表看不到，详情 404；本店 STAFF 可用
        other = {r["public_code"] for r in (await self._search("manager_b", limit=100)).json()["items"]}
        self.assertNotIn(paid["code"], other)
        async with self.client("manager_b") as c:
            self.assertEqual((await c.get(f"/api/v1/admin/orders/{paid['code']}")).status_code, 404)
        async with self.client("staff_a") as c:
            detail = await c.get(f"/api/v1/admin/orders/{paid['code']}")
            self.assertEqual(detail.status_code, 200, detail.text)
            body = detail.json()
            self.assertEqual(body["received_minor"], 550)
            self.assertEqual(len(body["refunds"]), 2)
        async with self.client(None) as c:
            self.assertEqual((await c.get("/api/v1/admin/orders/search")).status_code, 401)
        # 非法参数
        self.assertEqual((await self._search(sort="password")).status_code, 422)
        self.assertEqual((await self._search(status="DROP TABLE")).status_code, 422)
        self.assertEqual((await self._search(limit=1000)).status_code, 422)
        async with self.client("manager_a") as c:
            data = (await c.get("/api/v1/admin/orders/counts")).json()
        self.assertGreaterEqual(data["counts"]["NEW"], 1)

    async def test_b1_search_sql_statement_count_does_not_grow_with_rows(self):
        from sqlalchemy import event
        statements = []

        def count(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        for _ in range(3):
            await self.make_order(created_at=await self._local(2026, 8, 1, 12))
        event.listen(self.engine.sync_engine, "before_cursor_execute", count)
        try:
            statements.clear()
            small = await self._search(limit=3, **{"from": "2026-08-01", "to": "2026-08-01"})
            small_count = len(statements)
            for _ in range(22):
                await self.make_order(created_at=await self._local(2026, 8, 1, 13))
            statements.clear()
            large = await self._search(limit=25, **{"from": "2026-08-01", "to": "2026-08-01"})
            large_count = len(statements)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", count)
        self.assertEqual(len(small.json()["items"]), 3)
        self.assertEqual(len(large.json()["items"]), 25)
        self.assertEqual(small_count, large_count, f"3 行 {small_count} 条 SQL，25 行 {large_count} 条：存在 N+1")


if __name__ == "__main__":
    unittest.main()
