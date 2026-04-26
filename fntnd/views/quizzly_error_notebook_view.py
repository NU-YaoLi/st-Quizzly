import streamlit as st

from fntnd.quizzly_state import load_error_history, save_error_history, set_query_params
from quizzly_config import ANSWER_LETTERS


def _clean_option_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    t = s.strip()
    for _ in range(3):
        if len(t) >= 3 and t[0].upper() in "ABCD" and t[1] == ")" and t[2] == " ":
            t = t[3:].lstrip()
        else:
            break
    return t


def render_error_notebook_view(*, client_id: str, quiz_id: str) -> None:
    with st.sidebar:
        if st.button("← Back to Quiz", use_container_width=True):
            set_query_params(client=client_id, quiz=quiz_id)
            st.rerun()

    st.title("Error Notebook")
    st.caption("All-time record of mistakes across quizzes for this client session.")

    history = st.session_state.get("_error_notebook_history") or load_error_history(client_id)
    st.session_state["_error_notebook_history"] = history

    col_a, col_b = st.columns([1, 1], gap="small")
    with col_a:
        st.metric("Total mistakes saved", len(history))
    with col_b:
        if st.button("Clear ALL history", type="secondary", use_container_width=True):
            save_error_history(client_id, [])
            st.session_state["_error_notebook_history"] = []
            st.success("History cleared.")
            st.rerun()

    st.divider()
    if not history:
        st.info("No mistakes saved yet.")
        return

    for idx, error in enumerate(reversed(history)):
        q_text = error.get("question") or "Question"
        with st.expander(f"{len(history) - idx}. {q_text[:80]}"):
            st.markdown(f"**Q:** {q_text}")

            options = error.get("options") or []
            if options:
                for i, opt in enumerate(options):
                    letter = ANSWER_LETTERS[i] if i < len(ANSWER_LETTERS) else str(i)
                    st.markdown(f"**{letter})** {_clean_option_text(opt)}")

            user_letter = error.get("user_answer_letter")
            correct_letter = error.get("correct_option")
            if user_letter is not None or correct_letter is not None:
                st.markdown(
                    f"❌ **Your answer:** {'' if user_letter is None else f'{user_letter})'}"
                    f"     ✅ **Correct answer:** {'' if correct_letter is None else f'{correct_letter})'}"
                )

            expl = error.get("explanation")
            if expl:
                st.markdown(f"💡 **Explanation:**\n\n{expl}")

