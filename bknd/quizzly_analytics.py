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

USAGE_DETAIL_COLUMNS = (
    "created_at,estimated_cost_usd,user_ip_id,generation_mode,material_source,"
    "material_quantity,num_questions,upload_total_bytes,web_text_chars,"
    "generation_duration_sec,country,region,city,"
    "ext_input_tokens,ext_cached_input_tokens,ext_output_tokens,"
    "gen_input_tokens,gen_cached_input_tokens,gen_output_tokens,"
    "vrf_input_tokens,vrf_cached_input_tokens,vrf_output_tokens"
)

USAGE_DETAIL_COLUMNS_MIN = (
    "created_at,estimated_cost_usd,user_ip_id,generation_mode,material_source,"
    "generation_duration_sec"
)

USAGE_DETAIL_COLUMNS_LEGACY = (
    "created_at,estimated_cost_usd,user_ip_id,generation_mode,material_source,"
    "generation_duration_sec,"
    "ext_input_tokens,ext_cached_input_tokens,ext_output_tokens,"
    "gen_input_tokens,gen_cached_input_tokens,gen_output_tokens,"
    "vrf_input_tokens,vrf_cached_input_tokens,vrf_output_tokens"
)


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

    page_size = 1000

    def _pull_pages(select_cols: str) -> tuple[list[dict], str | None]:
        rows: list[dict] = []
        page = 0
        try:
            while True:
                q = (
                    supabase.table(TABLE_NAME)
                    .select(select_cols)
                    .gte("created_at", start_iso)
                    .lt("created_at", end_iso)
                )
                res = q.range(page * page_size, (page + 1) * page_size - 1).execute()
                batch = res.data or []
                rows.extend(batch)
                if len(batch) < page_size:
                    break
                page += 1
                if page > 1000:
                    break
        except Exception as ex:
            return [], str(ex)
        return rows, None

    all_rows, pull_err = _pull_pages("created_at, estimated_cost_usd, user_ip_id")
    err_l = (pull_err or "").lower()
    if pull_err and (
        "estimated_cost_usd" in pull_err
        or "42703" in pull_err
        or "user_ip_id" in pull_err
        or ("does not exist" in err_l and "column" in err_l)
    ):
        all_rows, pull_err = _pull_pages("created_at, user_ip_id")
    if pull_err:
        return [], pull_err

    by_day: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"generations": 0, "cost": 0.0, "visitors": set()}
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
        uid = r.get("user_ip_id")
        if uid:
            g["visitors"].add(str(uid))
        else:
            g["visitors"].add(f"anon:{hash(str(ts))}")

    out = [
        DailyRow(
            day=d,
            generations=b["generations"],
            total_cost_usd=float(b["cost"]),
            distinct_visitors=len(b["visitors"]),
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


def fetch_usage_detail_rows(
    start_utc: datetime,
    end_utc_exclusive: datetime,
) -> tuple[list[dict[str, Any]], str | None]:
    """Raw usage rows for advanced analytics (mode/source, latency, tokens)."""
    supabase = supabase_admin_client()
    if supabase is None:
        return [], "Supabase is not configured."

    start_iso = start_utc.astimezone(timezone.utc).isoformat()
    end_iso = end_utc_exclusive.astimezone(timezone.utc).isoformat()
    page_size = 1000

    def _pull(cols: str) -> tuple[list[dict[str, Any]], str | None]:
        rows: list[dict[str, Any]] = []
        page = 0
        try:
            while True:
                q = (
                    supabase.table(TABLE_NAME)
                    .select(cols)
                    .gte("created_at", start_iso)
                    .lt("created_at", end_iso)
                )
                res = q.range(page * page_size, (page + 1) * page_size - 1).execute()
                batch = res.data or []
                rows.extend(batch)
                if len(batch) < page_size:
                    break
                page += 1
                if page > 500:
                    break
        except Exception as ex:
            return [], str(ex)
        return rows, None

    all_rows, err = _pull(USAGE_DETAIL_COLUMNS)
    err_l = (err or "").lower()
    if err and ("42703" in err or "does not exist" in err_l):
        all_rows, err = _pull(USAGE_DETAIL_COLUMNS_LEGACY)
        err_l = ((err or "").lower() if err else "")
    if err and ("42703" in err or "does not exist" in err_l):
        all_rows, err = _pull(USAGE_DETAIL_COLUMNS_MIN)
    if err:
        return [], err
    return all_rows, None


def fetch_user_ip_rows(
    user_ip_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Map user_ip id -> {ip, country, region, city}."""
    ids = list(dict.fromkeys(str(i).strip() for i in user_ip_ids if i))
    if not ids:
        return {}, None
    supabase = supabase_admin_client()
    if supabase is None:
        return {}, "Supabase is not configured."

    out: dict[str, dict[str, Any]] = {}
    chunk = 80
    try:
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            res = (
                supabase.table("user_ip")
                .select("id,ip,country,region,city")
                .in_("id", part)
                .execute()
            )
            for row in res.data or []:
                uid = str(row.get("id") or "")
                if uid:
                    out[uid] = row
        return out, None
    except Exception as e:
        return {}, str(e)


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
