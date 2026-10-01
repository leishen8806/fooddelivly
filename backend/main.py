from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy import text
from pathlib import Path
from routers import auth, products, orders, admin_orders, admin_customers, admin_audit, bot, admin_stats, admin_settings, admin_staff, uploads, wallet
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Startup safety checks
if not os.getenv("JWT_SECRET"):
    raise RuntimeError("CRITICAL: JWT_SECRET environment variable is not set. Refusing to start.")

production = os.getenv("APP_ENV", "development").lower() == "production"
app = FastAPI(title="Tea Cafe API", docs_url=None if production else "/docs", redoc_url=None if production else "/redoc", openapi_url=None if production else "/openapi.json")

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://food.workline.ink").split(",")
allowed_origins = [origin.strip().rstrip("/") for origin in allowed_origins if origin.strip()]

class CookieOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.url.path.endswith("/telegram/webhook"):
            has_session = "session_token" in request.cookies or "admin_session_token" in request.cookies
            if has_session:
                origin = (request.headers.get("origin") or "").rstrip("/")
                if not origin or origin not in allowed_origins:
                    return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
        return await call_next(request)

app.add_middleware(CookieOriginMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(admin_orders.router)
app.include_router(admin_customers.router)
app.include_router(admin_audit.router)
app.include_router(bot.router)
app.include_router(admin_stats.router)
app.include_router(admin_settings.router)
app.include_router(admin_staff.router)
app.include_router(uploads.router)
app.include_router(wallet.router)
app.include_router(wallet.admin_router)

@app.get("/health")
async def health_check():
    from database import engine
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.2"}

