"""HTTP coverage for the admin coupon list and localized share links.

Run with a PostgreSQL 15 database after migrations:
    RUN_DB_TESTS=1 DATABASE_URL=postgresql+asyncpg://... JWT_SECRET=... \
        python -m unittest tests/coupon_admin_test.py -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from sqlalchemy import delete

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("BOT_USERNAME", "teacafe_test_bot")


class CouponAdminIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        import httpx
        from jose import jwt
        from sqlalchemy import delete, event, select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        import models

        self.httpx = httpx
        self.jwt = jwt
        self.models = models
        self.event = event
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.secret = os.environ["JWT_SECRET"]
        self.tag = uuid.uuid4().hex[:8]
        async with self.sessions() as db:
            self.store = models.Store(code=f"CP{self.tag}", name={"en": "Coupon test"}, status="ACTIVE")
            self.manager = models.Staff(login_name=f"coupon-manager-{self.tag}", password_hash="unused",
                                        role="MANAGER", active=True)
            db.add_all([self.store, self.manager])
            await db.flush()
            self.manager.store_id = self.store.id
            self.coupons = []
            for index in range(25):
                coupon = models.CouponCampaign(
                    store_id=self.store.id, created_by=self.manager.id, name=f"Coupon {index}",
                    currency="USD", coupon_type="FIXED", distribution_mode="PUBLIC",
                    min_spend_minor=1000, fixed_discount_minor=123,
                    starts_at=datetime.now(timezone.utc) - timedelta(days=1),
                    ends_at=datetime.now(timezone.utc) + timedelta(days=1),
                    status="ACTIVE", total_claim_limit=100,
                )
                db.add(coupon)
                self.coupons.append(coupon)
            await db.flush()
            for coupon in self.coupons:
                db.add(models.CouponClaimLink(
                    coupon_id=coupon.id, token_value=f"raw-{coupon.id}-{self.tag}",
                    token_hash=f"hash-{coupon.id}-{self.tag}", mode="PUBLIC", status="ACTIVE",
                    created_by=self.manager.id,
                ))
            await db.commit()
            self.store_id = self.store.id
            self.manager_id = self.manager.id
            self.coupon_ids = [coupon.id for coupon in self.coupons]

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
        async with self.sessions() as db:
            await db.execute(delete(self.models.CouponClaimLink).where(self.models.CouponClaimLink.coupon_id.in_(self.coupon_ids)))
            await db.execute(delete(self.models.CouponCampaign).where(self.models.CouponCampaign.id.in_(self.coupon_ids)))
            await db.execute(delete(self.models.Staff).where(self.models.Staff.id == self.manager_id))
            await db.execute(delete(self.models.Store).where(self.models.Store.id == self.store_id))
            await db.commit()
        self.app.dependency_overrides.pop(self.get_db, None)
        await self.engine.dispose()

    def client(self, staff_id: int | None = None):
        token = self.jwt.encode({"sub": str(staff_id or self.manager_id), "type": "staff"}, self.secret, algorithm="HS256")
        return self.httpx.AsyncClient(
            transport=self.httpx.ASGITransport(app=self.app), base_url="http://test",
            cookies={"admin_session_token": token}, headers={"Origin": "http://localhost:5173"},
        )

    async def test_list_uses_one_link_query_for_all_rows_and_localizes_share_text(self):
        statements: list[str] = []

        def before_cursor_execute(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        self.event.listen(self.engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        try:
            async with self.client() as client:
                response = await client.get("/api/v1/admin/coupons")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(len(payload), 25)
            self.assertLessEqual(len(statements), 4, statements)
            self.assertEqual(len({statement for statement in statements if "coupon_claim_links" in statement.lower()}), 1)
            urls = payload[0]["public_share_urls"]
            self.assertEqual(set(urls), {"zh-CN", "en", "km"})
            self.assertIn("满额减 1.23 USD", unquote(urls["zh-CN"]))
            self.assertIn("1.23 USD OFF", unquote(urls["en"]))
        finally:
            self.event.remove(self.engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    async def test_store_manager_cannot_write_shared_coupon_catalog_when_multistore(self):
        async with self.client() as client:
            response = await client.post("/api/v1/admin/coupons", json={
                "name": "Should be blocked", "coupon_type": "FIXED", "distribution_mode": "PUBLIC",
                "fixed_discount_minor": 100, "starts_at": "2026-10-01T00:00:00Z",
                "ends_at": "2026-11-01T00:00:00Z",
            })
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
