# SlotFlow

Appointment booking and no-show prevention for a small clinic, built with **FastAPI + Python**, **React + TypeScript**, **PostgreSQL**, and **Redis**. PostgreSQL also holds a durable job queue and message outbox. The app includes patient booking, a clinic dashboard with a draggable calendar, staff check-in, waitlist auto-fill, reminders, and optional Twilio/WhatsApp and Google Calendar integration.

Read [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the transaction boundaries, race-condition handling, time-zone rules, worker recovery, and failure behavior.

## Run with Docker Compose

```bash
cp .env.example .env
# Set ADMIN_PASSWORD and SESSION_SECRET in .env
docker compose up --build
```

- Web app: `http://localhost:5173`
- Patient booking: `http://localhost:5173/book`
- Staff check-in: `http://localhost:5173/check-in`
- FastAPI docs: `http://localhost:8000/docs`

Compose starts PostgreSQL, Redis, the API, the durable worker, and the Vite frontend. The API applies the migration and seeds the sample clinic on startup. The seed is idempotent.

## Run each service locally

Start PostgreSQL and Redis, then:

```bash
cp .env.example .env
set -a; source .env; set +a
cd backend
uv sync --extra dev
uv run python -m scripts.migrate
uv run python -m scripts.seed
uv run uvicorn app.main:app --reload
```

In another terminal, load the same environment and run:

```bash
cd backend
uv run python -m app.worker
```

For the frontend:

```bash
cd frontend
npm ci
npm run dev
```

The Vite dev server proxies `/api` to FastAPI on port 8000. For production, route `/api/*` to FastAPI and serve `frontend/dist` as static files. Set `COOKIE_SECURE=true` behind HTTPS.

## Verify

```bash
cd backend && uv run pytest -q && uv run ruff check app scripts tests
cd ../frontend && npm run build
```

The integration test needs a PostgreSQL database, plus `SLOTFLOW_INTEGRATION=1`, `DATABASE_URL`, `ADMIN_PASSWORD`, and `SESSION_SECRET`. The regular test run covers slot generation, DST, breaks, and occupied ranges without services. The integration test covers idempotent booking, slot conflict, waitlist offer and claim, the database overlap constraint, and worker outbox dispatch.

Without Twilio credentials, the worker logs messages as a dry run. Google Calendar sync is skipped until its OAuth settings are configured. The dashboard's **28% → 11%** card is an illustrative example from the brief; it is not a measured result from this installation.

This is a working prototype. Add individual staff accounts, patient consent and retention controls, audit logs, and clinic-scoped authorization before using real patient information.
