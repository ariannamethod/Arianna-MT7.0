import asyncio
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Hashable, Tuple, TypeVar

T = TypeVar("T")

class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: Dict[Hashable, Tuple[float, T]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: Hashable) -> T | None:
        async with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None
            return value

    async def set(self, key: Hashable, value: T) -> None:
        async with self._lock:
            self._data[key] = (time.time() + self.ttl, value)


def async_ttl_cache(ttl: float) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    cache = TTLCache(ttl)

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key = (args, frozenset(kwargs.items()))
            cached = await cache.get(key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            await cache.set(key, result)
            return result

        return wrapper

    return decorator
