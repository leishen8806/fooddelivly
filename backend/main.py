from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy import text
from pathlib import Path
from daily_report import start_scheduler
from rate_limit import RateLimitMiddleware
from routers import auth, products, orders, admin_orders, admin_customers, admin_audit, bot, admin_stats, admin_settings, admin_staff, uploads, wallet, admin_reports
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 应用自己的日志（限流命中、报表投递、审核动作…）默认没有 handler 会被直接丢弃，
# uvicorn 只配置它自己的 logger。这里在没有 handler 时兜一个，级别可用 LOG_LEVEL 调整。
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

# Startup safety checks
if not os.getenv("JWT_SECRET"):
    raise RuntimeError("CRITICAL: JWT_SECRET environment variable is not set. Refusing to start.")

production = os.getenv("APP_ENV", "development").lower() == "production"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 进程内定时：每天早上 DAILY_REPORT_HOUR（默认 8 点，店铺时区）发前一天的报表。
    # 发送本身幂等（report_deliveries 唯一约束），所以重启/多副本只会送达一次。
    task = start_scheduler()
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(title="Tea Cafe API", lifespan=lifespan, docs_url=None if production else "/docs", redoc_url=None if production else "/redoc", openapi_url=None if production else "/openapi.json")

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

# 限流放在 CORS 之内、CSRF 之外：先按身份限流，再校验来源。
# 读取请求体和访问数据库都发生在更内层，所以被限的请求不会打到数据库。
app.add_middleware(RateLimitMiddleware)

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
app.include_router(admin_reports.router)

@app.get("/health")
async def health_check():
    from database import engine
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.2"}

