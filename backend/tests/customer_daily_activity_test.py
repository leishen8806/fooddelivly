"""B0 tests for store-local, de-duplicated Mini App activity.

The unit tests run without a database.  PostgreSQL tests are enabled with
``RUN_DB_TESTS=1`` and a migrated ``DATABASE_URL``; they exercise the real
unique key and concurrent upsert path.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from customer_activity import activity_date_for_store  # noqa: E402


class CustomerDailyActivityUnitTest(unittest.TestCase):
    def test_phnom_penh_day_boundary(self):
        before_midnight = datetime(2026, 1, 1, 16, 59, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 1, 1, 17, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(activity_date_for_store(before_midnight, "Asia/Phnom_Penh").isoformat(), "2026-01-01")
        self.assertEqual(activity_date_for_store(after_midnight, "Asia/Phnom_Penh").isoformat(), "2026-01-02")

    def test_invalid_or_missing_timezone_uses_default(self):
        instant = datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc)
        expected = activity_date_for_store(instant, "Asia/Phnom_Penh")
        self.assertEqual(activity_date_for_store(instant, None), expected)
        self.assertEqual(activity_date_for_store(instant, "Not/A_Timezone"), expected)

    def test_migration_documents_distinct_historical_and_live_definitions(self):
        migration = pathlib.Path(__file__).parents[1] / "alembic" / "versions" / "p0123456789i_customer_daily_activity.py"
        text = migration.read_text(encoding="utf-8")
        self.assertIn("下单口径", text)
        self.assertIn("打开口径", text)

    def test_activity_write_failure_is_best_effort(self):
        from customer_activity import record_customer_activity_best_effort

        class FakeSession:
            def __init__(self):
                self.commits = 0
                self.rollbacks = 0

            async def commit(self):
                self.commits += 1

            async def rollback(self):
                self.rollbacks += 1

        async def run():
            session = FakeSession()
            with patch("customer_activity.upsert_customer_activity", new=AsyncMock(side_effect=RuntimeError("table unavailable"))):
                ok = await record_customer_activity_best_effort(
                    session, customer_id=1, store_id=None, store_timezone="Asia/Phnom_Penh"
                )
            self.assertFalse(ok)
            self.assertEqual(session.commits, 0)
            self.assertEqual(session.rollbacks, 1)

        asyncio.run(run())


@unittest.skipUnless(os.getenv("RUN_DB_TESTS") == "1", "set RUN_DB_TESTS=1 for PostgreSQL integration tests")
class CustomerDailyActivityPostgresTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from models import Customer, CustomerDailyActivity, Store

        self._select = select
        self._delete = delete
        self.Customer = Customer
        self.CustomerDailyActivity = CustomerDailyActivity
        self.Store = Store
        self.engine = create_async_engine(
            os.environ["DATABASE_URL"], pool_pre_ping=True, pool_size=5, max_overflow=0
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        async with self.sessions() as db:
            self.store = (await db.execute(select(Store).order_by(Store.id).limit(1))).scalars().first()
            if self.store is None:
                self.store = Store(code=f"B0-{uuid.uuid4().hex[:10]}", name={"en": "B0 Test"})
                db.add(self.store)
                await db.flush()
            self.customer = Customer(
                telegram_user_id=f"b0-{uuid.uuid4().hex}",
                display_name="B0 test customer",
                store_id=self.store.id,
            )
            db.add(self.customer)
            await db.commit()
            await db.refresh(self.customer)
            self.customer_ids = [self.customer.id]

    async def asyncTearDown(self):
        async with self.sessions() as db:
            await db.execute(self._delete(self.Customer).where(self.Customer.id.in_(self.customer_ids)))
            await db.commit()
        await self.engine.dispose()

    async def test_repeated_open_same_day_is_one_row_and_last_seen_moves_forward(self):
        from customer_activity import upsert_customer_activity

        first = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        second = first + timedelta(minutes=5)
        async with self.sessions() as db:
            await upsert_customer_activity(db, customer_id=self.customer.id, store_id=self.store.id,
                                            store_timezone=self.store.timezone, now=first)
            await db.commit()
        async with self.sessions() as db:
            await upsert_customer_activity(db, customer_id=self.customer.id, store_id=self.store.id,
                                            store_timezone=self.store.timezone, now=second)
            await db.commit()
        async with self.sessions() as db:
            rows = (await db.execute(self._select(self.CustomerDailyActivity).where(
                self.CustomerDailyActivity.customer_id == self.customer.id))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].first_seen_at, first)
        self.assertEqual(rows[0].last_seen_at, second)

    async def test_concurrent_opens_create_one_row(self):
        from customer_activity import upsert_customer_activity

        moment = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)

        async def write(index: int):
            async with self.sessions() as db:
                await upsert_customer_activity(
                    db,
                    customer_id=self.customer.id,
                    store_id=self.store.id,
                    store_timezone=self.store.timezone,
                    now=moment + timedelta(seconds=index),
                )
                await db.commit()

        await asyncio.gather(*(write(index) for index in range(30)))
        async with self.sessions() as db:
            rows = (await db.execute(self._select(self.CustomerDailyActivity).where(
                self.CustomerDailyActivity.customer_id == self.customer.id))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].last_seen_at, moment + timedelta(seconds=29))

    async def test_telegram_login_records_activity(self):
        from starlette.requests import Request
        from starlette.responses import Response
        from routers.auth import TelegramLoginRequest, telegram_login

        bot_token = "123456:test-token"
        os.environ["BOT_TOKEN"] = bot_token
        os.environ["JWT_SECRET"] = "b0-test-secret"
        auth_date = str(int(time.time()))
        user = {
            "id": 987654321,
            "first_name": "B0",
            "last_name": "Login",
            "username": "b0_login",
            "language_code": "en",
        }
        # Use a new Telegram id for this route test so the login path also
        # covers customer creation and its post-commit activity write.
        user["id"] = int(uuid.uuid4().int % 2_000_000_000) + 1
        data = {
            "auth_date": auth_date,
            "query_id": "b0-query",
            "user": json.dumps(user, separators=(",", ":")),
        }
        check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        request = Request({"type": "http", "method": "POST", "path": "/api/v1/auth/telegram", "headers": []})
        response = Response()
        async with self.sessions() as db:
            result = await telegram_login(TelegramLoginRequest(initData=urlencode(data)), response, request, db)
            self.assertEqual(result["message"], "Login successful")
            customer = (await db.execute(self._select(self.Customer).where(
                self.Customer.telegram_user_id == str(user["id"])))).scalars().one()
            self.customer = customer
            self.customer_ids.append(customer.id)
            rows = (await db.execute(self._select(self.CustomerDailyActivity).where(
                self.CustomerDailyActivity.customer_id == customer.id))).scalars().all()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
