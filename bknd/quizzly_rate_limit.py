"""
Daily per-IP quiz generation limits backed by Supabase (Postgres).

Stores only a salted hash of the client IP and a timestamp — no other fields.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
import streamlit as st

from quizzly_config import DAILY_GENERATION_LIMIT, SUPABASE_URL

TABLE_NAME = "quiz_generation_usage"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    message: str = ""
    used_today: int | None = None


def _secret(key: str) -> str | None:
    try:
        v = st.secrets.get(key)
        return str(v).strip() if v else None
    except Exception:
        pass
    v = os.environ.get(key)
    return str(v).strip() if v else None


def rate_limit_disabled() -> bool:
    if os.environ.get("RATE_LIMIT_DISABLED", "").strip() in ("1", "true", "yes"):
        return True
    v = _secret("RATE_LIMIT_DISABLED")
    return v is not None and v.strip().lower() in ("1", "true", "yes")


def _ip_salt() -> str:
    s = _secret("RATE_LIMIT_IP_SALT")
    if s:
        return s
    k = _secret("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not k:
        return "quizzly-default-salt-not-configured"
    return hashlib.sha256((k + "|quizzly-ip").encode()).hexdigest()


def hash_client_ip(ip: str) -> str:
    return hashlib.sha256(f"{_ip_salt()}:{ip}".encode()).hexdigest()


def get_client_ip() -> str:
    """
    Best-effort client IP from Streamlit request headers (works on Streamlit Cloud
    via X-Forwarded-For). Local `streamlit run` often yields 'unknown'.
    """
    try:
        ctx = getattr(st, "context", None)
        if ctx is None:
            return "unknown"
        headers = getattr(ctx, "headers", None)
        if not headers:
            return "unknown"
        xff = (headers.get("X-Forwarded-For") or headers.get("x-forwarded-for") or "").strip()
        if xff:
            return xff.split(",")[0].strip() or "unknown"
        rip = (headers.get("X-Real-IP") or headers.get("x-real-ip") or "").strip()
        if rip:
            return rip
    except Exception:
        pass
    return "unknown"


def _supabase_config() -> tuple[str | None, str | None]:
    url = (_secret("SUPABASE_URL") or SUPABASE_URL or "").strip().rstrip("/") or None
    key = _secret("SUPABASE_SERVICE_ROLE_KEY")
    return url, key


def _client():
    from supabase import create_client

    url, key = _supabase_config()
    if not url or not key:
        return None
    return create_client(url, key)


def utc_day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def count_generations_today(ip_hash: str) -> tuple[int | None, str | None]:
    """
    Returns (count, error_message). count is None if the query failed.
    """
    supabase = _client()
    if supabase is None:
        return None, "Supabase is not configured (set SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets)."

    start = utc_day_start().isoformat()
    try:
        # head=True: count via Content-Range only; no row bodies transferred.
        res = (
            supabase.table(TABLE_NAME)
            .select("id", count="exact", head=True)
            .eq("ip_hash", ip_hash)
            .gte("created_at", start)
            .execute()
        )
        n = getattr(res, "count", None)
        if n is None:
            return None, "Unexpected Supabase response (missing count)."
        return int(n), None
    except Exception as e:
        return None, str(e)


def check_daily_generation_allowed() -> RateLimitResult:
    """
    Call before starting generation. Uses UTC calendar day.
    """
    if rate_limit_disabled():
        return RateLimitResult(True, used_today=0)

    url, key = _supabase_config()
    if not url or not key:
        return RateLimitResult(True, used_today=0)

    ip = get_client_ip()
    ip_hash = hash_client_ip(ip)
    used, err = count_generations_today(ip_hash)
    if err is not None:
        return RateLimitResult(
            False,
            message=(
                "Could not verify the daily usage limit. Please try again in a moment. "
                f"({err})"
            ),
            used_today=None,
        )

    if used is not None and used >= DAILY_GENERATION_LIMIT:
        return RateLimitResult(
            False,
            message=(
                f"Daily quiz generation limit reached ({DAILY_GENERATION_LIMIT} per day, UTC). "
                "Try again after midnight UTC."
            ),
            used_today=used,
        )

    return RateLimitResult(True, used_today=used)


def record_successful_generation(ip_hash: str) -> str | None:
    """Insert one usage row. Returns error string or None on success."""
    if rate_limit_disabled():
        return None

    supabase = _client()
    if supabase is None:
        return None

    try:
        supabase.table(TABLE_NAME).insert({"ip_hash": ip_hash}).execute()
    except Exception as e:
        return str(e)
    return None
