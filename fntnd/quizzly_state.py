import hashlib
import json
import os
import tempfile
import time
import uuid

import streamlit as st


STATE_DIR = os.path.join(tempfile.gettempdir(), "quizzly_state")


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


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
    set_query_params(client=client_id, quiz=quiz_id)
    return client_id


def _state_path(client_id: str, quiz_id: str) -> str:
    safe_client = "".join(ch for ch in client_id if ch.isalnum())[:64] or "client"
    safe_quiz = "".join(ch for ch in quiz_id if ch.isalnum())[:64] or "quiz"
    return os.path.join(STATE_DIR, f"{safe_client}_{safe_quiz}.json")


def _history_state_path(client_id: str) -> str:
    safe_client = "".join(ch for ch in client_id if ch.isalnum())[:64] or "client"
    return os.path.join(STATE_DIR, f"{safe_client}__error_history.json")


def load_error_history(client_id: str) -> list[dict]:
    if not client_id:
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


def load_state_from_disk(client_id: str, quiz_id: str) -> dict | None:
    if not client_id or not quiz_id:
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


def load_state_cached(client_id: str, quiz_id: str) -> dict | None:
    return load_state_from_disk(client_id, quiz_id)


def persist_quiz_state(
    client_id: str,
    quiz_id: str,
    *,
    quiz_data: dict | None,
    verification_report: dict | None,
    error_notebook: list[dict],
    answers: dict[str, int | None],
) -> None:
    payload = {
        "saved_at": time.time(),
        "quiz_data": quiz_data,
        "verification_report": verification_report,
        "error_notebook": error_notebook,
        "answers": answers,
    }
    save_state_to_disk(client_id, quiz_id, payload)
    try:
        load_state_cached.clear()  # type: ignore[attr-defined]
    except Exception:
        pass


def init_session_state() -> None:
    st.session_state.setdefault("_web_text", "")
    st.session_state.setdefault("_persisted_answers", {})
    st.session_state.setdefault("_last_autosave_hash", None)
    st.session_state.setdefault("_quiz_submitted", False)
    st.session_state.setdefault("_last_graded_hash", None)
    st.session_state.setdefault("_show_score_dialog", False)
    st.session_state.setdefault("_last_score", None)
    st.session_state.setdefault("_error_notebook_current", [])
    st.session_state.setdefault("_error_notebook_history", [])
    st.session_state.setdefault("_error_history_loaded", False)

    st.session_state.setdefault("quiz_data", None)
    st.session_state.setdefault("verification_report", None)
    st.session_state.setdefault("generation_time", None)
    st.session_state.setdefault("current_paths", [])
    st.session_state.setdefault("cleanup_paths", [])
    st.session_state.setdefault("workflow_status_label", None)
    st.session_state.setdefault("workflow_status_lines", [])
    st.session_state.setdefault("web_url_slot_count", 1)

