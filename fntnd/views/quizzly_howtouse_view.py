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

Tip: You can switch **Quiz Generation Mode**:

- **Fast**: skips concept extraction + skips LLM grading verification (fastest)
- **Full**: runs the full pipeline (best quality checks)

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
### Performance & cost expectations (rough)

Quiz generation time and cost depend mainly on:

- **Pages / amount of text**: impacts **concept extraction**.
- **Number of questions**: impacts **generation** and **verification**.
- **Network + model latency**: varies run-to-run.

**Real example run (your measurement)**:

- **50 pages** (1 PDF) → **25 questions** (Full mode, `gpt-5-mini`)
- Time: **~235 seconds** (≈ **3.9 minutes**)
- Cost: **~$0.05**

Useful “back-of-the-napkin” averages from that run:

- **Per page**: about **4.7 seconds/page** (235 / 50)
- **Per question**: about **9.4 seconds/question** (235 / 25)
- **Cost per question**: about **$0.002 per question** ($0.05 / 25)

Notes:

- Full mode can cost similar (or sometimes more/less) depending on token usage and caching.
- Fast mode mainly saves time by skipping 2 extra model calls.

### Rules & limitations (important)

- **One source type per run**: use either **Upload files** *or* **Website links**, not both.
- **URL limit**: up to **{MAX_WEB_URL_SLOTS}** URLs.
- **Question cap**: hard cap is **{MAX_QUESTIONS_CAP}** questions (even if materials are large).
- **Minimum questions**: **{MIN_QUESTIONS}**.
- **Upload size cap**: total uploaded file size must be **≤ 10 MB**.
- **Website safety blocks**: URLs that resolve to **localhost/private/internal IPs** are blocked for safety.
- **API cost varies**: total cost scales mostly with **question count** and **material size**, and depends on the model + cached token usage.
- **State is per client session**:
  - Quizzly saves quiz state and error notebook history so you can return after reruns.
  - If you clear browser/site data or the temp state is deleted, history may be lost.
        """
    )

    st.info(
        "Tip: If website mode fails, try a more direct article URL (not a search results page), "
        "or switch to uploading a PDF export."
    )

