FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies for psycopg (libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run alembic migrations (and optional idempotent demo seed) then start uvicorn
CMD alembic upgrade head && \
    if [ "$SEED_ON_STARTUP" = "true" ]; then python -m app.db.seed; fi && \
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
