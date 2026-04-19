from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import asyncpg
import os
import logging

logger = logging.getLogger("uvicorn")

# OTel初期化
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces")))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

app = FastAPI(title="sample-backend")
FastAPIInstrumentor.instrument_app(app)

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://sample-frontend.localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint"])

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "app")
DB_USER = os.getenv("DB_USER", "app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

class Item(BaseModel):
    name: str

async def get_conn():
    return await asyncpg.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

@app.on_event("startup")
async def startup():
    try:
        conn = await get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.close()
        logger.info("DB接続成功・テーブル初期化完了")
    except Exception as e:
        logger.warning(f"DB接続失敗（DBが未起動の可能性）: {e}")

@app.get("/health")
async def health():
    REQUEST_COUNT.labels(method="GET", endpoint="/health").inc()
    return {"status": "ok"}

@app.get("/items")
async def list_items():
    REQUEST_COUNT.labels(method="GET", endpoint="/items").inc()
    with tracer.start_as_current_span("db.fetch_items"):
        try:
            conn = await get_conn()
            rows = await conn.fetch("SELECT id, name, created_at::text FROM items ORDER BY id")
            await conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"DB接続エラー: {e}")

@app.post("/items", status_code=201)
async def create_item(item: Item):
    REQUEST_COUNT.labels(method="POST", endpoint="/items").inc()
    with tracer.start_as_current_span("db.insert_item"):
        try:
            conn = await get_conn()
            row = await conn.fetchrow(
                "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at::text",
                item.name
            )
            await conn.close()
            return dict(row)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"DB接続エラー: {e}")

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
