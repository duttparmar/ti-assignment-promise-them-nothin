from __future__ import annotations

import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone

import httpx

from coordinator.window import (
    SlidingWindowLimiter,
    naive_fixed_window_allowances,
)


COORDINATOR = "http://127.0.0.1:7099"

NODES = [
    "http://127.0.0.1:7101",
    "http://127.0.0.1:7102",
    "http://127.0.0.1:7103",
]


class RoundRobin:
    def __init__(self):
        self.i = 0
        self._lock = asyncio.Lock()

    async def next(self) -> str:
        async with self._lock:
            url = NODES[self.i % 3]
            self.i += 1
            return url


async def set_clock(
    client: httpx.AsyncClient,
    now_ms: int | None,
) -> None:
    r = await client.post(
        f"{COORDINATOR}/harness/clock",
        json={"now_ms": now_ms},
    )
    r.raise_for_status()


async def reset_limiter(client: httpx.AsyncClient) -> None:
    r = await client.post(
        f"{COORDINATOR}/harness/reset"
    )
    r.raise_for_status()


async def ping(
    client: httpx.AsyncClient,
    rr: RoundRobin,
    customer: str,
) -> httpx.Response:

    return await client.get(
        f"{await rr.next()}/api/v1/ping",
        headers={"X-Customer-Id": customer},
    )


async def burst(
    client,
    rr,
    customer: str,
    n: int,
    concurrency: int = 40,
):
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def one():
        async with sem:
            try:
                r = await ping(client, rr, customer)

                return (
                    r.status_code,
                    r.headers.get("x-relay-node"),
                    r.headers.get("x-relay-quota-policy"),
                )

            except httpx.HTTPError as e:
                return (
                    f"err:{e.__class__.__name__}",
                    None,
                    None,
                )

    tasks = [
        asyncio.create_task(one())
        for _ in range(n)
    ]

    for t in tasks:
        results.append(await t)

    return results


def summarize(results):
    codes = Counter(r[0] for r in results)

    nodes = Counter(
        r[1]
        for r in results
        if r[1]
    )

    policies = Counter(
        r[2]
        for r in results
        if r[2]
    )

    return {
        "n": len(results),
        "200": codes.get(200, 0),
        "429": codes.get(429, 0),
        "other": sum(
            v
            for k, v in codes.items()
            if k not in (200, 429)
        ),
        "nodes": dict(nodes),
        "policies": dict(policies),
    }


def row(name, ok, detail):
    flag = "PASS" if ok else "FAIL"

    print(
        f"  {flag:4}  "
        f"{name:28}  "
        f"{detail}"
    )

    return ok


async def scenario_isolation(client):

    print(
        "\n== 1. isolation + fairness "
        "(acme vs globex, 100 RPM, 3 nodes) =="
    )

    await reset_limiter(client)

    await set_clock(
        client,
        _ms("2026-03-15T12:00:00Z"),
    )

    rr = RoundRobin()

    acme = await burst(
        client,
        rr,
        "acme",
        150,
    )

    globex = await burst(
        client,
        rr,
        "globex",
        150,
    )

    sa = summarize(acme)
    sg = summarize(globex)

    print(
        f"     acme   200={sa['200']:3} "
        f"429={sa['429']:3} "
        f"nodes={sa['nodes']}"
    )

    print(
        f"     globex 200={sg['200']:3} "
        f"429={sg['429']:3} "
        f"nodes={sg['nodes']}"
    )

    ok = True

    ok &= row(
        "acme exact budget",
        sa["200"] == 100 and sa["429"] == 50,
        f"allowed={sa['200']}",
    )

    ok &= row(
        "globex exact budget",
        sg["200"] == 100 and sg["429"] == 50,
        f"allowed={sg['200']}",
    )

    ok &= row(
        "not 3x (per-node leak)",
        sa["200"] != 300
        and sg["200"] != 300,
        "would be 300 if nodes were independent",
    )

    ok &= row(
        "traffic hit all 3 nodes",
        len(sa["nodes"]) == 3
        and len(sg["nodes"]) == 3,
        f"{sa['nodes']}",
    )

    return ok


