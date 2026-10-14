"""Real PostgreSQL coverage for the headquarters manager bootstrap flow."""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault("COOKIE_SECURE", "false")


class CreateManagerHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.getenv("RUN_DB_TESTS") != "1":
            self.skipTest("set RUN_DB_TESTS=1 for PostgreSQL integration tests")
        import httpx
        from jose import jwt
        from sqlalchemy import delete, select, text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        import models
        import create_manager
        from database import get_db
        from main import app

        self.httpx = httpx
        self.jwt = jwt
        self.select = select
        self.delete = delete
        self.models = models
        self.create_manager = create_manager
        self.engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.tag = uuid.uuid4().hex[:8]
        async with self.sessions() as db:
            self.main_store = models.Store(code=f"CM{self.tag}", name={"en": "HQ test"}, status="ACTIVE",
                                           timezone="Asia/Phnom_Penh", currency="USD")
            self.branch_store = models.Store(code=f"CB{self.tag}", name={"en": "Branch test"}, status="ACTIVE",
                                             timezone="Asia/Phnom_Penh", currency="USD")
            db.add_all([self.main_store, self.branch_store])
            await db.flush()
            self.branch_manager = models.Staff(
                login_name=f"branch-{self.tag}", password_hash="unused", role="MANAGER",
                store_id=self.branch_store.id, active=True,
            )
            self.branch_staff = models.Staff(
                login_name=f"staff-{self.tag}", password_hash="unused", role="STAFF",
                store_id=self.branch_store.id, active=True,
            )
            self.category = models.Category(name={"en": f"HQ {self.tag}"}, sort_order=999, active=True)
            db.add_all([self.branch_manager, self.branch_staff, self.category])
            await db.commit()
            # The shared development database contains hand-seeded rows; make
            # PostgreSQL sequences safe for the HTTP catalog insert below.
            await db.execute(text("SELECT setval('categories_id_seq', GREATEST(COALESCE((SELECT MAX(id) FROM categories), 1), 1), true)"))
            await db.execute(text("SELECT setval('products_id_seq', GREATEST(COALESCE((SELECT MAX(id) FROM products), 1), 1), true)"))
            await db.commit()

        async def _db():
            async with self.sessions() as session:
                yield session

        self.app = app
        self.get_db = get_db
        self.app.dependency_overrides[get_db] = _db

    async def asyncTearDown(self):
        if not hasattr(self, "engine"):
            return
        async with self.sessions() as db:
            hq_ids = [getattr(self, "hq_manager", None).id] if getattr(self, "hq_manager", None) else []
            product_ids = []
            if getattr(self, "product", None):
                product_ids.append(self.product.id)
            if product_ids:
                await db.execute(self.models.product_categories.delete().where(
                    self.models.product_categories.c.product_id.in_(product_ids)
                ))
                await db.execute(self.delete(self.models.Product).where(self.models.Product.id.in_(product_ids)))
            actor_ids = [self.branch_manager.id, self.branch_staff.id, *hq_ids]
            await db.execute(self.delete(self.models.AuditLog).where(self.models.AuditLog.actor_staff_id.in_(actor_ids)))
            await db.execute(self.delete(self.models.Staff).where(self.models.Staff.id.in_(actor_ids)))
            await db.execute(self.delete(self.models.Category).where(self.models.Category.id == self.category.id))
            await db.execute(self.delete(self.models.Store).where(
                self.models.Store.id.in_([self.main_store.id, self.branch_store.id])
            ))
            await db.commit()
        self.app.dependency_overrides.pop(self.get_db, None)
        await self.engine.dispose()

    def client(self, staff_id: int):
        token = self.jwt.encode(
            {"sub": str(staff_id), "type": "staff", "role": "MANAGER"},
            os.environ["JWT_SECRET"], algorithm="HS256",
        )
        return self.httpx.AsyncClient(
            transport=self.httpx.ASGITransport(app=self.app), base_url="http://test",
            headers={"Origin": "http://localhost:5173"},
            cookies={"admin_session_token": token},
        )

    async def test_hq_manager_can_cross_store_catalog_and_me(self):
        async with self.sessions() as db:
            self.hq_manager = await self.create_manager.create_manager(
                db, f"hq-{self.tag}", "A-strong-password-123", hq=True
            )

        async with self.client(self.hq_manager.id) as hq_client:
            me = await hq_client.get("/api/v1/auth/admin/me")
            self.assertEqual(me.status_code, 200, me.text)
            self.assertIsNone(me.json()["store_id"])
            self.assertTrue(me.json()["catalog_write_allowed"])
            created = await hq_client.post("/api/v1/admin/products", json={
                "category_id": self.category.id,
                "name": {"en": "HQ test product", "zh-CN": "总部测试商品", "km": "HQ"},
                "price_minor": 125,
                "currency": "USD",
                "available": True,
            })
            self.assertEqual(created.status_code, 200, created.text)
            self.product = type("ProductRef", (), {"id": created.json()["id"]})()

        async with self.client(self.branch_manager.id) as branch_client:
            forbidden = await branch_client.post("/api/v1/admin/products", json={
                "category_id": self.category.id,
                "name": {"en": "Branch must not write"},
                "price_minor": 125,
            })
            self.assertEqual(forbidden.status_code, 403, forbidden.text)

        async with self.sessions() as db:
            audit = (await db.execute(self.select(self.models.AuditLog).where(
                self.models.AuditLog.actor_staff_id == self.hq_manager.id,
                self.models.AuditLog.action == "hq_manager_created",
            ))).scalars().one()
            self.assertIsNone(audit.store_id)
            self.assertEqual(audit.details, {"login_name": f"hq-{self.tag}"})
            self.assertNotIn("password", str(audit.details).lower())

    async def test_first_manager_guard_duplicate_and_password_validation(self):
        async with self.sessions() as db:
            with self.assertRaises(RuntimeError):
                await self.create_manager.create_manager(
                    db, f"initial-{self.tag}", "A-strong-password-123", hq=False
                )
            with self.assertRaises(ValueError):
                await self.create_manager.create_manager(db, "bad login", "A-strong-password-123", hq=True)
            with self.assertRaises(ValueError):
                await self.create_manager.create_manager(db, f"weak-{self.tag}", "short", hq=True)
            with self.assertRaises(RuntimeError):
                await self.create_manager.create_manager(
                    db, self.branch_manager.login_name, "A-strong-password-123", hq=True
                )


if __name__ == "__main__":
    unittest.main()
