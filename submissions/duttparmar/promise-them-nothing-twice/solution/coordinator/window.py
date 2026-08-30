"""True sliding window: at most `limit` events in any `window_ms` interval.

Naive fixed windows are wrong at the boundary: they can grant 2x the quota
in two seconds that straddle :00. This structure does not.
"""

from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class SlidingWindowLimiter:
    def __init__(self, window_ms: int = 60_000) -> None:
        self.window_ms = window_ms
        self._hits: Dict[str, Deque[int]] = defaultdict(deque)

    def allow(self, customer_id: str, limit: int, now_ms: int) -> Tuple[bool, int, int]:
        """Returns (allowed, used_after, retry_after_seconds)."""
        if limit < 1:
            return False, 0, 1

        q = self._hits[customer_id]
        cutoff = now_ms - self.window_ms
        while q and q[0] <= cutoff:
            q.popleft()

        used = len(q)
        if used >= limit:
            retry_ms = q[0] + self.window_ms - now_ms
            retry_s = max(1, (retry_ms + 999) // 1000)
            return False, used, retry_s

        q.append(now_ms)
        return True, used + 1, 0

    def used(self, customer_id: str, now_ms: int) -> int:
        q = self._hits[customer_id]
        cutoff = now_ms - self.window_ms
        while q and q[0] <= cutoff:
            q.popleft()
        return len(q)


def naive_fixed_window_allowances(timestamps_ms, limit: int, window_ms: int = 60_000) -> int:
    """How many requests a per-minute fixed window would have allowed.

    Used by the harness to show the boundary trap, not for enforcement.
    """
    buckets = defaultdict(int)
    allowed = 0
    for ts in timestamps_ms:
        bucket = ts // window_ms
        if buckets[bucket] < limit:
            buckets[bucket] += 1
            allowed += 1
    return allowed