async def scenario_hard_cutoff(client):

    print(
        "\n== 2. hard cutoff "
        "(acme 250 shots at frozen t, limit 100) =="
    )

    await reset_limiter(client)

    await set_clock(
        client,
        _ms("2026-03-15T13:00:00Z"),
    )

    rr = RoundRobin()

    s = summarize(
        await burst(
            client,
            rr,
            "acme",
            250,
        )
    )

    print(
        f"     200={s['200']:3} "
        f"429={s['429']:3} "
        f"other={s['other']}"
    )

    ok = True

    ok &= row(
        "never exceeds 100",
        s["200"] == 100,
        f"allowed={s['200']}",
    )

    ok &= row(
        "remainder 429",
        s["429"] == 150,
        f"429={s['429']}",
    )

    return ok


async def scenario_fixed_window_trap(client):

    print(
        "\n== 3. fixed-window boundary trap =="
    )

    await reset_limiter(client)

    t0 = _ms(
        "2026-03-15T14:00:59Z"
    )

    t1 = _ms(
        "2026-03-15T14:01:00.500Z"
    )

    await set_clock(client, t0)

    rr = RoundRobin()

    first = await burst(
        client,
        rr,
        "acme",
        100,
    )

    await set_clock(client, t1)

    second = await burst(
        client,
        rr,
        "acme",
        100,
    )

    s0 = summarize(first)
    s1 = summarize(second)

    timestamps = (
        [t0] * s0["200"]
        + [t1] * 100
    )

    naive = naive_fixed_window_allowances(
        timestamps,
        limit=100,
        window_ms=60_000,
    )

    sliding_allowed = (
        s0["200"] + s1["200"]
    )

    print(
        f"     before :00  "
        f"allowed={s0['200']}"
    )

    print(
        f"     after  :00  "
        f"allowed={s1['200']} "
        f"429={s1['429']}"
    )

    print(
        f"     sliding total allowed="
        f"{sliding_allowed} "
        f"  naive fixed-window would allow="
        f"{naive}"
    )

    ok = True

    ok &= row(
        "first burst fills quota",
        s0["200"] == 100,
        f"{s0['200']}",
    )

    ok &= row(
        "second burst denied",
        s1["200"] == 0
        and s1["429"] == 100,
        f"allowed={s1['200']}",
    )

    ok &= row(
        "naive would have doubled",
        naive == 200,
        f"naive={naive}",
    )

    return ok


async def scenario_northwind(client):

    print(
        "\n== 4. Northwind: "
        "contracted vs scheduled exception =="
    )

    await reset_limiter(client)

    ok = True

    # Outside batch window: 300 RPM

    await set_clock(
        client,
        _ms("2026-03-15T01:30:00Z"),
    )

    rr = RoundRobin()

    outside = summarize(
        await burst(
            client,
            rr,
            "northwind",
            400,
        )
    )

    print(
        f"     01:30 UTC  "
        f"200={outside['200']:3} "
        f"429={outside['429']:3} "
        f"policy={outside['policies']}"
    )

    ok &= row(
        "outside window capped at 300",
        outside["200"] == 300,
        f"allowed={outside['200']}",
    )

    ok &= row(
        "outside uses contracted",
        outside["policies"].get(
            "contracted",
            0,
        ) >= 300,
        str(outside["policies"]),
    )

    # Inside batch window: 1200 effective RPM

    await reset_limiter(client)

    await set_clock(
        client,
        _ms("2026-03-15T02:30:00Z"),
    )

    rr = RoundRobin()

    inside = summarize(
        await burst(
            client,
            rr,
            "northwind",
            1300,
            concurrency=80,
        )
    )

    print(
        f"     02:30 UTC  "
        f"200={inside['200']:3} "
        f"429={inside['429']:3} "
        f"policy={inside['policies']}"
    )

    ok &= row(
        "batch window allows 1200",
        inside["200"] == 1200,
        f"allowed={inside['200']}",
    )

    ok &= row(
        "1201st+ are 429",
        inside["429"] == 100,
        f"429={inside['429']}",
    )

    ok &= row(
        "policy is named exception",
        "exception:northwind-nightly-batch"
        in inside["policies"],
        str(inside["policies"]),
    )

    ok &= row(
        "customer still gets 200 not a special code",
        inside["200"] == 1200,
        "invisible to customer",
    )

    return ok


