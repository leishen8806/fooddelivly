# Tea Cafe

Telegram Mini App ordering with a Telegram bot and a browser based staff console.

## Current delivery status

- React, TypeScript, and Vite frontend; FastAPI, SQLAlchemy async, and Aiogram backend.
- PostgreSQL 15 schema managed by Alembic; Docker Compose production layout.
- Customer Mini App authentication uses validated Telegram `initData`; the browser admin uses a separate staff login and HttpOnly cookie.
- Product, order, manual payment proof review, staff/group actions, store settings, and date-range financial summary are implemented.
- Customer ordering supports per-item sweetness, a shared cart, one room number, and one checkout/payment handoff for the full cart.
- Manager-only customer management shows Telegram name/ID, a Telegram chat link, total orders, and completed orders.
- Payment-proof images are served with a raster content type detected from the file bytes, including when Telegram labels the download as generic binary data.
- Chinese, English, and Khmer locale files contain the same 202 keys. Product, category, customer-management, and sweetness labels support all three languages.
- Current local checks: frontend production build, backend Python syntax compilation and FastAPI route import, order sweetness schema validation, and locale-key parity. Docker/PostgreSQL integration remains unverified in this host environment.
- Deployed on VPS `159.223.92.104`; PostgreSQL 15.19 migration and `/health`, `/api/v1/menu`, and `/admin` HTTPS routes have been verified.
- Telegram `initData` login, Bot webhook, manager bootstrap, ABA payment details, staff group, and real order/payment flow remain pending Tea Cafe configuration and live acceptance.

## Production environment

Copy `.env.example` to `.env` and replace every placeholder. Keep `.env` out of Git.

Required values:

- `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `JWT_SECRET` and `WEBHOOK_SECRET` (generate separate random secrets)
- `BOT_TOKEN` and `BOT_USERNAME` from the Tea Cafe bot in BotFather
- `MINI_APP_URL=https://food.workline.ink/`
- `COOKIE_SECURE=true` and `APP_ENV=production`

Start the stack from the repository root:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend frontend
```

Compose exposes the frontend only on `127.0.0.1:8080`; put the existing HTTPS reverse proxy in front of that port. The admin console is at `https://food.workline.ink/admin`; customers must open the Mini App through Telegram.

Create the first manager once, after the database migration has completed:

```sh
docker compose exec backend python create_manager.py manager
```

The command prompts for and confirms a password. Do not put the password in shell history or process arguments. After a manager exists, create other staff accounts in the admin console.

## Telegram webhook setup

After the public HTTPS route is live, register the webhook from the backend container so the token and secret do not appear in shell history:

```sh
docker compose exec backend python -c 'import os,urllib.parse,urllib.request; data=urllib.parse.urlencode({"url":"https://food.workline.ink/api/v1/telegram/webhook","secret_token":os.environ["WEBHOOK_SECRET"]}).encode(); req=urllib.request.Request("https://api.telegram.org/bot"+os.environ["BOT_TOKEN"]+"/setWebhook",data=data); print(urllib.request.urlopen(req,timeout=15).read().decode())'
```

Do not paste a real token or secret into Git, screenshots, or chat. Verify the webhook with BotFather/Telegram `getWebhookInfo`, then open the Mini App from the bot and send a test payment-proof photo before accepting real orders.

## Local development

Use Python 3.11, Node.js 22, and Docker Compose. Copy `.env.example` to `.env`, set valid non-production secrets and bot details, and set `COOKIE_SECURE=false` for local HTTP. Run `docker compose up --build`; the combined frontend/API origin is `http://localhost:8080`. The Vite dev server is optional and runs with `npm run dev` from `frontend/`.

## Important operational boundaries

- Payment screenshots are evidence only. A staff member must verify actual ABA receipt before marking an order paid.
- The order and payment states are independent. Do not advance an unpaid order to preparation.
- Do not reuse another service's database, bot token, webhook, or Telegram group configuration.
- Back up PostgreSQL and restrict VPS firewall access; the database has no public port mapping.
- Check [the product requirements](docs/PRODUCT-REQUIREMENTS.md), [technical design](docs/TECHNICAL-DESIGN.md), and [UI handoff](docs/ui/UI-DESIGN-HANDOFF.md) for the implementation contract and remaining acceptance scenarios.
