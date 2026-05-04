"""
All-time error notebook page.

Renders the full history of missed questions across every quiz this client
session has taken (loaded via ``fntnd.quizzly_state.load_error_history``).
Includes a clear-history action that writes the empty list back to disk.
"""

import streamlit as st

from fntnd.quizzly_state import load_error_history, save_error_history
from bknd.quizzly_text import clean_option_text
from quizzly_config import ANSWER_LETTERS


def render_error_notebook_view(*, client_id: str, quiz_id: str) -> None:
    st.title("Error Notebook")
    st.caption("All-time record of mistakes across quizzes for this client session.")

    history = st.session_state.get("_error_notebook_history") or load_error_history(client_id)
    st.session_state["_error_notebook_history"] = history

    col_a, col_b = st.columns([1, 1], gap="small")
    with col_a:
        st.metric("Total mistakes saved", len(history))
    with col_b:
        if st.button("Clear ALL history", type="secondary", width="stretch"):
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
                    st.markdown(f"**{letter})** {clean_option_text(opt)}")

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