async def scenario_fail_closed(client):

    print(
        "\n== 5. fail-closed "
        "(coordinator down → 429, not passthrough) =="
    )

    from app import main as app_main

    original = app_main.COORDINATOR

    app_main.COORDINATOR = (
        "http://127.0.0.1:1"
    )

    try:

        transport = httpx.ASGITransport(
            app=app_main.app
        )

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as asgi:

            r = await asgi.get(
                "/api/v1/ping",
                headers={
                    "X-Customer-Id": "acme"
                },
            )

        detail = (
            f"status={r.status_code} "
            f"policy="
            f"{r.headers.get('x-relay-quota-policy')}"
        )

        ok = row(
            "dead meter returns 429",
            r.status_code == 429
            and r.headers.get(
                "x-relay-quota-policy"
            ) == "fail-closed",
            detail,
        )

    finally:

        app_main.COORDINATOR = original

    return ok


def _ms(iso: str) -> int:

    if iso.endswith("Z"):
        iso = (
            iso[:-1]
            + "+00:00"
        )

    return int(
        datetime.fromisoformat(
            iso
        ).timestamp()
        * 1000
    )


def scenario_in_process_window():

    print(
        "\n== 0. in-process sliding window "
        "(no HTTP) =="
    )

    lim = SlidingWindowLimiter(
        window_ms=60_000
    )

    t = 1_000_000

    allowed = sum(
        1
        for _ in range(100)
        if lim.allow(
            "c",
            100,
            t,
        )[0]
    )

    denied = lim.allow(
        "c",
        100,
        t,
    )[0]

    later = lim.allow(
        "c",
        100,
        t + 60_000,
    )[0]

    ok = True

    ok &= row(
        "exactly 100 then deny",
        allowed == 100
        and denied is False,
        f"allowed={allowed} "
        f"101st={denied}",
    )

    ok &= row(
        "slot frees after window",
        later is True,
        f"at t+window allowed={later}",
    )

    return ok


async def amain() -> int:

    print(
        "RelayAPI limiter harness — "
        "3 nodes, shared coordinator, "
        "sliding 60s window"
    )

    async with httpx.AsyncClient(
        timeout=10.0
    ) as client:

        await client.get(
            f"{COORDINATOR}/health"
        )

        results = []

        results.append(
            scenario_in_process_window()
        )

        results.append(
            await scenario_isolation(
                client
            )
        )

        results.append(
            await scenario_hard_cutoff(
                client
            )
        )

        results.append(
            await scenario_fixed_window_trap(
                client
            )
        )

        results.append(
            await scenario_northwind(
                client
            )
        )

        results.append(
            await scenario_fail_closed(
                client
            )
        )

    print("\n== summary ==")

    passed = sum(
        1
        for x in results
        if x
    )

    print(
        f"  {passed}/{len(results)} "
        f"scenarios passed"
    )

    return (
        0
        if all(results)
        else 1
    )


def main():

    try:
        raise SystemExit(
            asyncio.run(amain())
        )

    except httpx.HTTPError as e:

        print(
            "harness could not reach the stack:",
            e,
            file=sys.stderr,
        )

        raise SystemExit(2)


if __name__ == "__main__":
    main()
