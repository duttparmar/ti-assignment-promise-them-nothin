import os
from typing import Optional

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

app = FastAPI()

COORDINATOR = "http://127.0.0.1:7099"

NODE_ID = os.getenv("NODE_ID", "unknown")


@app.get("/api/v1/ping")
async def api_ping(
    x_customer_id: Optional[str] = Header(default=None),
):
    if not x_customer_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "X-Customer-Id header is required"},
        )

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                f"{COORDINATOR}/check",
                json={"customer_id": x_customer_id},
            )

        response.raise_for_status()
        decision = response.json()

    except httpx.HTTPError:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limiter unavailable"},
            headers={
                "Retry-After": "1",
                "X-Relay-Quota-Policy": "fail-closed",
                "X-Relay-Node": NODE_ID,
            },
        )

    allowed = decision.get("allowed", False)
    policy = decision.get("policy", "unknown")

    headers = {
        "X-Relay-Node": NODE_ID,
        "X-Relay-Quota-Policy": policy,
    }

    if not allowed:
        retry_after = int(decision.get("retry_after", 1))

        headers["Retry-After"] = str(max(1, retry_after))

        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers=headers,
        )

    return JSONResponse(
        status_code=200,
        content={"message": "pong"},
        headers=headers,
    )
