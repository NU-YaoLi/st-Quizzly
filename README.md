# Quizzly (Streamlit) — quiz generator + verification + error notebook

Quizzly is a Streamlit web app that turns **study materials** (uploaded files *or* website links) into a **multiple‑choice quiz** and then runs **verification checks** to catch malformed / low‑fidelity outputs. While taking the quiz, mistakes are captured into a persistent **Error Notebook** for targeted review.

## What it does

- **Material sources (choose one)**
  - **Upload files** (max **5**): `PDF`, `DOCX`, `PPTX`, `TXT`, `PNG`, `JPG/JPEG`
  - **Website links** (max **5**) with SSRF safety checks (blocks localhost / private IPs)
- **Auto sizing + limits**
  - Total upload size limited to **≤ 10 MB**
  - Question count is capped (hard cap **50**) and also constrained by your material size
- **Quiz generation**
  - Extracts **core concepts** first
  - Generates MCQs with **Easy → Medium → Hard** ordering (Bloom’s‑style difficulty)
  - Slider to control **scenario‑based vs conceptual** questions
  - **Quiz Generation Mode**
    - **Full**: concept extraction + generation + LLM grading verification
    - **Fast**: skips concept extraction + skips LLM grading verification (still runs output guard + schema checks)
- **Verification pipeline**
  - **Output guard** (rejects unsafe / manipulative outputs, rewrites fixable format issues)
  - **Schema validation** (exact question count, required keys, 4 options, A/B/C/D answers)
  - **Quality evaluation** (LLM‑based scoring for task fidelity + pedagogy)
- **Quiz taking**
  - Submit answers to see feedback + explanations
  - **Redo Quiz** resets your answers but keeps the same generated quiz
- **Error Notebook**
  - “Mistakes Review” panel for the current quiz
  - “Error Notebook” view shows **all‑time history** for your client session, with a clear‑all button
- **Resumable sessions**
  - Quiz state is persisted locally (temporary directory) and can be rehydrated via URL params
  - URL params are **HMAC‑signed** to prevent guessing other users’ state (when a signing secret is set)

## Quick start (local)

### Prerequisites

- Python 3.10+ recommended
- An OpenAI API key

### Install

```bash
pip install -r requirements.txt
```

### Configure secrets

Create `.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "YOUR_KEY_HERE"

# Optional:
# DEBUG = true
# STATE_SIGNING_SECRET = "any-long-random-string"
```

### Run

```bash
streamlit run quizzly_main.py
```

## How the workflow works (high level)

1. **Load materials**
   - Files are temporarily saved locally; `DOCX/PPTX/images` are converted to PDF for page counting.
   - Websites are fetched and simplified into readable text (with response size caps + SSRF guard).
2. **Upload to OpenAI (files mode)**
   - Files are uploaded using the OpenAI Files API with `purpose="user_data"`.
3. **Extract concepts → Generate quiz**
   - A first call extracts the most important concepts.
   - A second call generates the quiz JSON for the selected question count + scenario/concept ratio.
4. **Guard + verify**
   - Guard pass can keep / rewrite / reject the quiz.
   - Code‑based checks validate strict structure.
   - LLM‑based grading produces task‑fidelity + pedagogy scores.
5. **Take the quiz**
   - Answers are autosaved; on submission, incorrect answers are appended to the Error Notebook.

## Configuration knobs

- **Question limits**: edit `quizzly_config.py`
  - `MAX_QUESTIONS_CAP`, `MIN_QUESTIONS`, etc.
- **Cost estimates**
  - `quizzly_config.py` includes `MODEL_PRICING_USD_PER_1K` used to estimate total cost from token usage.
- **Session signing**
  - Set `STATE_SIGNING_SECRET` (recommended) to enable HMAC signing for persisted state URLs.

## Notes / caveats

- **Website fetching**: some pages (search results, heavily scripted sites, paywalls) may fail or extract too little text.
- **Privacy**: in file mode, materials are sent to the OpenAI API to generate your quiz; the app attempts to delete uploaded file objects after the workflow completes.
- **State storage**: quiz state + error history are stored in your OS temp directory under a `quizzly_state` folder.
- **Performance**: quiz runs have fixed overhead (upload + model roundtrips) and scale with pages + question count.
  - Example (50-page PDF → 25 questions):
    - `gpt-5-mini`: **~235s**, **~$0.05**
    - `gpt-5.4-mini`: **~67s**, **~$0.12**
  - Relative to `gpt-5-mini`, `gpt-5.4-mini` is **~3.5× faster** and **~2.4× more expensive** (based on the example above).

## Project layout

- `quizzly_main.py`: Streamlit entrypoint (sets page config, calls the app)
- `fntnd/quizzly_ftnd.py`: Streamlit UI + workflow orchestration
- `bknd/`: generation / upload-processing / verification helpers
- `fntnd/views/`: Error Notebook views and “Mistakes Review” panel
