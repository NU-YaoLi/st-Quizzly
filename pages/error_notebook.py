import json
import os
import tempfile

import streamlit as st


STATE_DIR = os.path.join(tempfile.gettempdir(), "quizzly_state")


def _get_query_params() -> dict[str, str]:
    try:
        qp = dict(st.query_params)  # type: ignore[attr-defined]
        return {k: (v[0] if isinstance(v, list) else str(v)) for k, v in qp.items()}
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            return {k: (v[0] if isinstance(v, list) and v else "") for k, v in qp.items()}
        except Exception:
            return {}


def _history_state_path(client_id: str) -> str:
    safe_client = "".join(ch for ch in (client_id or "") if ch.isalnum())[:64] or "client"
    return os.path.join(STATE_DIR, f"{safe_client}__error_history.json")


def _load_error_history(client_id: str) -> list[dict]:
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


def _save_error_history(client_id: str, history: list[dict]) -> None:
    if not client_id:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = _history_state_path(client_id)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(history, f)
    except Exception:
        pass


st.set_page_config(page_title="Error Notebook", page_icon="📒", layout="wide")

qp = _get_query_params()
client_id = (qp.get("client") or "").strip()

st.title("Error Notebook")
st.caption("All-time record of mistakes across quizzes for this client session.")

history = _load_error_history(client_id)

col_a, col_b = st.columns([1, 1], gap="small")
with col_a:
    st.metric("Total mistakes saved", len(history))
with col_b:
    if st.button("Back to Quiz", use_container_width=True):
        try:
            st.switch_page("quizzly_main.py")
        except Exception:
            st.switch_page("quizzly_main.py")

st.divider()

if not history:
    st.info("No mistakes saved yet.")
else:
    for idx, error in enumerate(reversed(history)):
        q_text = error.get("question", "Question")
        with st.expander(f"{len(history) - idx}. {q_text[:80]}"):
            st.markdown(f"**Q:** {q_text}")
            user_wrong = error.get("user_wrong")
            if user_wrong is not None:
                st.markdown(f"❌ *You answered:* {user_wrong}")
            expl = error.get("explanation")
            if expl:
                st.markdown(f"💡 **Correction:**\n\n{expl}")

    st.divider()
    if st.button("Clear ALL history", type="secondary", use_container_width=True):
        _save_error_history(client_id, [])
        st.success("History cleared.")
        st.rerun()

