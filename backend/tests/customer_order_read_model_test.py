"""HTTP coverage for the customer order read model.

Run with the real PostgreSQL test database:
RUN_DB_TESTS=1 ALLOWED_ORIGINS=http://localhost:5173 python -m unittest tests.customer_order_read_model_test -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")


class CustomerOrderReadModelHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        global models, httpx, jwt, delete, event
        import httpx
        from jose import jwt
        from sqlalchemy import delete, event
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        import models

        self.models = models
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.secret = os.environ["JWT_SECRET"]
        self.tag = uuid.uuid4().hex[:8]
        self.issue_query_count = 0

        async with self.sessions() as db:
            self.store = models.Store(code=f"CR{self.tag}", name={"en": "Customer read"}, status="ACTIVE",
                                       timezone="Asia/Phnom_Penh", currency="USD")
            self.owner = models.Customer(telegram_user_id=f"customer-read-owner-{self.tag}", display_name="Owner",
                                         preferred_language="en", store_id=None)
            self.other = models.Customer(telegram_user_id=f"customer-read-other-{self.tag}", display_name="Other",
                                         preferred_language="en", store_id=None)
            db.add_all([self.store, self.owner, self.other])
            await db.flush()
            self.order_partial = await self._make_order(db, "PARTIALLY_REFUNDED", 700, 1000, "PARTIAL")
            self.order_full = await self._make_order(db, "REFUNDED", 0, 500, "FULL")
            self.order_pending = await self._make_order(db, "PARTIALLY_REFUNDED", 600, 700, "PENDING")
            db.add_all([
                models.OrderRefund(store_id=self.store.id, order_id=self.order_partial.id, amount_minor=200,
                                   method="WALLET", status="DONE"),
                models.OrderRefund(store_id=self.store.id, order_id=self.order_partial.id, amount_minor=100,
                                   method="ABA_MANUAL", status="PENDING"),
                models.OrderRefund(store_id=self.store.id, order_id=self.order_full.id, amount_minor=500,
                                   method="WALLET", status="DONE"),
                models.OrderRefund(store_id=self.store.id, order_id=self.order_pending.id, amount_minor=100,
                                   method="ABA_MANUAL", status="PENDING"),
            ])
            await db.flush()
            db.add_all([
                models.OrderIssue(store_id=self.store.id, order_id=self.order_partial.id,
                                  order_item_id=self.order_partial._test_item_id, status="OPEN", issue_type="OUT_OF_STOCK"),
                models.OrderIssue(store_id=self.store.id, order_id=self.order_partial.id,
                                  order_item_id=self.order_partial._test_item_id, status="OPEN", issue_type="OUT_OF_STOCK"),
                models.OrderIssue(store_id=self.store.id, order_id=self.order_partial.id,
                                  order_item_id=self.order_partial._test_item_id, status="RESOLVED", issue_type="OUT_OF_STOCK"),
            ])
            await db.commit()

        from sqlalchemy import event as sqlalchemy_event
        sqlalchemy_event.listen(self.engine.sync_engine, "before_cursor_execute", self._count_issue_query)
        from main import app
        from database import get_db

        async def _db():
            async with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.get_db = get_db

    async def _make_order(self, db, payment_status: str, total: int, paid: int, suffix: str):
        order = self.models.Order(
            store_id=self.store.id, customer_id=self.owner.id, public_code=f"CR{suffix}{self.tag[:5]}",
            room_number="101", order_status="PREPARING", payment_status=payment_status,
            payment_method="MIXED", currency="USD", customer_language="en", total_minor=total,
            subtotal_minor=total, wallet_paid_minor=paid // 2, external_due_minor=paid - paid // 2,
            paid_confirmed_minor=paid,
        )
        db.add(order)
        await db.flush()
        item = self.models.OrderItem(order_id=order.id, product_id=None, product_name_snapshot={"en": suffix},
                                     unit_price_minor=max(total, 1), quantity=1, line_total_minor=max(total, 1))
        db.add(item)
        await db.flush()
        order._test_item_id = item.id
        return order

    def _count_issue_query(self, conn, cursor, statement, parameters, context, executemany):
        if "order_issues" in statement.lower():
            self.issue_query_count += 1

    async def asyncTearDown(self):
        if not hasattr(self, "engine"):
            return
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._count_issue_query)
        self.app.dependency_overrides.pop(self.get_db, None)
        async with self.sessions() as db:
            order_ids = [self.order_partial.id, self.order_full.id, self.order_pending.id]
            await db.execute(delete(self.models.OrderRefund).where(self.models.OrderRefund.order_id.in_(order_ids)))
            await db.execute(delete(self.models.OrderIssue).where(self.models.OrderIssue.order_id.in_(order_ids)))
            await db.execute(delete(self.models.OrderItem).where(self.models.OrderItem.order_id.in_(order_ids)))
            await db.execute(delete(self.models.Order).where(self.models.Order.id.in_(order_ids)))
            await db.execute(delete(self.models.Customer).where(self.models.Customer.id.in_([self.owner.id, self.other.id])))
            await db.execute(delete(self.models.Store).where(self.models.Store.id == self.store.id))
            await db.commit()
        await self.engine.dispose()

    def client(self, customer_id: int):
        token = jwt.encode({"sub": str(customer_id), "type": "customer"}, self.secret, algorithm="HS256")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                                 headers={"Origin": "http://localhost:5173"}, cookies={"session_token": token})

    async def test_detail_aggregates_refunds_and_enforces_customer_scope(self):
        async with self.client(self.owner.id) as client:
            partial = await client.get(f"/api/v1/orders/{self.order_partial.public_code}")
            full = await client.get(f"/api/v1/orders/{self.order_full.public_code}")
            pending = await client.get(f"/api/v1/orders/{self.order_pending.public_code}")
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json()["paid_confirmed_minor"], 1000)
        self.assertEqual(partial.json()["refunded_done_minor"], 200)
        self.assertEqual(partial.json()["refund_pending_minor"], 100)
        self.assertNotIn("reference", partial.json())
        self.assertEqual(full.json()["refunded_done_minor"], 500)
        self.assertEqual(full.json()["refund_pending_minor"], 0)
        self.assertEqual(pending.json()["refund_pending_minor"], 100)

        async with self.client(self.other.id) as other:
            forbidden = await other.get(f"/api/v1/orders/{self.order_partial.public_code}")
        self.assertEqual(forbidden.status_code, 404, forbidden.text)

    async def test_list_counts_open_issues_with_one_aggregate_query(self):
        self.issue_query_count = 0
        async with self.client(self.owner.id) as client:
            response = await client.get("/api/v1/orders")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {row["public_code"]: row for row in response.json()}
        self.assertEqual(rows[self.order_partial.public_code]["open_issue_count"], 2)
        self.assertEqual(rows[self.order_full.public_code]["open_issue_count"], 0)
        self.assertEqual(self.issue_query_count, 1)


if __name__ == "__main__":
    unittest.main()
