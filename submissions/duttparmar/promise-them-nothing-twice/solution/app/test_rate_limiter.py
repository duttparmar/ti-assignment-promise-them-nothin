from rate_limiter import SlidingWindowRateLimiter


limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)

for i in range(4):
    print(f"Request {i + 1}: {limiter.allow()}")
