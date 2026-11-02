from fastapi import FastAPI
from sqlalchemy import text
from app.db.session import engine
from app.api.inventory import router as inventory_router

app = FastAPI(title="StockSignal API", version="0.1.0")

app.include_router(inventory_router)

@app.get("/")
def root():
    return {"service": "StockSignal API", "status": "running"}

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "healthy", "database": "connected"}
