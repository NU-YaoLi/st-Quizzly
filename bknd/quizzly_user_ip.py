"""
Resolve client IP to a `user_ip` row (geo + FK for `quiz_generation_usage`).

Uses ip-api.com free HTTP API (no API key; ~45 req/min). Failures leave geo fields NULL.
"""

from __future__ import annotations

from typing import Any

import requests

USER_IP_TABLE = "user_ip"


def lookup_ip_geo(ip: str) -> tuple[str | None, str | None, str | None]:
    """Return (country, region/province, city) for a public IP."""
    ip = (ip or "").strip()
    if not ip or ip == "unknown":
        return None, None, None
    if ip in ("127.0.0.1", "::1"):
        return None, None, None

    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,regionName,city,message"},
            timeout=4,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "success":
            return None, None, None
        return j.get("country"), j.get("regionName"), j.get("city")
    except Exception:
        return None, None, None


def _normalize_ip(ip: str) -> str:
    ip = (ip or "").strip()
    if len(ip) > 128:
        ip = ip[:128]
    return ip


def get_or_create_user_ip_id(supabase: Any, ip: str) -> tuple[str | None, str | None]:
    """
    Return (user_ip row uuid as str, error_message).
    Inserts a row with best-effort geo; reuses existing row for the same ip.
    """
    if supabase is None:
        return None, "Supabase client missing"

    ip_key = _normalize_ip(ip)
    if not ip_key or ip_key == "unknown":
        ip_key = "unknown"

    try:
        res = supabase.table(USER_IP_TABLE).select("id").eq("ip", ip_key).limit(1).execute()
        rows = res.data or []
        if rows:
            return str(rows[0]["id"]), None
    except Exception as e:
        return None, str(e)

    country, region, city = lookup_ip_geo(ip_key) if ip_key != "unknown" else (None, None, None)

    try:
        ins = (
            supabase.table(USER_IP_TABLE)
            .insert(
                {
                    "ip": ip_key,
                    "country": country,
                    "region": region,
                    "city": city,
                }
            )
            .execute()
        )
        data = ins.data
        if isinstance(data, list) and data:
            return str(data[0].get("id")), None
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"]), None
    except Exception as e:
        err = f"{type(e).__name__}: {e!s}"
        if "23505" in err or "duplicate" in err.lower() or "unique" in err.lower():
            try:
                res = supabase.table(USER_IP_TABLE).select("id").eq("ip", ip_key).limit(1).execute()
                rows = res.data or []
                if rows:
                    return str(rows[0]["id"]), None
            except Exception as e2:
                return None, str(e2)
        return None, err

    try:
        res = supabase.table(USER_IP_TABLE).select("id").eq("ip", ip_key).limit(1).execute()
        rows = res.data or []
        if rows:
            return str(rows[0]["id"]), None
    except Exception:
        pass
    return None, "user_ip insert returned no id"
