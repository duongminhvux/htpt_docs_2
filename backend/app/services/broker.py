from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika import ExchangeType, Message, RobustChannel, RobustConnection

from app.core.config import settings

OPS_EXCHANGE = "document.ops.exchange"
OPS_QUEUE = "document.ops.queue"
EVENTS_EXCHANGE = "document.events.exchange"


class RabbitBroker:
    def __init__(self):
        self.connection: RobustConnection | None = None
        self.channel: RobustChannel | None = None
        self.ops_exchange = None
        self.events_exchange = None

    async def connect(self):
        if self.connection and not self.connection.is_closed:
            return
        self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=1)
        self.ops_exchange = await self.channel.declare_exchange(OPS_EXCHANGE, ExchangeType.DIRECT, durable=True)
        self.events_exchange = await self.channel.declare_exchange(EVENTS_EXCHANGE, ExchangeType.FANOUT, durable=True)
        queue = await self.channel.declare_queue(OPS_QUEUE, durable=True)
        await queue.bind(self.ops_exchange, routing_key="operation")

    async def close(self):
        if self.connection and not self.connection.is_closed:
            await self.connection.close()

    async def publish_operation(self, payload: dict[str, Any]):
        await self.connect()
        assert self.ops_exchange
        body = json.dumps(payload).encode()
        await self.ops_exchange.publish(
            Message(body, content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key="operation",
        )

    async def publish_event(self, payload: dict[str, Any]):
        await self.connect()
        assert self.events_exchange
        body = json.dumps(payload).encode()
        await self.events_exchange.publish(
            Message(body, content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key="",
        )

    async def consume_events(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        await self.connect()
        assert self.channel and self.events_exchange
        queue_name = f"gateway.events.{uuid.uuid4()}"
        queue = await self.channel.declare_queue(queue_name, exclusive=True, auto_delete=True)
        await queue.bind(self.events_exchange)

        async def _handler(message: aio_pika.IncomingMessage):
            async with message.process(ignore_processed=True):
                payload = json.loads(message.body.decode())
                await callback(payload)

        await queue.consume(_handler)

    async def consume_operations(self, callback: Callable[[dict[str, Any]], Awaitable[None]]):
        await self.connect()
        assert self.channel
        queue = await self.channel.declare_queue(OPS_QUEUE, durable=True)

        async def _handler(message: aio_pika.IncomingMessage):
            async with message.process(requeue=True):
                payload = json.loads(message.body.decode())
                await callback(payload)

        await queue.consume(_handler)


broker = RabbitBroker()
