# RelayAPI rate limiter (thin slice)

Three **stateless** API nodes plus one **metering coordinator**. The harness round-robins across the nodes the way a load balancer would.

You need **Python 3.9+**. No Docker, Redis, or paid services.

## Setup (once)

```bash
cd solution
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run service + harness

```bash
python3 run_stack.py --with-harness
```

That starts the coordinator on `:7099`, nodes on `:7101–7103`, runs the boundary scenarios, prints a PASS/FAIL table, and exits.

To leave the stack up (manual curl):

```bash
python3 run_stack.py
# another terminal:
curl -sD - -H 'X-Customer-Id: acme' http://127.0.0.1:7101/api/v1/ping
```

## What you should see

| Scenario | Obvious if correct | Obvious if naive |
| -------- | ------------------ | ---------------- |
| Isolation | `acme` and `globex` each allowed **exactly 100** | One customer stealing the other's budget |
| Hard cutoff | 250 shots → **100** × 200, **150** × 429 | Allowed 101+ |
| Fixed-window trap | Second burst **all 429**; naive column shows **200** | 100 more allowed after `:00` |
| Northwind | 01:30 UTC capped at **300**; 02:30 UTC allows **1200** with policy `exception:northwind-nightly-batch` | Hardcoded `if northwind` or 429 during batch |
| Fail-closed | Coordinator unreachable → **429**, not 200 | Passthrough when the meter is down |

## Identity and policies

- Customer id: `X-Customer-Id` (`acme`, `globex`, `northwind`).
- Quotas and the Northwind batch window live in `policies.yaml`, not in `if customer_id == ...` branches.
- Internal (not customer-facing) header: `X-Relay-Quota-Policy`.

## Clock

The coordinator is the only clock. The harness freezes it so quota-boundary tests do not wait real minutes. Clock override is disabled unless `HARNESS_MODE=1` (the default for `run_stack.py`).
