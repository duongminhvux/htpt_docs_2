from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket


logger = logging.getLogger(__name__)

PALETTE = [
    "#1a73e8", "#e8710a", "#188038", "#d93025", "#9334e6", "#00897b", "#f9ab00", "#c2185b",
]


def color_for_user(user_id: str) -> str:
    digest = hashlib.md5(user_id.encode()).hexdigest()
    return PALETTE[int(digest[:2], 16) % len(PALETTE)]


@dataclass
class ClientConnection:
    websocket: WebSocket
    user_id: str
    username: str
    role: str
    color: str
    cursor: dict[str, Any] | None = None


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, dict[str, ClientConnection]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, document_id: str, websocket: WebSocket, user_id: str, username: str, role: str):
        await websocket.accept()
        async with self.lock:
            self.active.setdefault(document_id, {})[user_id] = ClientConnection(
                websocket=websocket,
                user_id=user_id,
                username=username,
                role=role,
                color=color_for_user(user_id),
            )
            total = len(self.active.get(document_id, {}))
        logger.info("[MANAGER_CONNECT] doc=%s user=%s username=%s active=%s", document_id, user_id, username, total)

    async def disconnect(self, document_id: str, user_id: str):
        async with self.lock:
            if document_id in self.active:
                self.active[document_id].pop(user_id, None)
                if not self.active[document_id]:
                    self.active.pop(document_id, None)
            total = len(self.active.get(document_id, {}))
        logger.info("[MANAGER_DISCONNECT] doc=%s user=%s active=%s", document_id, user_id, total)
        await self.broadcast_presence(document_id)

    async def send_personal(self, document_id: str, user_id: str, payload: dict[str, Any]):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
        if conn:
            await conn.websocket.send_json(payload)

    async def broadcast(self, document_id: str, payload: dict[str, Any], exclude_user_id: str | None = None):
        async with self.lock:
            conns = list(self.active.get(document_id, {}).values())

        sent = 0
        failed = 0
        for conn in conns:
            if exclude_user_id and conn.user_id == exclude_user_id:
                continue
            try:
                await conn.websocket.send_json(payload)
                sent += 1
            except Exception:
                failed += 1

        if payload.get("type") == "operation_applied":
            logger.info(
                "[WS_BROADCAST] doc=%s server_version=%s from_user=%s sent=%s failed=%s text='%s'",
                document_id,
                payload.get("server_version"),
                payload.get("user_id"),
                sent,
                failed,
                payload.get("content_text"),
            )

    async def broadcast_event(self, payload: dict[str, Any]):
        document_id = payload.get("document_id")
        if not document_id:
            return

        event_type = payload.get("type")
        if event_type == "access_removed":
            await self.kick_user(document_id, payload.get("user_id"), payload.get("message") or "Access removed")
            return

        if event_type == "role_changed":
            await self.notify_role_changed(document_id, payload.get("user_id"), payload.get("role") or "viewer")
            return

        if event_type == "document_deleted":
            async with self.lock:
                user_ids = list(self.active.get(document_id, {}).keys())
            for uid in user_ids:
                await self.kick_user(document_id, uid, payload.get("message") or "Document deleted")
            return

        await self.broadcast(document_id, payload)

    async def update_cursor(self, document_id: str, user_id: str, cursor: dict[str, Any] | None):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
            if conn:
                conn.cursor = cursor
                username = conn.username
                color = conn.color
            else:
                username = None
                color = color_for_user(user_id)
        await self.broadcast(document_id, {
            "type": "cursor",
            "document_id": document_id,
            "user_id": user_id,
            "username": username,
            "color": color,
            "cursor": cursor,
        }, exclude_user_id=user_id)

    async def notify_role_changed(self, document_id: str, user_id: str, role: str):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
            if conn:
                conn.role = role
                ws = conn.websocket
            else:
                ws = None
        if ws:
            await ws.send_json({"type": "role_changed", "document_id": document_id, "role": role})
            await self.broadcast_presence(document_id)

    async def kick_user(self, document_id: str, user_id: str, reason: str = "Access removed"):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
            ws = conn.websocket if conn else None
        if ws:
            try:
                await ws.send_json({"type": "access_removed", "document_id": document_id, "message": reason})
                await ws.close(code=4403)
            except Exception:
                pass
        await self.disconnect(document_id, user_id)

    def users_for_document_sync(self, document_id: str) -> list[dict[str, Any]]:
        return [
            {
                "user_id": c.user_id,
                "username": c.username,
                "role": c.role,
                "color": c.color,
                "cursor": c.cursor,
            }
            for c in self.active.get(document_id, {}).values()
        ]

    async def users_for_document(self, document_id: str) -> list[dict[str, Any]]:
        async with self.lock:
            return self.users_for_document_sync(document_id)

    async def broadcast_presence(self, document_id: str):
        users = await self.users_for_document(document_id)
        await self.broadcast(document_id, {
            "type": "presence",
            "document_id": document_id,
            "users": users,
        })


manager = ConnectionManager()
