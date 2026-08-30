"""Load customer quotas and scheduled commercial exceptions from YAML.

No customer-id special cases live in request-path code. Northwind is a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml


@dataclass(frozen=True)
class ExceptionWindow:
    id: str
    reason: str
    start_utc: time
    end_utc: time
    effective_rpm: int


@dataclass
class CustomerPolicy:
    customer_id: str
    contracted_rpm: int
    exceptions: List[ExceptionWindow] = field(default_factory=list)

    def resolve(self, now: datetime) -> Tuple[int, str]:
        """Return (effective_rpm, policy_label) for `now` (must be UTC-aware)."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        current = now.timetz().replace(tzinfo=None)
        for ex in self.exceptions:
            if _in_tod_window(current, ex.start_utc, ex.end_utc):
                return ex.effective_rpm, f"exception:{ex.id}"
        return self.contracted_rpm, "contracted"


def _in_tod_window(now: time, start: time, end: time) -> bool:
    """Half-open [start, end) in UTC clock time. Does not wrap midnight."""
    n = _seconds(now)
    return _seconds(start) <= n < _seconds(end)


def _seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _parse_hhmm(value: str) -> time:
    parts = value.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    second = int(parts[2]) if len(parts) > 2 else 0
    return time(hour, minute, second)


@dataclass
class PolicyBook:
    window_seconds: int
    customers: Dict[str, CustomerPolicy]

    def get(self, customer_id: str) -> Optional[CustomerPolicy]:
        return self.customers.get(customer_id)


def load_policies(path: Path) -> PolicyBook:
    raw = yaml.safe_load(path.read_text())
    customers = {}
    for cid, body in raw["customers"].items():
        exceptions = []
        for ex in body.get("exceptions") or []:
            exceptions.append(
                ExceptionWindow(
                    id=ex["id"],
                    reason=ex.get("reason") or "",
                    start_utc=_parse_hhmm(ex["start_utc"]),
                    end_utc=_parse_hhmm(ex["end_utc"]),
                    effective_rpm=int(ex["effective_rpm"]),
                )
            )
        customers[cid] = CustomerPolicy(
            customer_id=cid,
            contracted_rpm=int(body["contracted_rpm"]),
            exceptions=exceptions,
        )
    return PolicyBook(
        window_seconds=int(raw.get("window_seconds") or 60),
        customers=customers,
    )
