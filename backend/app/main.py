from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.db.session import engine
from app.api.inventory import router as inventory_router
from app.api.decision_support import router as decision_support_router

app = FastAPI(title="StockSignal API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inventory_router)
app.include_router(decision_support_router)

@app.get("/")
def root():
    return {"service": "StockSignal API", "status": "running"}

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "healthy", "database": "connected"}
