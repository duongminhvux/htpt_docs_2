import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.routers import auth, documents, websocket
from app.services.broker import broker
from app.services.connection_manager import manager
from app.services.redis_service import redis_service


async def wait_async_service(name: str, func, retries: int = 30, delay: float = 2.0):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            print(f"[startup] Connecting {name}... attempt {attempt}/{retries}", flush=True)
            await asyncio.wait_for(func(), timeout=10)
            print(f"[startup] {name} connected", flush=True)
            return
        except Exception as exc:
            last_error = exc
            print(f"[startup] {name} not ready: {repr(exc)}", flush=True)
            await asyncio.sleep(delay)

    raise RuntimeError(f"{name} is not ready: {last_error}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[startup] Initializing database...", flush=True)
    init_db()
    print("[startup] Database ready", flush=True)

    await wait_async_service("Redis", redis_service.connect)
    await wait_async_service("RabbitMQ", broker.connect)

    print("[startup] Starting RabbitMQ event consumer...", flush=True)
    await broker.consume_events(manager.broadcast_event)

    print("[startup] Application startup done", flush=True)

    yield

    await broker.close()
    await redis_service.close()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(websocket.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}