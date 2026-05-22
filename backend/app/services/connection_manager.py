from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket


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
    cursor: int | None = None


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

    async def disconnect(self, document_id: str, user_id: str):
        async with self.lock:
            if document_id in self.active:
                self.active[document_id].pop(user_id, None)
                if not self.active[document_id]:
                    self.active.pop(document_id, None)
        await self.broadcast_presence(document_id)

    async def send_personal(self, document_id: str, user_id: str, payload: dict[str, Any]):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
        if conn:
            await conn.websocket.send_json(payload)

    async def broadcast(self, document_id: str, payload: dict[str, Any], exclude_user_id: str | None = None):
        async with self.lock:
            conns = list(self.active.get(document_id, {}).values())
        living: list[ClientConnection] = []
        for conn in conns:
            if exclude_user_id and conn.user_id == exclude_user_id:
                continue
            try:
                await conn.websocket.send_json(payload)
                living.append(conn)
            except Exception:
                pass

    async def broadcast_event(self, payload: dict[str, Any]):
        document_id = payload.get("document_id")
        if not document_id:
            return
        await self.broadcast(document_id, payload)

    async def update_cursor(self, document_id: str, user_id: str, cursor: int | None):
        async with self.lock:
            conn = self.active.get(document_id, {}).get(user_id)
            if conn:
                conn.cursor = cursor
        await self.broadcast(document_id, {
            "type": "cursor",
            "document_id": document_id,
            "user_id": user_id,
            "cursor": cursor,
        }, exclude_user_id=user_id)

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
