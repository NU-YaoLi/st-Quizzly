import streamlit as st

from quizzly_config import MAX_QUESTIONS_CAP, MAX_WEB_URL_SLOTS, MIN_QUESTIONS


def render_how_to_use_view() -> None:
    st.title("How to Use Quizzly")
    st.caption("A quick walkthrough, plus rules and limitations.")

    st.divider()

    st.markdown(
        """
### 1) Choose your material source (one mode at a time)

- **Upload files** (best for slides/notes)
  - Supported: **PDF, DOCX, PPTX, TXT, PNG, JPG/JPEG**
  - You can upload up to **5** files at once.
- **Website links** (best for articles)
  - You can add up to **5** URLs.
  - If a page is hard to extract (search results, heavily scripted pages, paywalls), it may fail.

### 2) Load materials

- After you add files or URLs, Quizzly will detect sources and auto-calculate a safe maximum question count.

### 3) Pick quiz settings

- **Number of questions**: choose between **{min_q}** and the auto-calculated maximum (hard cap **{cap}**).
- **Scenario-based vs Conceptual**: use the slider to control how many questions are scenario-style.

### 4) Generate & verify

Click **Generate & Verify Quiz**.

Quizzly runs a pipeline:

- **Concept extraction** (find key concepts first)
- **Quiz generation** (MCQs)
- **Output guard + verification** (shape checks + quality scoring)

### 5) Take the quiz + review mistakes

- Click **Submit Answers** to grade your quiz.
- Incorrect questions are added to:
  - **Mistakes Review** (right rail, current quiz)
  - **Error Notebook** (all-time history for this client session)
        """.format(min_q=MIN_QUESTIONS, cap=MAX_QUESTIONS_CAP)
    )

    st.divider()

    st.markdown(
        f"""
### Rules & limitations (important)

- **One source type per run**: use either **Upload files** *or* **Website links**, not both.
- **URL limit**: up to **{MAX_WEB_URL_SLOTS}** URLs.
- **Question cap**: hard cap is **{MAX_QUESTIONS_CAP}** questions (even if materials are large).
- **Minimum questions**: **{MIN_QUESTIONS}**.
- **Upload size cap**: total uploaded file size must be **≤ 10 MB**.
- **Website safety blocks**: URLs that resolve to **localhost/private/internal IPs** are blocked for safety.
- **State is per client session**:
  - Quizzly saves quiz state and error notebook history so you can return after reruns.
  - If you clear browser/site data or the temp state is deleted, history may be lost.
        """
    )

    st.info(
        "Tip: If website mode fails, try a more direct article URL (not a search results page), "
        "or switch to uploading a PDF export."
    )

