"""HTTP coverage for the D/E/G admin surfaces (PostgreSQL only)."""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


class AdminV2RemainingHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        import httpx
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from jose import jwt
        import models
        from database import get_db
        from main import app

        self.httpx = httpx
        self.jwt = jwt
        self.models = models
        self.delete = delete
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.tag = uuid.uuid4().hex[:8]
        async with self.sessions() as db:
            self.store = models.Store(code=f"D{self.tag}", name={"en": "D test"}, status="ACTIVE", timezone="Asia/Phnom_Penh", telegram_staff_group_id=None)
            db.add(self.store); await db.flush()
            self.manager = models.Staff(login_name=f"dm-{self.tag}", password_hash="unused", role="MANAGER", store_id=self.store.id, active=True)
            self.staff = models.Staff(login_name=f"ds-{self.tag}", password_hash="unused", role="STAFF", store_id=self.store.id, active=True)
            db.add_all([self.manager, self.staff]); await db.commit()
        async def _db():
            async with self.sessions() as session:
                yield session
        self.app = app; self.app.dependency_overrides[get_db] = _db

    async def asyncTearDown(self):
        from sqlalchemy import delete
        async with self.sessions() as db:
            await db.execute(delete(self.models.AuditLog).where(self.models.AuditLog.actor_staff_id.in_([self.manager.id, self.staff.id])))
            await db.execute(delete(self.models.Staff).where(self.models.Staff.id.in_([self.manager.id, self.staff.id])))
            await db.execute(delete(self.models.Store).where(self.models.Store.id == self.store.id))
            await db.commit()
        self.app.dependency_overrides.clear(); await self.engine.dispose()

    async def client(self):
        transport = self.httpx.ASGITransport(app=self.app)
        client = self.httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://localhost:5173"})
        client.cookies.set("admin_session_token", self.jwt.encode({"sub": str(self.manager.id), "type": "staff", "role": "MANAGER"}, os.environ["JWT_SECRET"], algorithm="HS256"))
        return client

    async def test_staff_security_settings_dashboard_and_refunds_routes(self):
        client = await self.client()
        try:
            me = await client.get("/api/v1/auth/admin/me")
            self.assertEqual(me.status_code, 200); self.assertIn("last_login_at", me.json())
            rows = await client.get("/api/v1/admin/staff")
            self.assertEqual(rows.status_code, 200)
            from unittest.mock import patch
            with patch("routers.admin_staff.get_password_hash", return_value="hash"):
                reset = await client.post(f"/api/v1/admin/staff/{self.staff.id}/reset-password")
            self.assertEqual(reset.status_code, 200); self.assertTrue(reset.json()["temporary_password"])
            summary = await client.get("/api/v1/admin/settings/summary")
            self.assertEqual(summary.status_code, 200); self.assertIn("business", summary.json()["groups"])
            dashboard = await client.get("/api/v1/admin/dashboard?days=7")
            self.assertEqual(dashboard.status_code, 200, dashboard.text); self.assertEqual(len(dashboard.json()["series"]), 7)
            refunds = await client.get("/api/v1/admin/order-refunds?status=PENDING")
            self.assertEqual(refunds.status_code, 200)
        finally:
            await client.aclose()
