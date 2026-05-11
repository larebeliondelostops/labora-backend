from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, status


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, *, key: str, limit: int, window_seconds: int) -> None:
        now = monotonic()
        bucket = self._hits[key]

        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()

        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        bucket.append(now)

    def clear(self) -> None:
        self._hits.clear()


public_rate_limiter = InMemoryRateLimiter()
