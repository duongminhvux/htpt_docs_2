from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.models.entities import User
from app.services.broker import broker
from app.services.connection_manager import manager
from app.services.permissions import get_document_role
from app.services.redis_service import redis_service

router = APIRouter(tags=["websocket"])


async def load_initial_document(db: Session, document_id: str):
    from app.models.entities import Document

    doc = db.get(Document, document_id)
    if not doc:
        return None
    cached = await redis_service.get_json(redis_service.document_key(document_id), default=None)
    if cached:
        return {
            "content_delta": cached.get("content_delta", doc.content_delta),
            "version": cached.get("version", doc.version),
            "vector_clock": cached.get("vector_clock", doc.vector_clock),
        }
    return {
        "content_delta": doc.content_delta,
        "version": doc.version,
        "vector_clock": doc.vector_clock,
    }


@router.websocket("/ws/documents/{document_id}")
async def document_socket(websocket: WebSocket, document_id: str):
    token = websocket.query_params.get("token")
    user_id = decode_access_token(token or "")
    if not user_id:
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            await websocket.close(code=4401)
            return
        from app.models.entities import Document

        doc = db.get(Document, document_id)
        if not doc:
            await websocket.close(code=4404)
            return
        role = get_document_role(db, doc, user)
        if not role:
            await websocket.close(code=4403)
            return

        await manager.connect(document_id, websocket, user.id, user.username, role)
        initial = await load_initial_document(db, document_id)
        users = await manager.users_for_document(document_id)
        await websocket.send_json({
            "type": "init",
            "document_id": document_id,
            "user": {"id": user.id, "email": user.email, "username": user.username},
            "role": role,
            "content_delta": initial["content_delta"],
            "version": initial["version"],
            "vector_clock": initial["vector_clock"],
            "users": users,
        })
        await manager.broadcast_presence(document_id)

        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "operation":
                if role == "viewer":
                    await websocket.send_json({"type": "error", "message": "Viewer cannot edit"})
                    continue
                payload = {
                    "type": "operation",
                    "document_id": document_id,
                    "user_id": user.id,
                    "username": user.username,
                    "operation_delta": message.get("operation_delta") or {"ops": []},
                    "base_version": int(message.get("base_version") or 0),
                    "vector_clock": message.get("vector_clock") or {},
                    "client_id": message.get("client_id") or user.id,
                }
                await broker.publish_operation(payload)
            elif msg_type == "cursor":
                await manager.update_cursor(document_id, user.id, message.get("cursor"))
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        db.close()
        await manager.disconnect(document_id, user_id)
