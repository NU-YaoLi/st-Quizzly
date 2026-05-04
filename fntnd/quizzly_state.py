"""
Session state, disk persistence, and signed query-parameter helpers.

- ``init_session_state``: defaults for every key the UI reads.
- ``get_or_create_client_id`` + ``sign_client`` / ``sign_state``: HMAC-signed
  ids that survive query-param round-trips.
- ``load_state_from_disk`` / ``save_state_to_disk`` / ``persist_quiz_state``:
  best-effort on-disk continuity for an in-progress quiz across reruns.
- ``load_error_history`` / ``save_error_history``: all-time mistake notebook
  persisted per ``client_id``.
- ``get_query_params`` / ``set_query_params``: thin wrappers that smooth over
  Streamlit API differences across versions.
"""

import hashlib
import json
import os
import tempfile
import time
import uuid
import hmac

import streamlit as st


STATE_DIR = os.path.join(tempfile.gettempdir(), "quizzly_state")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _signing_secret() -> str | None:
    """Read the dedicated HMAC secret used for client/quiz URL signatures.

    We deliberately do NOT fall back to ``OPENAI_API_KEY`` — rotating that key
    would silently invalidate every previously-signed quiz URL. Set
    ``STATE_SIGNING_SECRET`` in Streamlit secrets (or env) to enable signing.
    When unset, ``sign_state``/``sign_client`` return ``""`` and verification
    becomes a no-op.
    """
    try:
        s = (st.secrets.get("STATE_SIGNING_SECRET") or "").strip()
        if s:
            return s
    except Exception:
        pass
    s = (os.environ.get("STATE_SIGNING_SECRET") or "").strip()
    return s or None


def sign_state(client_id: str, quiz_id: str) -> str:
    """
    HMAC signature to prevent query-param guessing from loading other users' state.
    Returns a short hex string suitable for URLs.
    """
    secret = _signing_secret()
    if not secret:
        return ""
    msg = f"{client_id}:{quiz_id}".encode("utf-8", errors="ignore")
    sig = hmac.new(secret.encode("utf-8", errors="ignore"), msg, hashlib.sha256).hexdigest()
    return sig[:24]


def sign_client(client_id: str) -> str:
    secret = _signing_secret()
    if not secret:
        return ""
    msg = f"client:{client_id}".encode("utf-8", errors="ignore")
    sig = hmac.new(secret.encode("utf-8", errors="ignore"), msg, hashlib.sha256).hexdigest()
    return sig[:24]


def get_query_params() -> dict[str, str]:
    # streamlit >=1.30: st.query_params behaves like a mutable mapping.
    try:
        qp = dict(st.query_params)  # type: ignore[attr-defined]
        return {k: (v[0] if isinstance(v, list) else str(v)) for k, v in qp.items()}
    except Exception:
        # streamlit <1.30 compatibility
        try:
            qp = st.experimental_get_query_params()
            return {k: (v[0] if isinstance(v, list) and v else "") for k, v in qp.items()}
        except Exception:
            return {}


def set_query_params(**kwargs: str) -> None:
    cleaned = {k: v for k, v in kwargs.items() if v}
    try:
        st.query_params.clear()  # type: ignore[attr-defined]
        st.query_params.update(cleaned)  # type: ignore[attr-defined]
    except Exception:
        st.experimental_set_query_params(**cleaned)


def get_or_create_client_id() -> str:
    qp = get_query_params()
    client_id = (qp.get("client") or "").strip()
    if client_id:
        return client_id
    client_id = uuid.uuid4().hex
    quiz_id = (qp.get("quiz") or "").strip()
    set_query_params(client=client_id, quiz=quiz_id, csig=sign_client(client_id), sig=sign_state(client_id, quiz_id))
    return client_id


def _state_path(client_id: str, quiz_id: str) -> str:
    safe_client = "".join(ch for ch in client_id if ch.isalnum())[:64] or "client"
    safe_quiz = "".join(ch for ch in quiz_id if ch.isalnum())[:64] or "quiz"
    return os.path.join(STATE_DIR, f"{safe_client}_{safe_quiz}.json")


def _history_state_path(client_id: str) -> str:
    safe_client = "".join(ch for ch in client_id if ch.isalnum())[:64] or "client"
    return os.path.join(STATE_DIR, f"{safe_client}__error_history.json")


def load_error_history(client_id: str, *, csig: str | None = None) -> list[dict]:
    if not client_id:
        return []
    expected = sign_client(client_id)
    if expected and (csig or "") != expected:
        return []
    p = _history_state_path(client_id)
    try:
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []
    except Exception:
        return []


def save_error_history(client_id: str, history: list[dict]) -> None:
    if not client_id:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = _history_state_path(client_id)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(history, f)
    except Exception:
        pass


def load_state_from_disk(client_id: str, quiz_id: str, *, sig: str | None = None) -> dict | None:
    if not client_id or not quiz_id:
        return None
    expected = sign_state(client_id, quiz_id)
    if expected and (sig or "") != expected:
        return None
    p = _state_path(client_id, quiz_id)
    try:
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state_to_disk(client_id: str, quiz_id: str, payload: dict) -> None:
    if not client_id or not quiz_id:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = _state_path(client_id, quiz_id)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def persist_quiz_state(
    client_id: str,
    quiz_id: str,
    *,
    quiz_data: dict | None,
    verification_report: dict | None,
    error_notebook: list[dict],
    answers: dict[str, int | None],
    quiz_submitted: bool | None = None,
    current_quiz_score: tuple[int, int] | None = None,
    workflow_status_label: str | None = None,
    workflow_status_lines: list[str] | None = None,
) -> None:
    payload = {
        "saved_at": time.time(),
        "quiz_data": quiz_data,
        "verification_report": verification_report,
        "error_notebook": error_notebook,
        "answers": answers,
        "quiz_submitted": bool(quiz_submitted) if quiz_submitted is not None else None,
        "current_quiz_score": list(current_quiz_score) if current_quiz_score is not None else None,
        "workflow_status_label": workflow_status_label,
        "workflow_status_lines": workflow_status_lines,
    }
    save_state_to_disk(client_id, quiz_id, payload)


def init_session_state() -> None:
    st.session_state.setdefault("_web_text", "")
    st.session_state.setdefault("_persisted_answers", {})
    st.session_state.setdefault("_last_autosave_hash", None)
    st.session_state.setdefault("_quiz_submitted", False)
    st.session_state.setdefault("_last_graded_hash", None)
    st.session_state.setdefault("_current_quiz_score", None)  # tuple[int, int] -> (correct, total)
    st.session_state.setdefault("_error_notebook_current", [])
    st.session_state.setdefault("_error_notebook_history", [])
    st.session_state.setdefault("_error_history_loaded", False)

    st.session_state.setdefault("quiz_data", None)
    st.session_state.setdefault("verification_report", None)
    st.session_state.setdefault("current_paths", [])
    st.session_state.setdefault("cleanup_paths", [])
    st.session_state.setdefault("workflow_status_label", None)
    st.session_state.setdefault("workflow_status_lines", [])
    st.session_state.setdefault("web_url_slot_count", 1)

