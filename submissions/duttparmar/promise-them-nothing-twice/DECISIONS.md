# Decisions — Promise Them Nothing Twice

## Conflict resolution

Priya (never exceed contracted RPM; no hidden customer bypasses) and Marcus (Northwind must not 429 in 02:00–04:00 UTC) cannot both be literally true. Northwind’s batch is **800–1200 RPM vs a 300 RPM contract**.

**Chose:** a **named, time-bounded commercial exception in config**. The limiter is the same for every customer. It reads `effective_rpm = resolve(customer, now)`. Northwind’s nightly window is a row in `policies.yaml` (`northwind-nightly-batch`, 1200 RPM, 02:00–04:00 UTC). Customers still see ordinary 200s; internally `X-Relay-Quota-Policy` names the exception so it is auditable, not a midnight `if`.

**Rejected:** (1) `if customer_id == "northwind"` in the request path; (2) turning limits off globally; (3) asking Northwind to reshape the ERP job; (4) 24/7 uncapped Northwind; (5) “bill the overage” in v1 (Priya forbade it); (6) pretending 300 RPM can absorb 1200 RPM with bursts.

The honest leftover: **sales/legal must ratify the exception** before GA. Engineering made the gap visible and bounded, not invisible.

## Technical design

**Algorithm:** true **sliding 60s window** (deque of timestamps, admit iff `count < limit` after dropping events older than the window). Well-known, easy to explain to an enterprise prospect: “at most N requests in any 60-second interval.”

**Rejected fixed windows** — they grant ~2× quota across a minute boundary (the trap the harness prints). **Rejected per-node counters** — 3 nodes ⇒ 3× quota. **Did not assume Redis**; this laptop has none. App nodes are stateless; they call a **single coordinator** that serializes per customer. Same shape as Redis+Lua, worse ops story.

**Error direction:** coordinator timeout ⇒ **fail closed (429)**. Prefer under-limiting. Coordinator **crash-restart wipes in-memory windows** and can over-limit until the window refills — called out, not papered over.

## Verification

The harness **proves** (under frozen coordinator time, 3 processes, round-robin): exact 100 RPM isolation for two peers; 101st denied; sliding window refuses the post-`:00` burst that a fixed window would allow; Northwind 300 outside / 1200 inside with a named policy; fail-closed on a dead meter.

It **does not prove:** multi-host networks, coordinator crash durability, clock-skew between real machines, 90-minute sustained 1200 RPM, or that Legal signed the exception.

## If I had four more hours

Persist the window (SQLite/Redis AOF) so restarts cannot over-limit; add a wrap-around midnight schedule; emit an audit log line per exception admit; run a real-time 60s soak at 1200 RPM.
