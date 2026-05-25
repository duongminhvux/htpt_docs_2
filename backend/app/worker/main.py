from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal, init_db
from app.models.entities import Document, DocumentOperation, DocumentVersion
from app.services.broker import broker
from app.services.ot import apply_delta, delta_to_plain_text, transform_delta
from app.services.redis_service import redis_service
from app.services.vector_clock import VectorClock

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SERVER_NODE = "server"


def summarize_delta(delta: dict[str, Any] | None) -> str:
    """Tạo log ngắn: insert/delete/retain ở index nào."""
    if not delta:
        return "empty"

    index = 0
    parts: list[str] = []

    for op in delta.get("ops", []):
        if "retain" in op:
            index += int(op["retain"])
            continue

        if "insert" in op:
            value = op["insert"]
            if isinstance(value, str):
                safe_value = value.replace("\n", "\\n")
            else:
                safe_value = str(value)
            parts.append(f"insert='{safe_value}' at index={index}")
            index += len(value) if isinstance(value, str) else 1
            continue

        if "delete" in op:
            count = int(op["delete"])
            parts.append(f"delete={count} at index={index}")
            continue

    return "; ".join(parts) if parts else "format/retain-only"


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


def ensure_version_snapshot(
    db: Session,
    document: Document,
    *,
    user_id: str | None = None,
    operation_id: str | None = None,
    action: str = "edit",
    source_version: int | None = None,
    target_version: int | None = None,
):
    exists = (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == document.id, DocumentVersion.version == document.version)
        .first()
    )
    if exists:
        return exists
    row = DocumentVersion(
        document_id=document.id,
        version=document.version,
        content_delta=document.content_delta,
        content_text=document.content_text or "",
        vector_clock=document.vector_clock or {},
        created_by=user_id,
        operation_id=operation_id,
        action=action,
        source_version=source_version,
        target_version=target_version,
    )
    db.add(row)
    return row


def transform_against_history(
    db: Session,
    document_id: str,
    base_version: int,
    op_delta: dict[str, Any],
    user_id: str | None = None,
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
        # Cùng user thường là chuỗi thao tác tuần tự từ cùng client.
        if user_id and previous.user_id == user_id:
            continue

        before = transformed
        transformed = transform_delta(transformed, previous.transformed_delta)

        logger.info(
            "[OT] transform doc=%s against_server_version=%s previous_user=%s before=(%s) against=(%s) after=(%s)",
            document_id,
            previous.server_version,
            previous.user_id,
            summarize_delta(before),
            summarize_delta(previous.transformed_delta),
            summarize_delta(transformed),
        )

    return transformed


async def process_operation(payload: dict[str, Any]):
    db = SessionLocal()

    try:
        document_id = payload["document_id"]
        user_id = payload.get("user_id")
        username = payload.get("username")
        client_id = payload.get("client_id") or user_id
        client_op_id = payload.get("client_op_id")
        base_version = int(payload.get("base_version") or 0)
        client_clock = payload.get("vector_clock") or {}
        incoming_delta = payload.get("operation_delta") or {"ops": []}

        logger.info(
            "[WORKER_RECV] client=%s user=%s username=%s doc=%s client_op=%s base=%s delta=(%s) clock=%s",
            client_id,
            user_id,
            username,
            document_id,
            client_op_id,
            base_version,
            summarize_delta(incoming_delta),
            client_clock,
        )

        document = db.get(Document, document_id)
        if not document:
            logger.warning("[WORKER_DROP] Document %s not found", document_id)
            return

        if client_op_id:
            existing_op = (
                db.query(DocumentOperation)
                .filter(
                    DocumentOperation.document_id == document_id,
                    DocumentOperation.client_op_id == client_op_id,
                )
                .first()
            )
            if existing_op:
                snapshot = await get_snapshot(document)
                logger.info(
                    "[WORKER_DUPLICATE_OP] doc=%s client_op=%s already_server=%s - republish ack only",
                    document_id,
                    client_op_id,
                    existing_op.server_version,
                )
                await broker.publish_event({
                    "type": "operation_applied",
                    "document_id": document_id,
                    "user_id": user_id,
                    "username": username,
                    "client_id": client_id,
                    "client_op_id": client_op_id,
                    "operation_id": existing_op.id,
                    "operation_delta": existing_op.transformed_delta,
                    "base_version": existing_op.base_version,
                    "server_version": snapshot.get("version", existing_op.server_version),
                    "vector_clock": snapshot.get("vector_clock") or {},
                    "causal_relation": "duplicate_ack",
                    "content_delta": snapshot.get("content_delta") or document.content_delta,
                    "content_text": delta_to_plain_text(snapshot.get("content_delta") or document.content_delta),
                    "action": "edit",
                })
                return

        snapshot = await get_snapshot(document)

        current_version = int(snapshot.get("version") or document.version or 0)
        document.content_delta = snapshot.get("content_delta") or document.content_delta
        document.vector_clock = snapshot.get("vector_clock") or document.vector_clock or {}
        document.version = current_version

        logger.info(
            "[WORKER_STATE] doc=%s server_current_version=%s client_base_version=%s server_clock=%s",
            document_id,
            current_version,
            base_version,
            document.vector_clock,
        )

        relation = VectorClock.compare(client_clock, document.vector_clock).value

        transformed_delta = transform_against_history(
            db=db,
            document_id=document_id,
            base_version=base_version,
            op_delta=incoming_delta,
            user_id=user_id,
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
            client_op_id=client_op_id,
            operation_delta=incoming_delta,
            transformed_delta=transformed_delta,
            vector_clock=new_clock,
            base_version=base_version,
            server_version=new_version,
            causal_relation=relation,
        )

        db.add(op_log)
        db.flush()
        ensure_version_snapshot(
            db,
            document,
            user_id=user_id,
            operation_id=op_log.id,
            action="edit",
            source_version=base_version,
            target_version=new_version,
        )
        db.commit()
        db.refresh(op_log)

        await save_snapshot(document)

        event = {
            "type": "operation_applied",
            "document_id": document_id,
            "user_id": user_id,
            "username": username,
            "client_id": client_id,
            "client_op_id": client_op_id,
            "operation_id": op_log.id,
            "operation_delta": transformed_delta,
            "base_version": base_version,
            "server_version": new_version,
            "vector_clock": new_clock,
            "causal_relation": relation,

            # Quan trọng:
            # gửi snapshot đầy đủ từ server để mọi client set về cùng trạng thái.
            # Tránh trường hợp client tự optimistic update khác thứ tự server.
            "content_delta": new_delta,
            "content_text": document.content_text,
            "action": "edit",
        }

        await broker.publish_event(event)

        logger.info(
            "[WORKER_APPLIED] doc=%s client=%s user=%s client_op=%s base=%s -> server=%s relation=%s incoming=(%s) transformed=(%s) text='%s'",
            document_id,
            client_id,
            user_id,
            client_op_id,
            base_version,
            new_version,
            relation,
            summarize_delta(incoming_delta),
            summarize_delta(transformed_delta),
            document.content_text,
        )

        logger.info(
            "[WORKER_PUBLISH] doc=%s server_version=%s broadcast_snapshot_len=%s",
            document_id,
            new_version,
            len(document.content_text or ""),
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
