"""
Standalone feedback landing page.

Read-only onboarding-style view (like How to use): does not mutate quiz session
state. Navigation back to the quiz clears ``view`` from the URL only.
"""

import streamlit as st


def render_feedback_view() -> None:
    st.title("Feedback")
    st.caption("Share bugs, ideas, or study-flow friction — leaving this page does not change your quiz.")

    st.markdown(
        """
### How to reach us

- Describe **what you were doing** (e.g. upload vs links, approximate question count).
- If something broke, note **your browser** and whether a refresh helped.
- For wrong or confusing quiz items, mention the **topic** or paste a short quote from the question.

Your **in-progress quiz and answers stay in this browser session** when you open or close this page, as long as you use **← Back to Quiz** from the sidebar.
"""
    )
