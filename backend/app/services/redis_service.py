from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings


class RedisService:
    def __init__(self):
        self.redis: Redis | None = None

    async def connect(self):
        if self.redis is None:
            self.redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            await self.redis.ping()

    async def close(self):
        if self.redis:
            await self.redis.close()
            self.redis = None

    async def get_json(self, key: str, default: Any = None) -> Any:
        await self.connect()
        assert self.redis
        raw = await self.redis.get(key)
        if raw is None:
            return default
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ex: int | None = None):
        await self.connect()
        assert self.redis
        await self.redis.set(key, json.dumps(value), ex=ex)

    async def delete(self, key: str):
        await self.connect()
        assert self.redis
        await self.redis.delete(key)

    @staticmethod
    def document_key(document_id: str) -> str:
        return f"document:{document_id}:snapshot"

    @staticmethod
    def presence_key(document_id: str) -> str:
        return f"document:{document_id}:presence"


redis_service = RedisService()
