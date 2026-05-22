from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, init_db
from app.models.entities import Document, DocumentOperation
from app.services.broker import broker
from app.services.ot import apply_delta, delta_to_plain_text, transform_delta
from app.services.redis_service import redis_service
from app.services.vector_clock import VectorClock

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SERVER_NODE = "server"


async def wait_async_service(
    name: str,
    func: Callable[[], Awaitable[Any]],
    retries: int = 30,
    delay: float = 2.0,
):
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            logger.info("Connecting %s... attempt %s/%s", name, attempt, retries)
            await asyncio.wait_for(func(), timeout=15)
            logger.info("%s connected", name)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("%s not ready: %r", name, exc)
            await asyncio.sleep(delay)

    raise RuntimeError(f"{name} is not ready: {last_error}")


async def get_snapshot(document: Document) -> dict[str, Any]:
    cached = await redis_service.get_json(redis_service.document_key(document.id), default=None)
    if cached:
        return cached

    return {
        "content_delta": document.content_delta,
        "version": document.version,
        "vector_clock": document.vector_clock,
    }


async def save_snapshot(document: Document):
    await redis_service.set_json(
        redis_service.document_key(document.id),
        {
            "content_delta": document.content_delta,
            "version": document.version,
            "vector_clock": document.vector_clock,
        },
    )


def transform_against_history(
    db: Session,
    document_id: str,
    base_version: int,
    op_delta: dict[str, Any],
) -> dict[str, Any]:
    transformed = op_delta

    if base_version < 0:
        base_version = 0

    history = (
        db.query(DocumentOperation)
        .filter(
            DocumentOperation.document_id == document_id,
            DocumentOperation.server_version > base_version,
        )
        .order_by(DocumentOperation.server_version.asc())
        .all()
    )

    for previous in history:
        transformed = transform_delta(transformed, previous.transformed_delta)

    return transformed


async def process_operation(payload: dict[str, Any]):
    db = SessionLocal()

    try:
        document_id = payload["document_id"]
        user_id = payload.get("user_id")
        base_version = int(payload.get("base_version") or 0)
        client_clock = payload.get("vector_clock") or {}
        incoming_delta = payload.get("operation_delta") or {"ops": []}

        document = db.get(Document, document_id)
        if not document:
            logger.warning("Document %s not found", document_id)
            return

        snapshot = await get_snapshot(document)

        current_version = int(snapshot.get("version") or document.version or 0)
        document.content_delta = snapshot.get("content_delta") or document.content_delta
        document.vector_clock = snapshot.get("vector_clock") or document.vector_clock or {}
        document.version = current_version

        relation = VectorClock.compare(client_clock, document.vector_clock).value

        transformed_delta = transform_against_history(
            db=db,
            document_id=document_id,
            base_version=base_version,
            op_delta=incoming_delta,
        )

        new_delta = apply_delta(document.content_delta, transformed_delta)

        new_clock = VectorClock.merge(document.vector_clock, client_clock)
        new_clock = VectorClock.tick(new_clock, SERVER_NODE)

        new_version = current_version + 1

        document.content_delta = new_delta
        document.content_text = delta_to_plain_text(new_delta)
        document.version = new_version
        document.vector_clock = new_clock

        op_log = DocumentOperation(
            document_id=document_id,
            user_id=user_id,
            operation_delta=incoming_delta,
            transformed_delta=transformed_delta,
            vector_clock=new_clock,
            base_version=base_version,
            server_version=new_version,
            causal_relation=relation,
        )

        db.add(op_log)
        db.commit()
        db.refresh(op_log)

        await save_snapshot(document)

        event = {
            "type": "operation_applied",
            "document_id": document_id,
            "user_id": user_id,
            "username": payload.get("username"),
            "operation_id": op_log.id,
            "operation_delta": transformed_delta,
            "base_version": base_version,
            "server_version": new_version,
            "vector_clock": new_clock,
            "causal_relation": relation,
        }

        await broker.publish_event(event)

        logger.info(
            "Applied op doc=%s user=%s base=%s server=%s relation=%s",
            document_id,
            user_id,
            base_version,
            new_version,
            relation,
        )

    except Exception:
        logger.exception("Failed to process operation")
        raise

    finally:
        db.close()


async def main():
    logger.info("Starting OT worker...")

    init_db()

    await wait_async_service("Redis", redis_service.connect)
    await wait_async_service("RabbitMQ", broker.connect)

    await broker.consume_operations(process_operation)

    logger.info("OT worker is running")

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())