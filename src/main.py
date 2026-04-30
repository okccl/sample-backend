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
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
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

# コネクションプール（アプリ全体で共有）
pool: asyncpg.Pool | None = None

# 一時的なDB障害を対象にリトライ
RETRYABLE = (
    asyncpg.PostgresConnectionError,
    asyncpg.TooManyConnectionsError,
    OSError,
)

def db_retry(func):
    """最大5回・指数バックオフ（1→2→4→8→16秒）でリトライするデコレータ"""
    return retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )(func)

@db_retry
async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=DB_HOST,
        port=int(DB_PORT),
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=2,
        max_size=10,
        # サーバー側で切断されたコネクションを使用前に検知する
        max_inactive_connection_lifetime=30,
    )

class Item(BaseModel):
    name: str

@app.on_event("startup")
async def startup():
    global pool
    try:
        pool = await create_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        logger.info("DB接続成功・テーブル初期化完了")
    except Exception as e:
        logger.warning(f"DB接続失敗（DBが未起動の可能性）: {e}")

@app.on_event("shutdown")
async def shutdown():
    global pool
    if pool:
        await pool.close()
        logger.info("コネクションプールをクローズ")

@app.get("/health")
async def health():
    REQUEST_COUNT.labels(method="GET", endpoint="/health").inc()
    # DBへの疎通確認を含む（readinessProbe 対応）
    if pool is None:
        raise HTTPException(status_code=503, detail="DB接続プール未初期化")
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB接続エラー: {e}")
    return {"status": "ok"}

@app.get("/items")
async def list_items():
    REQUEST_COUNT.labels(method="GET", endpoint="/items").inc()
    with tracer.start_as_current_span("db.fetch_items"):
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT id, name, created_at::text FROM items ORDER BY id")
            return [dict(r) for r in rows]
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"DB接続エラー: {e}")

@app.post("/items", status_code=201)
async def create_item(item: Item):
    REQUEST_COUNT.labels(method="POST", endpoint="/items").inc()
    with tracer.start_as_current_span("db.insert_item"):
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at::text",
                    item.name
                )
            return dict(row)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"DB接続エラー: {e}")

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
