"""
Aggregate usage / estimated cost from Supabase (`quiz_generation_usage`).

Prefers RPC `quizzly_usage_by_day` when installed; otherwise loads rows and aggregates in Python.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bknd.quizzly_rate_limit import TABLE_NAME, supabase_admin_client


@dataclass(frozen=True)
class DailyRow:
    day: date
    generations: int
    total_cost_usd: float
    distinct_visitors: int


def _parse_ts_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _utc_day(d: datetime) -> date:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).date()


def fetch_daily_stats(
    start_utc: datetime,
    end_utc_exclusive: datetime,
) -> tuple[list[DailyRow], str | None]:
    """
    Return per-UTC-day aggregates in [start_utc, end_utc_exclusive).
    """
    supabase = supabase_admin_client()
    if supabase is None:
        return [], "Supabase is not configured (service role key missing)."

    p_start = start_utc.astimezone(timezone.utc).isoformat()
    p_end = end_utc_exclusive.astimezone(timezone.utc).isoformat()

    try:
        res = supabase.rpc(
            "quizzly_usage_by_day",
            {"p_start": p_start, "p_end": p_end},
        ).execute()
        raw = res.data
        if raw is None:
            raw = []
        out: list[DailyRow] = []
        for row in raw:
            d = row.get("day")
            if hasattr(d, "isoformat"):
                day_val = date.fromisoformat(str(d)[:10])
            elif isinstance(d, str):
                day_val = date.fromisoformat(d[:10])
            else:
                continue
            out.append(
                DailyRow(
                    day=day_val,
                    generations=int(row.get("generations") or 0),
                    total_cost_usd=float(row.get("total_cost_usd") or 0),
                    distinct_visitors=int(row.get("distinct_visitors") or 0),
                )
            )
        out.sort(key=lambda r: r.day)
        return out, None
    except Exception:
        return _fetch_daily_stats_fallback(supabase, start_utc, end_utc_exclusive)


def _fetch_daily_stats_fallback(
    supabase: Any,
    start_utc: datetime,
    end_utc_exclusive: datetime,
) -> tuple[list[DailyRow], str | None]:
    """Aggregate from raw rows when RPC is missing."""
    start_iso = start_utc.astimezone(timezone.utc).isoformat()
    end_iso = end_utc_exclusive.astimezone(timezone.utc).isoformat()

    all_rows: list[dict] = []
    page = 0
    page_size = 1000
    try:
        while True:
            q = (
                supabase.table(TABLE_NAME)
                .select("created_at, estimated_cost_usd, ip_hash")
                .gte("created_at", start_iso)
                .lt("created_at", end_iso)
            )
            res = q.range(page * page_size, (page + 1) * page_size - 1).execute()
            batch = res.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
            if page > 1000:
                break
    except Exception as e:
        return [], str(e)

    by_day: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"generations": 0, "cost": 0.0, "ips": set()}
    )
    for r in all_rows:
        ts = r.get("created_at")
        if not ts:
            continue
        try:
            d = _utc_day(_parse_ts_iso(str(ts)))
        except Exception:
            continue
        g = by_day[d]
        g["generations"] += 1
        c = r.get("estimated_cost_usd")
        if c is not None:
            try:
                g["cost"] += float(c)
            except (TypeError, ValueError):
                pass
        ih = r.get("ip_hash")
        if ih:
            g["ips"].add(str(ih))

    out = [
        DailyRow(
            day=d,
            generations=b["generations"],
            total_cost_usd=float(b["cost"]),
            distinct_visitors=len(b["ips"]),
        )
        for d, b in sorted(by_day.items(), key=lambda x: x[0])
    ]
    return out, None


def fetch_raw_events(
    start_utc: datetime,
    end_utc_exclusive: datetime,
) -> tuple[list[dict], str | None]:
    """Raw rows for hour-of-day / secondary charts."""
    supabase = supabase_admin_client()
    if supabase is None:
        return [], "Supabase is not configured."

    start_iso = start_utc.astimezone(timezone.utc).isoformat()
    end_iso = end_utc_exclusive.astimezone(timezone.utc).isoformat()
    all_rows: list[dict] = []
    page = 0
    page_size = 1000
    try:
        while True:
            res = (
                supabase.table(TABLE_NAME)
                .select("created_at")
                .gte("created_at", start_iso)
                .lt("created_at", end_iso)
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            )
            batch = res.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
            if page > 500:
                break
    except Exception as e:
        return [], str(e)
    return all_rows, None


def hour_of_day_counts(rows: list[dict]) -> dict[int, int]:
    c: Counter[int] = Counter()
    for r in rows:
        ts = r.get("created_at")
        if not ts:
            continue
        try:
            dt = _parse_ts_iso(str(ts)).astimezone(timezone.utc)
            c[dt.hour] += 1
        except Exception:
            continue
    return dict(sorted(c.items()))


def period_bounds(
    label: str,
    custom_start: date | None,
    custom_end: date | None,
) -> tuple[datetime, datetime]:
    """Return (start_utc inclusive, end_utc_exclusive) for the selected period."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if label == "Custom" and custom_start and custom_end:
        s = datetime(
            custom_start.year,
            custom_start.month,
            custom_start.day,
            tzinfo=timezone.utc,
        )
        e_day = datetime(
            custom_end.year,
            custom_end.month,
            custom_end.day,
            tzinfo=timezone.utc,
        )
        end_ex = e_day + timedelta(days=1)
        return s, end_ex

    if label == "All time":
        return datetime(2020, 1, 1, tzinfo=timezone.utc), today_start + timedelta(days=1)

    days_map = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
    n = days_map.get(label, 7)
    start = today_start - timedelta(days=n - 1)
    end_ex = today_start + timedelta(days=1)
    return start, end_ex
