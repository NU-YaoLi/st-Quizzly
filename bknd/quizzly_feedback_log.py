"""
Persist visitor feedback in Supabase ``user_feedback``.

Rows reference ``user_ip`` by id, so you can join with:

    select f.*, u.id as user_ip_id, u.country
    from public.user_feedback f
    join public.user_ip u on u.id = f.user_ip_id;
"""

import math
import re

from bknd.quizzly_rate_limit import get_client_ip, supabase_admin_client
from bknd.quizzly_user_ip import get_or_create_user_ip_id

FEEDBACK_TABLE = "user_feedback"
_MAX_BODY = 4000
_MAX_SUBJECT = 200


def _json_safe_row(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        else:
            out[k] = v
    return out


def _clip(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    t = s.strip()
    if not t:
        return None
    return t[:n] if len(t) > n else t


def _slug_category(raw: str | None) -> str | None:
    if not raw:
        return None
    t = raw.strip().lower()
    if not t:
        return None
    t = re.sub(r"[^a-z0-9_-]+", "-", t).strip("-")
    return (t[:64] or None)


def submit_user_feedback(
    *,
    body: str,
    category: str | None = None,
    subject: str | None = None,
) -> tuple[bool, str | None]:
    """
    Ensure ``user_ip`` exists for the current client IP, then insert one feedback row.

    Returns ``(True, None)`` on success, or ``(False, error_message)``.
    """
    supabase = supabase_admin_client()
    if supabase is None:
        return False, (
            "Feedback could not be saved: Supabase is not configured "
            "(set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in secrets)."
        )

    text = (body or "").strip()
    if not text:
        return False, "Please enter a message before submitting."
    if len(text) > _MAX_BODY:
        return False, f"Message is too long (max {_MAX_BODY} characters)."

    ip_hint = get_client_ip()
    uid, uerr = get_or_create_user_ip_id(supabase, ip_hint)
    if uerr or not uid:
        return False, f"Could not resolve visitor identity: {uerr or 'unknown error'}"

    row = {
        "user_ip_id": uid,
        "category": _slug_category(category),
        "subject": _clip(subject, _MAX_SUBJECT),
        "body": text,
    }

    try:
        res = supabase.table(FEEDBACK_TABLE).insert(_json_safe_row(row)).execute()
        if not (getattr(res, "data", None) or []):
            return False, "Insert returned no row — check that table user_feedback exists and RLS allows the service role."
        return True, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e!s}"
        if "user_feedback" in msg.lower() or "42P01" in msg:
            return (
                False,
                "Database table `user_feedback` is missing — run the SQL in `quizzly_sql.txt` in Supabase.",
            )
        if "23503" in msg or "foreign key" in msg.lower():
            return False, "Could not link feedback to visitor IP (user_ip row missing)."
        return False, msg
