"""
Right-rail "current quiz mistakes" panel.

Shows each missed question for the in-progress quiz (live, before submission)
and lets the user save those mistakes into the all-time error notebook via the
``persist_cb`` callback supplied by ``fntnd.quizzly_ftnd``.
"""

import streamlit as st

from bknd.quizzly_text import clean_option_text
from quizzly_config import ANSWER_LETTERS


def render_current_quiz_mistakes(*, client_id: str, quiz_id: str, persist_cb) -> None:
    """
    Render the right-rail "current quiz mistakes" notebook.

    persist_cb: callable(client_id, quiz_id, error_notebook_current, answers)
    """
    with st.container(
        border=True,
        height="stretch",
        width="stretch",
        key="quizzly_error_notebook",
    ):
        st.subheader("Mistakes Review")

        st.markdown("Incorrectly answered questions will be added to your error notebook.")
        st.divider()

        notebook = st.session_state.get("_error_notebook_current") or []
        if not notebook:
            st.info("No mistakes logged yet. Great job!")
            return

        for idx, error in enumerate(notebook):
            with st.expander(f"Review Question {idx + 1}"):
                q_text = error.get("question") or "Question"
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

        st.divider()
        if st.button("Clear Mistakes Review", width="stretch"):
            st.session_state["_error_notebook_current"] = []
            persisted_answers = st.session_state.get("_persisted_answers") or {}
            persist_cb(
                client_id=client_id,
                quiz_id=quiz_id,
                error_notebook_current=[],
                answers=persisted_answers,
            )
            st.rerun()

