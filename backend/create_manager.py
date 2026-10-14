"""Create the first manager or an explicit headquarters manager."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_utils import get_password_hash
from models import AuditLog, Staff


LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
MIN_PASSWORD_BYTES = 12
MAX_PASSWORD_BYTES = 72


def validate_login_name(login_name: str) -> str:
    value = (login_name or "").strip()
    if not LOGIN_RE.fullmatch(value):
        raise ValueError("Login must use 3-64 letters, numbers, dot, underscore, or hyphen")
    return value


def validate_password(password: str, confirmation: str | None = None) -> str:
    encoded = (password or "").encode("utf-8")
    if len(encoded) < MIN_PASSWORD_BYTES or len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be 12 to 72 UTF-8 bytes")
    if confirmation is not None and password != confirmation:
        raise ValueError("Password and confirmation do not match")
    return password


async def create_manager(
    db: AsyncSession,
    login_name: str,
    password: str,
    *,
    hq: bool = False,
) -> Staff:
    """Create a manager in the supplied transaction."""
    login = validate_login_name(login_name)
    validate_password(password)
    duplicate = await db.scalar(select(Staff.id).where(Staff.login_name == login).limit(1))
    if duplicate is not None:
        raise RuntimeError("A staff account with this login already exists")
    if not hq:
        existing_manager = await db.scalar(
            select(Staff.id).where(Staff.role == "MANAGER", Staff.active.is_(True)).limit(1)
        )
        if existing_manager is not None:
            raise RuntimeError("An active manager already exists; use --hq to create a headquarters manager")

    staff = Staff(
        login_name=login,
        password_hash=get_password_hash(password),
        role="MANAGER",
        store_id=None,
        active=True,
    )
    db.add(staff)
    await db.flush()
    db.add(AuditLog(
        actor_staff_id=staff.id,
        store_id=None,
        entity_type="staff",
        entity_id=str(staff.id),
        action="hq_manager_created" if hq else "initial_manager_created",
        details={"login_name": login},
    ))
    await db.commit()
    return staff


async def create_manager_with_engine(engine, login_name: str, password: str, *, hq: bool = False) -> Staff:
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        return await create_manager(db, login_name, password, hq=hq)


def _database_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    if value.startswith("postgresql://"):
        value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql+asyncpg://", 1)
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Tea Cafe manager")
    parser.add_argument("login", type=str, help="Manager login name")
    parser.add_argument("--hq", action="store_true", help="Create a cross-store headquarters manager")
    parser.add_argument("--yes", action="store_true", help="Accept the HQ warning for automation")
    args = parser.parse_args()
    try:
        args.login = validate_login_name(args.login)
    except ValueError as exc:
        parser.error(str(exc))
    if args.hq and not args.yes:
        print("This creates a headquarters manager with access to every store.")
        if input("Type yes to continue: ").strip().lower() != "yes":
            parser.error("HQ manager creation was not confirmed")
    return args


async def _run(args: argparse.Namespace) -> None:
    password = getpass.getpass("Manager password (12-72 UTF-8 bytes): ")
    confirmation = getpass.getpass("Confirm password: ")
    try:
        validate_password(password, confirmation)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    engine = create_async_engine(_database_url())
    try:
        try:
            staff = await create_manager_with_engine(engine, args.login, password, hq=args.hq)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    finally:
        await engine.dispose()
    scope = "headquarters" if args.hq else "initial manager"
    print(f"{scope.title()} {staff.login_name} created successfully.")


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
