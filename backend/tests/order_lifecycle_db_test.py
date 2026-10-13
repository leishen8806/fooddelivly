"""PostgreSQL integration checks for paid shortage refunds.

Run inside the backend container after migrations with RUN_DB_TESTS=1.  These
tests exercise the actual customer issue-resolution route function against a
real database and wallet schema; the small notification patch keeps Telegram
out of the transaction test.
"""
from __future__ import annotations

import os
import uuid
import unittest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class PaidShortageRefundDbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        global Customer, Order, OrderEvent, OrderIssue, OrderItem, OrderRefund, Staff, Store
        global RefundApprovalRequest, approve_wallet_refund, OrderIssueResolution, resolve_customer_order_issue
        global RefundRequest, refund_wallet_order
        global adjust_balance, get_payment, spend_up_to
        from models import Customer, Order, OrderEvent, OrderIssue, OrderItem, OrderRefund, Staff, Store
        global RefundCompletionRequest, complete_order_refund
        from routers.admin_orders import (RefundApprovalRequest, RefundCompletionRequest, RefundRequest,
                                           approve_wallet_refund, complete_order_refund, refund_wallet_order)
        from routers.orders import OrderIssueResolution, resolve_customer_order_issue
        from wallet_service import adjust_balance, get_payment, spend_up_to
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.order_ids: list[int] = []
        self.extra_customer_ids: list[int] = []
        self.extra_staff_ids: list[int] = []
        self.extra_store_ids: list[int] = []
        async with self.sessions() as db:
            self.store = (await db.execute(select(Store).order_by(Store.id).limit(1))).scalars().first()
            if self.store is None:
                self.store = Store(code="DBTEST", name={"en": "DB Test"}, status="ACTIVE")
                db.add(self.store)
                await db.flush()
            self.store.shortage_refund_review_minor = 0
            self.staff = Staff(login_name=f"dbtest-{uuid.uuid4().hex[:10]}", password_hash="unused",
                               role="MANAGER", active=True, store_id=self.store.id)
            self.customer = Customer(telegram_user_id=f"dbtest-{uuid.uuid4().hex}", display_name="DB Test",
                                     store_id=self.store.id, preferred_language="en")
            db.add_all([self.staff, self.customer])
            await db.flush()
            await db.commit()
            self.store_id, self.staff_id, self.customer_id = self.store.id, self.staff.id, self.customer.id

    async def asyncTearDown(self):
        async with self.sessions() as db:
            for order_id in self.order_ids:
                await db.execute(delete(OrderRefund).where(OrderRefund.order_id == order_id))
                await db.execute(delete(OrderIssue).where(OrderIssue.order_id == order_id))
                await db.execute(delete(OrderEvent).where(OrderEvent.order_id == order_id))
                await db.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
                await db.execute(delete(Order).where(Order.id == order_id))
            for customer_id in self.extra_customer_ids:
                await db.execute(delete(Customer).where(Customer.id == customer_id))
            for staff_id in self.extra_staff_ids:
                await db.execute(delete(Staff).where(Staff.id == staff_id))
            for store_id in self.extra_store_ids:
                await db.execute(delete(Store).where(Store.id == store_id))
            await db.commit()
        await self.engine.dispose()

    async def _make_order(self, *, wallet_paid: int, external_paid: int) -> tuple[int, int, int]:
        async with self.sessions() as db:
            order = Order(store_id=self.store_id, public_code=f"DB{uuid.uuid4().hex[:8].upper()}",
                          customer_id=self.customer_id, room_number="DB", order_status="ACCEPTED",
                          payment_status="PAID_CONFIRMED", payment_method="WALLET" if not external_paid else "MIXED",
                          currency="USD", customer_language="en", total_minor=1000, subtotal_minor=1000,
                          wallet_paid_minor=wallet_paid, external_due_minor=external_paid,
                          paid_confirmed_minor=wallet_paid + external_paid)
            db.add(order)
            await db.flush()
            first = OrderItem(order_id=order.id, product_id=None, product_name_snapshot={"en": "Unavailable"},
                              unit_price_minor=200, quantity=1, line_total_minor=200)
            second = OrderItem(order_id=order.id, product_id=None, product_name_snapshot={"en": "Remaining"},
                               unit_price_minor=800, quantity=1, line_total_minor=800)
            db.add_all([first, second])
            await db.flush()
            issue = OrderIssue(store_id=self.store_id, order_id=order.id, order_item_id=first.id,
                               issue_type="OUT_OF_STOCK", status="OPEN", note="integration")
            db.add(issue)
            await db.commit()
            self.order_ids.append(order.id)
            return order.id, first.id, second.id

    async def _resolve(self, public_code: str, issue_id: int, resolution: str):
        async with self.sessions() as db:
            with patch("order_lifecycle.notify_issue_resolved", new=AsyncMock()), \
                 patch("order_lifecycle.notify_cancelled", new=AsyncMock()):
                return await resolve_customer_order_issue(
                    public_code, issue_id, OrderIssueResolution(resolution=resolution),
                    customer_id=self.customer_id, db=db,
                )

    async def test_aba_shortage_then_cancel_only_creates_remaining_refund(self):
        order_id, first_id, second_id = await self._make_order(wallet_paid=0, external_paid=1000)
        async with self.sessions() as db:
            order = await db.get(Order, order_id)
            issue = (await db.execute(select(OrderIssue).filter(OrderIssue.order_id == order_id))).scalars().first()
            code = order.public_code
        await self._resolve(code, issue.id, "REMOVE_ITEM")
        async with self.sessions() as db:
            refunds = (await db.execute(select(OrderRefund).filter(OrderRefund.order_id == order_id))).scalars().all()
            self.assertEqual(sum(r.amount_minor for r in refunds), 200)
            second_issue = OrderIssue(store_id=self.store_id, order_id=order_id, order_item_id=second_id,
                                      issue_type="OUT_OF_STOCK", status="OPEN")
            db.add(second_issue)
            await db.commit()
            second_issue_id = second_issue.id
        await self._resolve(code, second_issue_id, "CANCEL_ORDER")
        async with self.sessions() as db:
            refunds = (await db.execute(select(OrderRefund).filter(OrderRefund.order_id == order_id))).scalars().all()
            order = await db.get(Order, order_id)
            self.assertEqual(sum(r.amount_minor for r in refunds), 1000)
            self.assertEqual(sorted(r.amount_minor for r in refunds), [200, 800])
            self.assertEqual(order.payment_status, "PARTIALLY_REFUNDED")
            pending_rows = [r for r in refunds if r.status == "PENDING"]
            completed = None
            for pending in pending_rows:
                completed = await complete_order_refund(
                    pending.id, RefundCompletionRequest(reference="aba-db-test"),
                    manager_info={"staff_id": self.staff_id, "role": "MANAGER", "store_id": self.store_id}, db=db,
                )
            self.assertEqual(completed["payment_status"], "REFUNDED")

    async def test_wallet_shortage_then_cancel_refunds_wallet_once(self):
        async with self.sessions() as db:
            await adjust_balance(db, customer_id=self.customer_id, bucket="principal", delta_minor=1000,
                                 staff_id=self.staff_id, reason="integration", idem=f"dbtest:{uuid.uuid4().hex}")
            await db.commit()
        order_id, first_id, second_id = await self._make_order(wallet_paid=1000, external_paid=0)
        async with self.sessions() as db:
            order = await db.get(Order, order_id)
            await spend_up_to(db, customer_id=self.customer_id, max_amount_minor=1000,
                              biz_id=order.public_code, idem=f"dbtest-spend:{order.public_code}")
            await db.commit()
            issue = (await db.execute(select(OrderIssue).filter(OrderIssue.order_id == order_id))).scalars().first()
            code = order.public_code
        await self._resolve(code, issue.id, "REMOVE_ITEM")
        async with self.sessions() as db:
            second_issue = OrderIssue(store_id=self.store_id, order_id=order_id, order_item_id=second_id,
                                      issue_type="OUT_OF_STOCK", status="OPEN")
            db.add(second_issue)
            await db.commit()
            second_issue_id = second_issue.id
        await self._resolve(code, second_issue_id, "CANCEL_ORDER")
        async with self.sessions() as db:
            payment = await get_payment(db, code)
            refunds = (await db.execute(select(OrderRefund).filter(OrderRefund.order_id == order_id))).scalars().all()
            order = await db.get(Order, order_id)
            self.assertEqual(int(payment["refunded_amount"]), 1000)
            self.assertEqual(sum(r.amount_minor for r in refunds if r.method == "WALLET"), 1000)
            self.assertEqual(order.payment_status, "REFUNDED")

    async def test_wallet_shortage_above_threshold_waits_for_manager_approval(self):
        async with self.sessions() as db:
            store = await db.get(Store, self.store_id)
            store.shortage_refund_review_minor = 100
            await adjust_balance(db, customer_id=self.customer_id, bucket="principal", delta_minor=1000,
                                 staff_id=self.staff_id, reason="integration", idem=f"dbtest:{uuid.uuid4().hex}")
            await db.commit()
        order_id, first_id, _ = await self._make_order(wallet_paid=1000, external_paid=0)
        async with self.sessions() as db:
            order = await db.get(Order, order_id)
            await spend_up_to(db, customer_id=self.customer_id, max_amount_minor=1000,
                              biz_id=order.public_code, idem=f"dbtest-spend:{order.public_code}")
            issue = (await db.execute(select(OrderIssue).filter(OrderIssue.order_id == order_id))).scalars().first()
            await db.commit()
            code = order.public_code
        await self._resolve(code, issue.id, "REMOVE_ITEM")
        async with self.sessions() as db:
            refund = (await db.execute(select(OrderRefund).filter(OrderRefund.order_id == order_id))).scalars().first()
            self.assertEqual(refund.status, "PENDING")
            self.assertTrue(refund.requires_approval)
            payment = await get_payment(db, code)
            self.assertEqual(int(payment["refunded_amount"]), 0)
            approved = await approve_wallet_refund(
                refund.id, RefundApprovalRequest(),
                manager_info={"staff_id": self.staff_id, "role": "MANAGER", "store_id": self.store_id}, db=db,
            )
            self.assertEqual(approved["status"], "DONE")
            payment = await get_payment(db, code)
            self.assertEqual(int(payment["refunded_amount"]), 200)

    async def test_concurrent_wallet_refund_cannot_over_refund(self):
        order_id, _, _ = await self._make_order(wallet_paid=1000, external_paid=0)
        async with self.sessions() as db:
            order = await db.get(Order, order_id)
            await adjust_balance(db, customer_id=self.customer_id, bucket="principal", delta_minor=1000,
                                 staff_id=self.staff_id, reason="concurrency", idem=f"dbtest:{uuid.uuid4().hex}")
            await spend_up_to(db, customer_id=self.customer_id, max_amount_minor=1000,
                              biz_id=order.public_code, idem=f"dbtest-spend:{order.public_code}")
            await db.commit()
            code = order.public_code

        async def attempt():
            async with self.sessions() as db:
                try:
                    result = await refund_wallet_order(
                        order_id, RefundRequest(reason="concurrent test"),
                        staff_info={"staff_id": self.staff_id, "role": "MANAGER", "store_id": self.store_id,
                                    "login_name": "dbtest"}, db=db,
                    )
                    return ("ok", result)
                except Exception as exc:  # second caller is expected to see 409 after the row lock
                    return ("error", getattr(exc, "status_code", None))

        outcomes = await asyncio.gather(attempt(), attempt())
        self.assertEqual(sum(1 for kind, _ in outcomes if kind == "ok"), 1)
        self.assertEqual(sum(1 for kind, detail in outcomes if kind == "error" and detail == 409), 1)
        async with self.sessions() as db:
            payment = await get_payment(db, code)
            self.assertEqual(int(payment["refunded_amount"]), 1000)

    async def test_cross_store_refund_endpoint_returns_not_found(self):
        async with self.sessions() as db:
            branch = Store(code=f"DB{uuid.uuid4().hex[:6].upper()}", name={"en": "Branch"}, status="ACTIVE")
            db.add(branch)
            await db.flush()
            branch_customer = Customer(telegram_user_id=f"dbbranch-{uuid.uuid4().hex}", store_id=branch.id,
                                       display_name="Branch customer", preferred_language="en")
            branch_staff = Staff(login_name=f"dbbranch-{uuid.uuid4().hex[:10]}", password_hash="unused",
                                 role="MANAGER", active=True, store_id=branch.id)
            db.add_all([branch_customer, branch_staff])
            await db.flush()
            order = Order(store_id=branch.id, public_code=f"BR{uuid.uuid4().hex[:8].upper()}",
                          customer_id=branch_customer.id, room_number="B", order_status="CANCELLED",
                          payment_status="PARTIALLY_REFUNDED", payment_method="MANUAL", currency="USD",
                          customer_language="en", total_minor=1000, subtotal_minor=1000,
                          external_due_minor=1000, paid_confirmed_minor=1000)
            db.add(order)
            await db.flush()
            refund = OrderRefund(store_id=branch.id, order_id=order.id, amount_minor=1000,
                                 method="ABA_MANUAL", status="PENDING")
            db.add(refund)
            await db.flush()
            await db.commit()
            self.order_ids.append(order.id)
            self.extra_customer_ids.append(branch_customer.id)
            self.extra_staff_ids.append(branch_staff.id)
            self.extra_store_ids.append(branch.id)
            refund_id = refund.id
        async with self.sessions() as db:
            try:
                await complete_order_refund(
                    refund_id, RefundCompletionRequest(reference="cross-store"),
                    manager_info={"staff_id": self.staff_id, "role": "MANAGER", "store_id": self.store_id}, db=db,
                )
            except Exception as exc:
                self.assertEqual(getattr(exc, "status_code", None), 404)
            else:
                self.fail("a manager from another store completed the refund")


if __name__ == "__main__":
    unittest.main()
