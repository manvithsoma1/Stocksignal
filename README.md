# StockSignal — Step 1 starter

## Prerequisites
- Docker Desktop installed and running
- Antigravity (or another editor)

## First run (Windows PowerShell)
1. Extract this ZIP.
2. Open the extracted `stocksignal_option_a` folder in Antigravity.
3. Copy `.env.example` to `.env` (same folder).
4. Open Antigravity Terminal at the project root.
5. Run: `docker compose up --build`
6. Wait for the backend log saying Uvicorn is running.

Open:
- http://localhost:8000/health
- http://localhost:8000/docs

Expected health response:
`{"status":"healthy","database":"connected"}`

## Useful commands
Stop containers, preserve database:
`docker compose down`

Stop containers and delete local database data:
`docker compose down -v`

Backend logs:
`docker compose logs -f backend`

Migration version:
`docker compose exec backend alembic current`

Run tests:
`docker compose exec backend pytest -q`

PostgreSQL shell:
`docker compose exec db psql -U stocksignal -d stocksignal`

## Important
This is a local development setup, not production security configuration.
The first migration intentionally creates only the seven agreed core tables.
A movement and all its postings must be inserted in one transaction. A deferred
database constraint verifies that their quantity totals match at commit.
The initial slice rejects zero-physical-change reclassification events; we will
resolve that ledger event design in the ledger-service phase before supporting it.
