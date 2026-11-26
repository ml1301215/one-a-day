"""极简内存限流。单进程够用；多实例部署请换成 Redis。"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from .config import settings

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def allow(key: str, limit: int | None = None, window: float = 60.0) -> bool:
    lim = limit if limit is not None else settings.rate_limit_per_min
    if lim <= 0:
        return True
    now = time.time()
    q = _BUCKETS[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= lim:
        return False
    q.append(now)
    return True
