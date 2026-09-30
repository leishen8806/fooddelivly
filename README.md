# Tea Cafe

Telegram Mini App for ordering, with a Telegram Bot and Admin Web Console.

## Development Slices

### Slice A: Project Skeleton - **COMPLETED**
- **Modules modified**: Project root, `backend/`, `frontend/`.
- **Validation**:
  - `docker-compose.yml` created with PostgreSQL.
  - FastAPI app skeleton with Async SQLAlchemy & Alembic (`backend/`).
  - React + Vite + TS skeleton (`frontend/`).
  - `.env.example` provided.
- **Remaining issues**: None. Ready for next slice.

### Slice B: Auth & Locales - **COMPLETED (Pending DB verification)**
- **Modules modified**: 
  - Backend: `models.py` (Customer, Staff), `auth_utils.py` (HMAC validation, JWT with strict `exp` set to 7 days), `routers/auth.py` (endpoints), `create_manager.py` (CLI tool), `dependencies.py` (unified Cookie-based Auth with live DB checks for active staff).
  - Frontend: `src/i18n.ts` (multi-language using `locales.json`), `src/store/authStore.ts` (Zustand state), `src/components/TelegramProvider.tsx` (WebApp SDK & Auto-login).
  - Git: `/.gitignore`, `/backend/.gitignore`, `/frontend/.gitignore` verified to exclude `.env` files and real secrets.
- **Validation**: Static code checks passed. `.gitignore` policies active. `JWT_SECRET` strict validation and `auth_date` expiry strictly checked. Cookie-based auth unified across API. JWT has expiry. `get_current_staff` does live DB query to ensure the account is active.
- **Remaining issues**: DB testing pending.

### Slice C: Products & Ordering - **COMPLETED (Pending DB verification)**
- **Modules modified**:
  - Backend: `models.py` (Category, Product, Order, OrderItem, `idempotency_key` and `request_digest`, added Category->Product relationships), `routers/products.py`, `routers/orders.py`, `main.py` (CORS).
  - Migrations: Created manual baseline Alembic script `backend/alembic/versions/1a2b3c4d5e6f_initial_schema.py` containing all 9 core tables (Customer, Staff, Category, Product, Order, OrderItem, PaymentProof, PaymentReview, OrderEvent, StoreSettings) with the UniqueConstraint `uq_order_idempotency` for `(customer_id, idempotency_key)`.
- **Validation**: 
  - Idempotency check scoped to `customer_id` and checks payload digest collision to handle concurrent request bugs safely using DB `IntegrityError` (`uq_order_idempotency`).
  - Server-side price calculation enforced. 
  - Ordered items strictly validate quantity (`> 0`) and block mixed currencies. 
  - Options with price implications are temporarily blocked in `POST /orders`.
  - `POST /admin/products` protected by `get_current_manager`.
  - `GET /menu` returns categories populated with their active products.
  - `LocalizedText` properly aliases `zh-CN`.
  - CORS strictly configured to environment `ALLOWED_ORIGINS` (no wildcards).
  - Alembic script static review passed.
- **Remaining issues**: DB testing pending.

### Slice D: Payment Proof & Manual Verification - **IMPLEMENTED (Pending DB and Telegram verification)**
- **Modules modified**:
  - Backend: `models.py` (PaymentProof, PaymentReview), `routers/admin_orders.py`, `routers/bot.py`.
- **Validation**: Webhook handler logic and manual review UI are implemented. Static checks pass.
- **Remaining issues**: Real Telegram webhook setup, payment-proof access, and database integration remain unverified.

### Slice E: Telegram Group & Fulfillment - **IMPLEMENTED (Pending DB and Telegram verification)**
- **Modules modified**:
  - Backend: `models.py` (OrderEvent), `routers/bot.py` (callback query state machine).
- **Validation**: State transitions, configured group checks, callback updates and customer notifications are implemented. Static checks pass.
- **Remaining issues**: Requires integration testing with the real Bot and PostgreSQL.

### Slice F: Admin, Stats & Deploy - **IMPLEMENTED (Deployment pending)**
- **Modules modified**:
  - Backend: `models.py` (StoreSettings), `routers/admin_stats.py`, `routers/admin_settings.py`.
  - Frontend: `App.tsx` Admin Dashboard fully populated with approval/rejection actions and stats.
  - Docker Compose: Initialized at project start.
- **Validation**: Aggregation, settings authorization, customer menu and admin order review are implemented. Frontend build passes.
- **Remaining issues**: PostgreSQL migration, production secrets, HTTPS, VPS deployment and live acceptance are pending.

## Running the Project Locally (When Docker is available)

1. **Environment Setup**:
   ```bash
   cp .env.example .env
   # Update .env with real BOT_TOKEN, JWT_SECRET, DB_USER, etc.
   ```
2. **Start Services**:
   ```bash
   docker-compose up -d --build
   ```
3. **Database Migrations** (Inside the backend container):
   ```bash
   docker-compose exec backend alembic revision --autogenerate -m "init"
   docker-compose exec backend alembic upgrade head
   ```
4. **Create First Manager**:
   ```bash
   docker-compose exec backend python create_manager.py admin mypassword123
   ```
5. **Access**:
   - Backend API: `http://localhost:8000/docs`
   - Frontend: `http://localhost:3000`
