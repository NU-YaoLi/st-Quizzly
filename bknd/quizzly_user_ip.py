"""
Resolve client IP to a `user_ip` row (geo + FK for `quiz_generation_usage`).

Uses ip-api.com (HTTP, free) first, then ipwho.is (HTTPS, free) if the first call fails.
Some deployment environments block plain HTTP; HTTPS fallback fixes missing geo.
"""

from __future__ import annotations

from typing import Any

import requests

USER_IP_TABLE = "user_ip"


def _lookup_ip_api_com(ip: str) -> tuple[str | None, str | None, str | None]:
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


def _lookup_ipwho_is(ip: str) -> tuple[str | None, str | None, str | None]:
    """HTTPS fallback (works when HTTP to ip-api is blocked)."""
    try:
        r = requests.get(f"https://ipwho.is/{ip}", timeout=4)
        r.raise_for_status()
        j = r.json()
        if not j.get("success"):
            return None, None, None
        return j.get("country"), j.get("region"), j.get("city")
    except Exception:
        return None, None, None


def lookup_ip_geo(ip: str) -> tuple[str | None, str | None, str | None]:
    """Return (country, region/province, city) for a public IP."""
    ip = (ip or "").strip()
    if not ip or ip == "unknown":
        return None, None, None
    if ip in ("127.0.0.1", "::1"):
        return None, None, None

    c, r, ct = _lookup_ip_api_com(ip)
    if c or r or ct:
        return c, r, ct
    return _lookup_ipwho_is(ip)


def _normalize_ip(ip: str) -> str:
    ip = (ip or "").strip()
    if len(ip) > 128:
        ip = ip[:128]
    return ip


def lookup_user_ip_id_only(supabase: Any, ip: str) -> str | None:
    """
    Return existing ``user_ip.id`` for this normalized IP string.
    Does **not** insert or refresh geo — used for rate-limit checks so failed/aborted
    generations do not create orphan ``user_ip`` rows.
    """
    if supabase is None:
        return None
    ip_key = _normalize_ip(ip)
    if not ip_key or ip_key == "unknown":
        ip_key = "unknown"
    try:
        res = (
            supabase.table(USER_IP_TABLE)
            .select("id")
            .eq("ip", ip_key)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            return str(rows[0]["id"])
    except Exception:
        pass
    return None


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
        res = (
            supabase.table(USER_IP_TABLE)
            .select("id,country,region,city")
            .eq("ip", ip_key)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            row = rows[0]
            rid = str(row["id"])
            # Existing rows were often created with NULL geo (HTTP blocked / lookup failed).
            if ip_key != "unknown" and not row.get("country"):
                c, rg, ct = lookup_ip_geo(ip_key)
                if c or rg or ct:
                    try:
                        supabase.table(USER_IP_TABLE).update(
                            {"country": c, "region": rg, "city": ct}
                        ).eq("id", rid).execute()
                    except Exception:
                        pass
            return rid, None
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


def ensure_user_ip_geo_and_read(supabase: Any, user_ip_id: str) -> tuple[str | None, str | None, str | None]:
    """
    If `user_ip` has no country yet, resolve geo from `ip` and UPDATE the row.
    Returns (country, region, city) for logging on the usage row (snapshot).
    """
    if supabase is None or not user_ip_id:
        return None, None, None
    try:
        res = (
            supabase.table(USER_IP_TABLE)
            .select("ip,country,region,city")
            .eq("id", user_ip_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None, None, None
        row = rows[0]
        ip_val = (row.get("ip") or "").strip()
        country, region, city = row.get("country"), row.get("region"), row.get("city")
        if ip_val and ip_val != "unknown" and not country:
            c, rg, ct = lookup_ip_geo(ip_val)
            if c or rg or ct:
                try:
                    supabase.table(USER_IP_TABLE).update(
                        {"country": c, "region": rg, "city": ct}
                    ).eq("id", user_ip_id).execute()
                    country, region, city = c, rg, ct
                except Exception:
                    pass
        return country, region, city
    except Exception:
        return None, None, None
