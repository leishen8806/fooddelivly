"""HTTP regression coverage for the post-v2 admin fixes."""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


class AdminFixPatchHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        import httpx
        from jose import jwt
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        import models
        from database import get_db
        from main import app

        self.httpx, self.jwt, self.models, self.delete, self.app = httpx, jwt, models, delete, app
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.tag = uuid.uuid4().hex[:8]
        from auth_utils import get_password_hash
        async with self.sessions() as db:
            self.store = models.Store(code=f"P{self.tag}", name={"en": "Patch test"}, status="ACTIVE", timezone="Asia/Phnom_Penh", currency="USD", telegram_staff_group_id=f"-100{self.tag[:8]}")
            db.add(self.store)
            await db.flush()
            self.forced = models.Staff(login_name=f"forced-{self.tag}", password_hash=get_password_hash("Old-password-123"), role="MANAGER", store_id=self.store.id, active=True, must_change_password=True)
            self.normal = models.Staff(login_name=f"normal-{self.tag}", password_hash=get_password_hash("Old-password-123"), role="MANAGER", store_id=self.store.id, active=True, must_change_password=False)
            self.hq = models.Staff(login_name=f"hq-{self.tag}", password_hash=get_password_hash("Old-password-123"), role="MANAGER", store_id=None, active=True, must_change_password=False)
            db.add_all([self.forced, self.normal, self.hq])
            await db.commit()

        async def _db():
            async with self.sessions() as session:
                yield session
        self.app.dependency_overrides[get_db] = _db

    async def asyncTearDown(self):
        async with self.sessions() as db:
            await db.execute(self.delete(self.models.AuditLog).where(self.models.AuditLog.actor_staff_id.in_([self.forced.id, self.normal.id, self.hq.id])))
            await db.execute(self.delete(self.models.Staff).where(self.models.Staff.id.in_([self.forced.id, self.normal.id, self.hq.id])))
            await db.execute(self.delete(self.models.Store).where(self.models.Store.id == self.store.id))
            await db.commit()
        self.app.dependency_overrides.clear()
        await self.engine.dispose()

    async def client(self, staff, *, base_url="http://test"):
        client = self.httpx.AsyncClient(transport=self.httpx.ASGITransport(app=self.app), base_url=base_url, headers={"Origin": "http://localhost:5173"})
        client.cookies.set("admin_session_token", self.jwt.encode({"sub": str(staff.id), "type": "staff", "role": staff.role}, os.environ["JWT_SECRET"], algorithm="HS256"))
        return client

    async def test_forced_password_gate_and_audit(self):
        client = await self.client(self.forced)
        try:
            self.assertEqual((await client.get("/api/v1/auth/admin/me")).status_code, 200)
            self.assertEqual((await client.post("/api/v1/auth/admin/logout")).status_code, 200)
            self.assertEqual((await client.get("/api/v1/admin/settings/summary")).status_code, 403)
            response = await client.post("/api/v1/auth/admin/change-password", json={"password": "New-password-123"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await client.get("/api/v1/admin/settings/summary")).status_code, 200)
            async with self.sessions() as db:
                audit = (await db.execute(__import__("sqlalchemy").select(self.models.AuditLog).where(self.models.AuditLog.actor_staff_id == self.forced.id))).scalars().all()
                self.assertEqual(len(audit), 1)
                self.assertNotIn("password", str(audit[0].details).lower())
        finally:
            await client.aclose()

    async def test_normal_password_change_requires_old_password(self):
        client = await self.client(self.normal)
        try:
            self.assertEqual((await client.post("/api/v1/auth/admin/change-password", json={"password": "New-password-123"})).status_code, 403)
            response = await client.post("/api/v1/auth/admin/change-password", json={"old_password": "Old-password-123", "password": "New-password-123"})
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            await client.aclose()

    async def test_notification_reports_configuration_and_provider_errors(self):
        client = await self.client(self.normal)
        try:
            class FakeSession:
                async def close(self): pass
            class FakeBot:
                def __init__(self, error=None): self.session, self.error = FakeSession(), error
                async def send_message(self, **_kwargs):
                    if self.error: raise self.error
            with patch("routers.admin_settings._bot", return_value=None):
                response = await client.post(f"/api/v1/admin/settings/test-notification?store={self.store.code}", json={"message": "x"})
                self.assertFalse(response.json()["ok"])
                self.assertIn("bot", response.json()["reason"].lower())
            with patch("routers.admin_settings._bot", return_value=FakeBot(RuntimeError("telegram down"))):
                response = await client.post(f"/api/v1/admin/settings/test-notification?store={self.store.code}", json={"message": "x"})
                self.assertFalse(response.json()["ok"])
                self.assertIn("Telegram send failed", response.json()["reason"])
            async with self.sessions() as db:
                store = await db.get(self.models.Store, self.store.id)
                store.telegram_staff_group_id = None
                await db.commit()
            with patch("routers.admin_settings._bot", return_value=FakeBot()):
                response = await client.post(f"/api/v1/admin/settings/test-notification?store={self.store.code}", json={"message": "x"})
                self.assertFalse(response.json()["ok"])
                self.assertIn("group", response.json()["reason"].lower())
        finally:
            await client.aclose()

    async def test_currency_change_requires_hq_confirmation_and_audits_values(self):
        branch = await self.client(self.normal)
        try:
            current = (await branch.get("/api/v1/admin/settings/")).json()
            payload = {"currency": "EUR", "timezone": current["timezone"], "confirm_currency_change": True}
            self.assertEqual((await branch.patch("/api/v1/admin/settings/payment", json=payload)).status_code, 403)
        finally:
            await branch.aclose()
        hq = await self.client(self.hq)
        try:
            current = (await hq.get(f"/api/v1/admin/settings/?store={self.store.code}")).json()
            missing_confirmation = {"currency": "EUR", "timezone": current["timezone"]}
            self.assertEqual((await hq.patch(f"/api/v1/admin/settings/payment?store={self.store.code}", json=missing_confirmation)).status_code, 422)
            payload = {"currency": "EUR", "timezone": current["timezone"], "confirm_currency_change": True}
            response = await hq.patch(f"/api/v1/admin/settings/payment?store={self.store.code}", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            async with self.sessions() as db:
                audit = (await db.execute(__import__("sqlalchemy").select(self.models.AuditLog).where(self.models.AuditLog.actor_staff_id == self.hq.id))).scalars().all()
                self.assertTrue(any(item.details.get("currency_before") == "USD" and item.details.get("currency_after") == "EUR" for item in audit))
        finally:
            await hq.aclose()

    async def test_dashboard_uses_aggregate_queries(self):
        statements = []
        from sqlalchemy import event
        def capture(_conn, _cursor, statement, _params, _context, _executemany): statements.append(statement.upper())
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        client = await self.client(self.normal)
        try:
            response = await client.get("/api/v1/admin/dashboard?days=7")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertLessEqual(len([s for s in statements if "SELECT" in s]), 14)
            self.assertFalse(any("SELECT ORDERS.ID" in statement for statement in statements))
            self.assertIn("status_breakdown", response.json())
            self.assertIn("payment_breakdown", response.json())
        finally:
            await client.aclose()
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
