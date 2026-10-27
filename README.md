# Bank Fraud System

A PostgreSQL database that detects and handles bank fraud by itself, with a small API and a dashboard on top.

```
Browser ──► Next.js on Vercel ──► FastAPI on Render ──► PostgreSQL on Neon
            (login, proxy)         (thin, no fraud logic)  (all the rules and money movement)
```

The database does the real work: a transfer is checked against the fraud rules, the money is moved, and the audit trail is written inside one stored procedure (`sp_execute_secure_transfer`). The API only calls it. The dashboard only shows it.

## Folder structure

```
database/simple.sql        Whole schema: 21 tables, 10 triggers, functions, procedures, 14 views, seed data
backend/                   FastAPI service (deploys to Render)
  app/main.py              All endpoints
  app/queries.py           Every SQL statement in one place
  app/transfers.py         Calls the transfer procedure, retries safely
  app/analysis.py          Money-flow graph analysis (networkx)
  app/jobs.py              Housekeeping and notification delivery loop
  scripts/apply_sql.py     Loads simple.sql into Neon
  scripts/smoke_test.py    Checks a running API
  tests/                   Unit tests
frontend/                  Next.js dashboard (deploys to Vercel)
  src/app/api/proxy        Adds the API key on the server; the browser never sees it
  src/middleware.js        Password gate for every page
render.yaml                Render blueprint
```

## Run it locally

You need Python 3.12+, Node 20+, and the Neon database.

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL, DIRECT_DATABASE_URL, API_KEY
python scripts/apply_sql.py # only if the schema is not in Neon yet (it wipes and recreates it)
uvicorn app.main:app --reload --port 8000
# Docs: http://localhost:8000/docs

# 2. Frontend (new terminal)
cd frontend
npm install
cp .env.example .env.local  # API_URL=http://localhost:8000, same API_KEY, a password, a secret
npm run dev                 # http://localhost:3000
```

Check the API: `python backend/scripts/smoke_test.py http://localhost:8000 YOUR_API_KEY`

## Deploy

### 1. Database (Neon)

`simple.sql` is already loaded. To reload it, run `python backend/scripts/apply_sql.py` (uses `DIRECT_DATABASE_URL`, not the pooled one).

### 2. API on Render

1. Push this folder to GitHub.
2. Render: New > Blueprint, pick the repo. It reads `render.yaml`.
3. Set the three secret values when asked:
   - `DATABASE_URL`: Neon **pooled** URL (host contains `-pooler`)
   - `DIRECT_DATABASE_URL`: Neon direct URL
   - `API_KEY`: a long random string
4. After deploy, open `https://YOUR-SERVICE.onrender.com/health`. It should say `{"status":"ok"}`.

### 3. Dashboard on Vercel

1. Vercel: Add New Project, pick the same repo, set **Root Directory** to `frontend`.
2. Environment variables:
   - `API_URL`: your Render URL, no trailing slash
   - `API_KEY`: the same value as on Render
   - `DASHBOARD_PASSWORD`: what you type on the login page
   - `SESSION_SECRET`: any long random string
3. Deploy, open the site, sign in.

Do not set `NEXT_PUBLIC_` anything. No secret should reach the browser.

## Things to know

- **Cold starts.** Render's free plan sleeps after 15 minutes idle; the first request can take about 30 seconds. Neon also wakes from sleep. The dashboard waits up to 55 seconds, then shows a message.
- **Background jobs** (housekeeping, notification delivery) run inside the API process every 60 seconds. They only run while the service is awake. Notification "sending" is simulated.
- **Demo data.** Every seeded customer's PIN is `4821`. The Send money page has ready-made scenarios (large transfer, mule account, blocklisted IP, new device, wrong PIN, brute-force).
- **Safe retries.** Every transfer carries an idempotency key. Sending the same key twice moves the money once.
- **Secrets.** `.env` and `.env.local` are git-ignored. Never commit them.
