import streamlit as st

from fntnd.quizzly_state import load_error_history, save_error_history, set_query_params
from quizzly_config import ANSWER_LETTERS


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

