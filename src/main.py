from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import asyncpg
import os

app = FastAPI(title="sample-backend")

# メトリクス定義
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint"])

# DB接続設定（CNPGが生成するSecretから環境変数で受け取る）
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "app")
DB_USER = os.getenv("DB_USER", "app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

async def get_conn():
    return await asyncpg.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

class Item(BaseModel):
    name: str

@app.on_event("startup")
async def startup():
    # テーブルの初期化
    conn = await get_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    await conn.close()

@app.get("/health")
async def health():
    REQUEST_COUNT.labels(method="GET", endpoint="/health").inc()
    return {"status": "ok"}

@app.get("/items")
async def list_items():
    REQUEST_COUNT.labels(method="GET", endpoint="/items").inc()
    conn = await get_conn()
    rows = await conn.fetch("SELECT id, name, created_at::text FROM items ORDER BY id")
    await conn.close()
    return [dict(r) for r in rows]

@app.post("/items", status_code=201)
async def create_item(item: Item):
    REQUEST_COUNT.labels(method="POST", endpoint="/items").inc()
    conn = await get_conn()
    row = await conn.fetchrow(
        "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at::text",
        item.name
    )
    await conn.close()
    return dict(row)

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
