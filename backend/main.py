from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, products, orders, admin_orders, bot, admin_stats, admin_settings
from dotenv import load_dotenv
import os

load_dotenv()

# Startup safety checks
if not os.getenv("JWT_SECRET"):
    raise RuntimeError("CRITICAL: JWT_SECRET environment variable is not set. Refusing to start.")

app = FastAPI(title="Tea Cafe API")

allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://food.workline.ink").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(admin_orders.router)
app.include_router(bot.router)
app.include_router(admin_stats.router)
app.include_router(admin_settings.router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1"}

