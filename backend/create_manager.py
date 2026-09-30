import argparse
import asyncio
import getpass
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from auth_utils import get_password_hash
from models import AuditLog, Staff

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


async def create_manager(login_name: str, password: str) -> None:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            managers = await db.execute(select(Staff.id).filter(Staff.role == "MANAGER", Staff.active.is_(True)).limit(1))
            if managers.first():
                raise RuntimeError("An active manager already exists; use the admin staff screen instead")
            staff = Staff(login_name=login_name, password_hash=get_password_hash(password), role="MANAGER", active=True)
            db.add(staff)
            await db.flush()
            db.add(AuditLog(actor_staff_id=staff.id, entity_type="staff", entity_id=str(staff.id),
                            action="initial_manager_created", details={"login_name": login_name}))
            await db.commit()
            print(f"Manager {login_name} created successfully.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the first Tea Cafe manager")
    parser.add_argument("login", type=str, help="Manager login name")
    args = parser.parse_args()
    password = getpass.getpass("Manager password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12 or len(password.encode("utf-8")) > 72 or password != confirmation:
        parser.error("Password must match confirmation and be between 12 and 72 UTF-8 bytes")
    asyncio.run(create_manager(args.login, password))
