import asyncio
import os
import argparse
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from dotenv import load_dotenv
from models import Staff
from auth_utils import get_password_hash

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/teacafe")

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def create_manager(login_name, password):
    async with AsyncSessionLocal() as db:
        hashed_pw = get_password_hash(password)
        manager = Staff(
            login_name=login_name,
            password_hash=hashed_pw,
            role="MANAGER",
            active=True
        )
        db.add(manager)
        await db.commit()
        print(f"Manager {login_name} created successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap initial manager account")
    parser.add_argument("login", type=str, help="Manager login name")
    parser.add_argument("password", type=str, help="Manager password")
    args = parser.parse_args()
    
    asyncio.run(create_manager(args.login, args.password))
