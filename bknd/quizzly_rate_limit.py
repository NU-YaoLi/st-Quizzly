"""
Daily per-client-IP quiz generation limits backed by Supabase (`user_ip` + `quiz_generation_usage`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import streamlit as st

from bknd.quizzly_usage_log import QuizGenerationUsageFields
from bknd.quizzly_user_ip import ensure_user_ip_geo_and_read, get_or_create_user_ip_id
from quizzly_config import DAILY_GENERATION_LIMIT, SUPABASE_URL

TABLE_NAME = "quiz_generation_usage"
_SESSION_USER_IP_ID = "_quizzly_user_ip_id"


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


def supabase_admin_client():
    """Configured Supabase client (service role), or None if secrets/url missing."""
    return _client()


def utc_day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def utc_next_midnight() -> datetime:
    return utc_day_start() + timedelta(days=1)


def format_time_until_next_utc_midnight() -> str:
    """Human-readable countdown until the next UTC midnight (rate limit reset)."""
    now = datetime.now(timezone.utc)
    secs = max(0, int((utc_next_midnight() - now).total_seconds()))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h} hour{'s' if h != 1 else ''} {m} minute{'s' if m != 1 else ''}"
    if m > 0:
        return f"{m} minute{'s' if m != 1 else ''} {s} second{'s' if s != 1 else ''}"
    return f"{s} second{'s' if s != 1 else ''}"


def count_generations_today(user_ip_id: str) -> tuple[int | None, str | None]:
    """Returns (count, error_message)."""
    supabase = _client()
    if supabase is None:
        return None, "Supabase is not configured (set SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets)."

    start = utc_day_start().isoformat()
    try:
        res = (
            supabase.table(TABLE_NAME)
            .select("id", count="exact", head=True)
            .eq("user_ip_id", user_ip_id)
            .gte("created_at", start)
            .execute()
        )
        n = getattr(res, "count", None)
        if n is None:
            return None, "Unexpected Supabase response (missing count)."
        return int(n), None
    except Exception as e:
        msg = f"{type(e).__name__}: {e!s}"
        # Legacy schema might still filter on ip_hash
        if "user_ip_id" in msg or "42703" in msg or (
            "does not exist" in msg.lower() and "column" in msg.lower()
        ):
            return None, msg
        return None, str(e)


def check_daily_generation_allowed() -> RateLimitResult:
    """Call before starting generation. Uses UTC calendar day."""
    if rate_limit_disabled():
        return RateLimitResult(True, used_today=0)

    url, key = _supabase_config()
    if not url or not key:
        return RateLimitResult(True, used_today=0)

    supabase = _client()
    if supabase is None:
        return RateLimitResult(True, used_today=0)

    ip = get_client_ip()
    uid, uerr = get_or_create_user_ip_id(supabase, ip)
    if uid:
        st.session_state[_SESSION_USER_IP_ID] = uid
    if not uid:
        return RateLimitResult(
            False,
            message=(
                "Could not verify the daily usage limit (user_ip). Please try again in a moment. "
                f"({uerr or 'unknown error'})"
            ),
            used_today=None,
        )

    used, err = count_generations_today(uid)
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
        remaining = format_time_until_next_utc_midnight()
        return RateLimitResult(
            False,
            message=(
                f"Daily quiz generation limit reached ({DAILY_GENERATION_LIMIT} per day, UTC). "
                f"Try again in {remaining} (resets at midnight UTC)."
            ),
            used_today=used,
        )

    return RateLimitResult(True, used_today=used)


def record_successful_generation(
    user_ip_id: str | None,
    *,
    usage: QuizGenerationUsageFields | None = None,
) -> str | None:
    """Insert one usage row. Returns error string or None on success."""
    if rate_limit_disabled():
        return None

    supabase = _client()
    if supabase is None:
        return None

    uid = user_ip_id or st.session_state.get(_SESSION_USER_IP_ID)
    if not uid:
        ip = get_client_ip()
        uid, _ = get_or_create_user_ip_id(supabase, ip)
    if not uid:
        return "Could not resolve user_ip_id for usage log."

    row_full: dict = (
        usage.as_insert_dict(uid) if usage is not None else {"user_ip_id": uid, "estimated_cost_usd": None}
    )
    gc, gr, gct = ensure_user_ip_geo_and_read(supabase, uid)
    row_full["country"] = gc
    row_full["region"] = gr
    row_full["city"] = gct

    row_min: dict = {"user_ip_id": uid}
    try:
        supabase.table(TABLE_NAME).insert(row_full).execute()
        return None
    except Exception as e:
        msg = f"{type(e).__name__}: {e!s}"
        if "42703" in msg or ("does not exist" in msg.lower() and "column" in msg.lower()):
            try:
                slim = {k: v for k, v in row_full.items() if k not in ("country", "region", "city")}
                supabase.table(TABLE_NAME).insert(slim).execute()
                return None
            except Exception as e2:
                msg2 = f"{type(e2).__name__}: {e2!s}"
                if "42703" in msg2 or ("does not exist" in msg2.lower() and "column" in msg2.lower()):
                    try:
                        supabase.table(TABLE_NAME).insert(row_min).execute()
                        return None
                    except Exception as e3:
                        return str(e3)
                return str(e2)
        return str(e)
