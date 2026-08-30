import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, limit, window_seconds=60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests = deque()

    def allow(self):
        now = time.time()

        while self.requests and self.requests[0] <= now - self.window_seconds:
            self.requests.popleft()

        if len(self.requests) >= self.limit:
            return False

        self.requests.append(now)
        return True


