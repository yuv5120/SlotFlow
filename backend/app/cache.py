import logging
import uuid
from contextlib import contextmanager
from datetime import date

import redis

from .config import settings

log = logging.getLogger(__name__)
client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=0.3)


def availability_key(staff_id: uuid.UUID | str, service_id: uuid.UUID | str, day: date) -> str:
    return f"availability:{staff_id}:{service_id}:{day.isoformat()}"


def get(key: str) -> str | None:
    try:
        return client.get(key)
    except redis.RedisError:
        log.warning("Redis unavailable; reading availability from PostgreSQL")
        return None


def set_short(key: str, value: str) -> None:
    try:
        client.setex(key, 20, value)
    except redis.RedisError:
        log.warning("Redis unavailable; availability remains uncached")


def invalidate(staff_id: uuid.UUID | str, day: date) -> None:
    try:
        for key in client.scan_iter(f"availability:{staff_id}:*:{day.isoformat()}"):
            client.delete(key)
    except redis.RedisError:
        log.warning("Redis unavailable; cached availability will expire within 20 seconds")


@contextmanager
def slot_lock(staff_id: uuid.UUID, starts_at_iso: str):
    key = f"booking-lock:{staff_id}:{starts_at_iso}"
    token = uuid.uuid4().hex
    acquired = False
    try:
        acquired = bool(client.set(key, token, nx=True, px=10_000))
    except redis.RedisError:
        log.warning("Redis unavailable; relying on PostgreSQL overlap constraint")
        acquired = True
    try:
        yield acquired
    finally:
        if acquired:
            try:
                client.eval(
                    "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                    1, key, token,
                )
            except redis.RedisError:
                pass
