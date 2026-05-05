"""
Feedback form: inserts into Supabase ``user_feedback`` linked to ``user_ip`` via ``user_ip_id``.

Does not modify quiz answers or ``quiz_data``.
"""

import time

import streamlit as st

from bknd.quizzly_feedback_log import submit_user_feedback

_FEEDBACK_COOLDOWN_SEC = 45.0
_COOLDOWN_KEY = "_quizzly_feedback_cooldown_until"


def render_feedback_view(*, client_id: str, quiz_id: str) -> None:
    st.title("Feedback")
    st.caption(
        "Send bugs, ideas, or study-flow notes. Your quiz stays in this session — "
        "this page only saves what you submit below."
    )

    st.markdown(
        """
**Tips**

- Say what you were doing (upload vs links, rough question count).
- For bad quiz items, quote a few words from the question or name the topic.
"""
    )

    now = time.time()
    wait_left = float(st.session_state.get(_COOLDOWN_KEY, 0.0)) - now
    if wait_left > 0:
        st.info(f"You can send another message in **{int(wait_left) + 1}** seconds.")

    with st.form("quizzly_feedback_form", clear_on_submit=False):
        category = st.selectbox(
            "Type",
            ["bug", "feature", "quiz-quality", "other"],
            format_func=lambda x: {
                "bug": "Bug / something broke",
                "feature": "Feature idea",
                "quiz-quality": "Quiz quality / wrong answer",
                "other": "Other",
            }[x],
            index=0,
        )
        subject = st.text_input(
            "Short subject (optional)",
            max_chars=200,
            placeholder="e.g. Upload stuck on DOCX",
        )
        body = st.text_area(
            "Message",
            height=200,
            max_chars=4000,
            placeholder="What happened? What did you expect?",
        )
        submitted = st.form_submit_button("Send feedback", type="primary", width="stretch")

    if submitted:
        if time.time() < float(st.session_state.get(_COOLDOWN_KEY, 0.0)):
            st.warning("Please wait a moment before sending again.")
            return

        ok, err = submit_user_feedback(
            body=body,
            category=category,
            subject=subject or None,
        )
        if ok:
            st.success("Thanks — your feedback was saved.")
            st.session_state[_COOLDOWN_KEY] = time.time() + _FEEDBACK_COOLDOWN_SEC
        elif err:
            st.error(err)
