from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import threading

from fastapi import FastAPI
from pydantic import BaseModel

from coordinator.window import SlidingWindowLimiter
from app.policy import load_policies


app = FastAPI()

# Load customer policies
POLICY_PATH = Path(__file__).resolve().parent.parent / "policies.yaml"
POLICIES = load_policies(POLICY_PATH)

# Shared sliding-window limiter
LIMITER = SlidingWindowLimiter(
    window_ms=POLICIES.window_seconds * 1000
)

# Harness-controlled clock
FROZEN_NOW_MS = None

# Protect check + update from concurrent requests
LIMITER_LOCK = threading.Lock()


class ClockRequest(BaseModel):
    now_ms: Optional[int] = None


class CheckRequest(BaseModel):
    customer_id: str


def current_time_ms() -> int:
    """Return current time or harness-controlled frozen time."""
    if FROZEN_NOW_MS is not None:
        return FROZEN_NOW_MS

    return int(datetime.now(timezone.utc).timestamp() * 1000)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/harness/clock")
def set_clock(request: ClockRequest):
    """
    Set or clear the harness clock.

    IMPORTANT:
    Changing the clock does NOT clear limiter state.
    This is required for the sliding-window boundary test.
    """
    global FROZEN_NOW_MS

    with LIMITER_LOCK:
        FROZEN_NOW_MS = request.now_ms

    return {
        "now_ms": FROZEN_NOW_MS
    }


@app.post("/harness/reset")
def reset_limiter():
    """
    Reset limiter state between independent harness scenarios.
    """
    with LIMITER_LOCK:
        LIMITER._hits.clear()

    return {
        "status": "reset"
    }


@app.post("/check")
def check(request: CheckRequest):
    """
    Atomically check and consume one request from the
    customer's sliding-window quota.
    """

    customer = POLICIES.get(request.customer_id)

    # Unknown customer
    if customer is None:
        return {
            "allowed": False,
            "used": 0,
            "limit": 0,
            "retry_after": 60,
            "policy": "unknown-customer",
        }

    now_ms = current_time_ms()

    now_dt = datetime.fromtimestamp(
        now_ms / 1000.0,
        tz=timezone.utc,
    )

    # Resolve contracted or exception quota
    limit, policy = customer.resolve(now_dt)

    # Check + record atomically
    with LIMITER_LOCK:
        allowed, used, retry_after = LIMITER.allow(
            request.customer_id,
            limit,
            now_ms,
        )

    return {
        "allowed": allowed,
        "used": used,
        "limit": limit,
        "retry_after": retry_after,
        "policy": policy,
    }
