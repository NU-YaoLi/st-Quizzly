import streamlit as st

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
        st.header("Current Quiz Mistakes")
        st.markdown("Review your mistakes to reinforce learning.")
        st.divider()

        notebook = st.session_state.get("_error_notebook_current") or []
        if not notebook:
            st.info("No errors logged yet. Great job!")
            return

        for idx, error in enumerate(notebook):
            with st.expander(f"Review Question {idx + 1}"):
                q_text = error.get("question") or "Question"
                st.markdown(f"**Q:** {q_text}")

                options = error.get("options") or []
                if options:
                    st.markdown("**Options:**")
                    for i, opt in enumerate(options):
                        letter = ANSWER_LETTERS[i] if i < len(ANSWER_LETTERS) else str(i)
                        st.markdown(f"- **{letter})** {opt}")

                user_letter = error.get("user_answer_letter")
                user_text = error.get("user_answer_text")
                if user_letter is not None:
                    if user_text:
                        st.markdown(f"❌ **Your answer:** {user_letter}) {user_text}")
                    else:
                        st.markdown(f"❌ **Your answer:** {user_letter}")

                correct_letter = error.get("correct_option")
                correct_text = error.get("correct_answer_text")
                if correct_letter is not None:
                    if correct_text:
                        st.markdown(f"✅ **Correct answer:** {correct_letter}) {correct_text}")
                    else:
                        st.markdown(f"✅ **Correct answer:** {correct_letter}")

                expl = error.get("explanation")
                if expl:
                    st.markdown(f"💡 **Explanation:**\n\n{expl}")

        st.divider()
        if st.button("Clear Notebook", use_container_width=True):
            st.session_state["_error_notebook_current"] = []
            persisted_answers = st.session_state.get("_persisted_answers") or {}
            persist_cb(
                client_id=client_id,
                quiz_id=quiz_id,
                error_notebook_current=[],
                answers=persisted_answers,
            )
            st.rerun()

