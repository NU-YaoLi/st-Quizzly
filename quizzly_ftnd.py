import mimetypes
import os
import tempfile
import time
import traceback
import uuid

import hashlib
import json

import streamlit as st
from openai import OpenAIError

from quizzly_bknd_upldprcs import (
    PENDING_REMOVE_URL_INDEX,
    apply_pending_web_url_removal,
    docx_to_pdf,
    fetch_website_text,
    image_to_pdf,
    pptx_to_pdf,
    pseudo_pages_from_web_text,
)
from quizzly_bknd_gnrt import (
    setup_api,
    get_page_count,
    create_extraction_chain,
    create_generation_chain,
)

try:
    # Streamlit Cloud can temporarily run a stale build; make this import robust.
    from quizzly_bknd_vrf import (
        validate_quiz_shape,
        verify_quiz,
        run_quiz_output_guard,
    )
except Exception as _e:  # pragma: no cover
    def verify_quiz(*args, **kwargs):  # type: ignore[no-redef]
        raise RuntimeError(f"Failed to import verification module: {_e}")

    def validate_quiz_shape(quiz, expected_count: int):  # type: ignore[no-redef]
        # Minimal fallback so the app can still run if Cloud build is stale.
        if not isinstance(quiz, dict) or "questions" not in quiz:
            raise ValueError("Generated quiz JSON is missing 'questions'.")
        if not isinstance(quiz["questions"], list) or len(quiz["questions"]) != expected_count:
            raise ValueError("Generated quiz JSON has unexpected question count.")
        return quiz

    def run_quiz_output_guard(quiz_data: dict) -> dict:  # type: ignore[no-redef]
        return quiz_data

from quizzly_config import (
    ANSWER_LETTERS,
    MAX_QUESTIONS_CAP,
    MAX_QUESTIONS_PER_SOURCE,
    MAX_WEB_URL_SLOTS,
    FILE_FINGERPRINT_BYTES,
    MIN_QUESTIONS,
    WEB_FETCH_CACHE_TTL_SECS,
)


@st.cache_data(ttl=WEB_FETCH_CACHE_TTL_SECS, show_spinner=False)
def _cached_fetch_website_text(url: str) -> tuple[bool, str, str]:
    return fetch_website_text(url)


STATE_DIR = os.path.join(tempfile.gettempdir(), "quizzly_state")


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _get_query_params() -> dict[str, str]:
    # streamlit >=1.30: st.query_params behaves like a mutable mapping.
    try:
        qp = dict(st.query_params)  # type: ignore[attr-defined]
        # values can be list-like in some versions; normalize to str
        return {k: (v[0] if isinstance(v, list) else str(v)) for k, v in qp.items()}
    except Exception:
        # streamlit <1.30 compatibility
        try:
            qp = st.experimental_get_query_params()
            return {k: (v[0] if isinstance(v, list) and v else "") for k, v in qp.items()}
        except Exception:
            return {}


def _set_query_params(**kwargs: str) -> None:
    cleaned = {k: v for k, v in kwargs.items() if v}
    try:
        st.query_params.clear()  # type: ignore[attr-defined]
        st.query_params.update(cleaned)  # type: ignore[attr-defined]
    except Exception:
        st.experimental_set_query_params(**cleaned)


def _get_or_create_client_id() -> str:
    qp = _get_query_params()
    client_id = (qp.get("client") or "").strip()
    if client_id:
        return client_id
    client_id = uuid.uuid4().hex
    quiz_id = (qp.get("quiz") or "").strip()
    _set_query_params(client=client_id, quiz=quiz_id)
    return client_id


def _state_path(client_id: str, quiz_id: str) -> str:
    safe_client = "".join(ch for ch in client_id if ch.isalnum())[:64] or "client"
    safe_quiz = "".join(ch for ch in quiz_id if ch.isalnum())[:64] or "quiz"
    return os.path.join(STATE_DIR, f"{safe_client}_{safe_quiz}.json")


def _load_state_from_disk(client_id: str, quiz_id: str) -> dict | None:
    if not client_id or not quiz_id:
        return None
    p = _state_path(client_id, quiz_id)
    try:
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_state_to_disk(client_id: str, quiz_id: str, payload: dict) -> None:
    if not client_id or not quiz_id:
        return
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = _state_path(client_id, quiz_id)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        # Best-effort only; don't break the app if disk write fails.
        pass


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def _load_state_cached(client_id: str, quiz_id: str) -> dict | None:
    # Cache-backed "rehydration" for WebSocket reconnects.
    return _load_state_from_disk(client_id, quiz_id)


def _persist_quiz_state(
    client_id: str,
    quiz_id: str,
    *,
    quiz_data: dict | None,
    verification_report: dict | None,
    error_notebook: list[dict],
    answers: dict[str, int | None],
) -> None:
    payload = {
        "saved_at": time.time(),
        "quiz_data": quiz_data,
        "verification_report": verification_report,
        "error_notebook": error_notebook,
        "answers": answers,
    }
    # Disk is the source of truth for cache; cache reduces rereads after reconnect.
    _save_state_to_disk(client_id, quiz_id, payload)
    try:
        _load_state_cached.clear()  # type: ignore[attr-defined]
    except Exception:
        pass


st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

# Initialize Session States for stateful UI
if 'quiz_data' not in st.session_state:
    st.session_state.quiz_data = None
if 'error_notebook' not in st.session_state:
    st.session_state.error_notebook = []
if 'verification_report' not in st.session_state:
    st.session_state.verification_report = None
if 'generation_time' not in st.session_state:
    st.session_state.generation_time = None
if 'current_paths' not in st.session_state:
    st.session_state.current_paths = []
if "cleanup_paths" not in st.session_state:
    st.session_state.cleanup_paths = []
if "workflow_status_label" not in st.session_state:
    st.session_state.workflow_status_label = None
if "workflow_status_lines" not in st.session_state:
    st.session_state.workflow_status_lines = []
if "web_url_slot_count" not in st.session_state:
    st.session_state.web_url_slot_count = 1


def main():
    client_id = _get_or_create_client_id()
    qp = _get_query_params()
    quiz_id = (qp.get("quiz") or "").strip()

    # If Streamlit reset our session_state (WebSocket reconnect / idle), try to rehydrate
    # from cached/disk state using URL params.
    if quiz_id and st.session_state.quiz_data is None:
        hydrated = _load_state_cached(client_id, quiz_id)
        if hydrated:
            st.session_state.quiz_data = hydrated.get("quiz_data")
            st.session_state.verification_report = hydrated.get("verification_report")
            st.session_state.error_notebook = hydrated.get("error_notebook") or []
            st.session_state._persisted_answers = hydrated.get("answers") or {}

    # API Key Check via Streamlit Secrets
    if "OPENAI_API_KEY" not in st.secrets:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return
    
    # Set it as an environment variable so LangChain and OpenAI clients pick it up automatically
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        st.header("Study Materials")

        source_mode = st.radio(
            "Material source (choose one)",
            ["Upload files", "Website links"],
            horizontal=True,
            help="Use either uploaded files (up to 5) or website URLs (up to 5), not both.",
        )

        files_mode = source_mode == "Upload files"
        if files_mode:
            # Prevent stale website text from influencing file-only runs.
            st.session_state.web_text = ""

        # Always mount the uploader (stable key) so switching source mode does not drop uploads.
        uploaded_files = st.file_uploader(
            "Upload files (PDF, DOCX, PPTX, TXT, PNG, JPG) — max 5",
            type=["pdf", "docx", "pptx", "txt", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="quizzly_study_files",
            disabled=not files_mode,
        )
        if files_mode and uploaded_files and len(uploaded_files) > 5:
            st.error("Please upload at most 5 files. Remove extras and try again.")
            uploaded_files = None
        website_urls: list[str] = []

        if not files_mode:
            st.markdown(
                """
                <style>
                /* Hide the file uploader UI in Website links mode (keep it mounted so state persists). */
                section[data-testid="stSidebar"] [class*="st-key-quizzly_study_files"] {
                    display: none !important;
                }

                /* Icon-sized secondary buttons in sidebar only (not primary Generate) */
                section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"],
                section[data-testid="stSidebar"] button[kind="secondary"] {
                    display: inline-flex !important;
                    align-items: center !important;
                    justify-content: center !important;
                    padding: 0.35rem 0.45rem !important;
                    min-width: 2.25rem !important;
                    min-height: 2.25rem !important;
                    font-size: 1.15rem !important;
                    line-height: 1 !important;
                }
                section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] p,
                section[data-testid="stSidebar"] button[kind="secondary"] p {
                    line-height: 1 !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.caption(
                "Use **+** to add a row and **✕** to remove it (at least one row stays). "
                "Search-result pages often fail; prefer article URLs."
            )
            apply_pending_web_url_removal()
            n_slots = min(int(st.session_state.web_url_slot_count), MAX_WEB_URL_SLOTS)
            st.caption(f"Website URLs")
            for i in range(n_slots):
                col_url, col_x = st.columns(
                    [1, 0.14], gap="small", vertical_alignment="center"
                )
                with col_url:
                    st.text_input(
                        "URL",
                        key=f"web_url_{i}",
                        label_visibility="collapsed",
                        placeholder="https://…",
                    )
                with col_x:
                    if st.button(
                        "✕",
                        key=f"remove_web_url_{i}",
                        type="secondary",
                        disabled=(n_slots <= 1),
                        help="Remove this URL field",
                        use_container_width=True,
                    ):
                        st.session_state[PENDING_REMOVE_URL_INDEX] = i
                        st.rerun()
            website_urls = []
            for i in range(n_slots):
                v = (st.session_state.get(f"web_url_{i}") or "").strip()
                if v:
                    # Normalize URL: default scheme to https://
                    if "://" not in v:
                        v = "https://" + v
                    website_urls.append(v)
            # De-dupe URLs (case-insensitive)
            deduped = []
            seen = set()
            for u in website_urls:
                k = u.strip().lower().rstrip("/")
                if k in seen:
                    continue
                seen.add(k)
                deduped.append(u.strip())
            if len(deduped) != len(website_urls):
                st.warning("Duplicate URL(s) detected and will be ignored.")
            website_urls = deduped
            if st.button(
                "+",
                key="add_web_url_slot",
                type="secondary",
                disabled=(n_slots >= MAX_WEB_URL_SLOTS),
                help="Add another URL field",
            ):
                st.session_state.web_url_slot_count = min(n_slots + 1, MAX_WEB_URL_SLOTS)
                st.rerun()

        num_questions = MIN_QUESTIONS
        generate_btn = False

        has_files = bool(uploaded_files)
        has_urls = bool(website_urls)

        if (source_mode == "Upload files" and has_files) or (source_mode == "Website links" and has_urls):
            temp_dir = tempfile.gettempdir()
            processed_paths: list[str] = []
            cleanup_paths: list[str] = []
            total_pages = 0
            web_blocks: list[tuple[str, str]] = []
            web_fetch_errors: list[str] = []
            source_count = 0

            if source_mode == "Upload files":
                st.session_state.web_text = ""
                # Reject duplicate filenames (case-insensitive) to avoid wasting tokens.
                seen_fps = set()
                unique_uploads = []
                dup_notes = []
                for uf in uploaded_files:
                    safe_name = os.path.basename(uf.name) or "upload"
                    buf = uf.getbuffer()
                    size = len(buf)
                    head = bytes(buf[:FILE_FINGERPRINT_BYTES])
                    fp = (safe_name.lower(), size, hashlib.sha256(head).hexdigest())
                    if fp in seen_fps:
                        dup_notes.append(safe_name)
                        continue
                    seen_fps.add(fp)
                    unique_uploads.append(uf)
                if dup_notes:
                    st.warning("Duplicate file(s) detected and will be ignored: " + ", ".join(dup_notes))

                for uf in unique_uploads:
                    safe_name = os.path.basename(uf.name) or "upload"
                    temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{safe_name}")
                    with open(temp_path, "wb") as f:
                        f.write(uf.getbuffer())
                    cleanup_paths.append(temp_path)

                    ext = os.path.splitext(temp_path)[1].lower()

                    if ext == ".docx":
                        new_path = docx_to_pdf(temp_path)
                        cleanup_paths.append(new_path)
                    elif ext == ".pptx":
                        new_path = pptx_to_pdf(temp_path)
                        cleanup_paths.append(new_path)
                    elif ext in [".png", ".jpg", ".jpeg"]:
                        new_path = image_to_pdf(temp_path)
                        cleanup_paths.append(new_path)
                    else:
                        new_path = temp_path

                    processed_paths.append(new_path)
                    total_pages += get_page_count(new_path)

                source_count = len(processed_paths)
                max_questions = max(
                    MIN_QUESTIONS,
                    min(MAX_QUESTIONS_CAP, total_pages // 2, source_count * MAX_QUESTIONS_PER_SOURCE),
                )

            else:
                st.session_state.current_paths = []
                for url in website_urls:
                    try:
                        ok, chunk, reason = _cached_fetch_website_text(url)
                        if ok and chunk:
                            web_blocks.append((url, chunk))
                        else:
                            if reason in {"blocked_localhost", "blocked_private_ip", "invalid_scheme"}:
                                web_fetch_errors.append(f"{url}: blocked for safety ({reason}).")
                            elif reason == "dns_failed":
                                web_fetch_errors.append(f"{url}: DNS lookup failed.")
                            elif reason == "request_failed":
                                web_fetch_errors.append(f"{url}: request failed (timeout/blocked).")
                            else:
                                web_fetch_errors.append(f"{url}: too little readable text.")
                    except Exception as e:
                        web_fetch_errors.append(f"{url}: {e}")

                if web_fetch_errors:
                    for msg in web_fetch_errors:
                        st.error(msg)

                source_count = len(web_blocks)
                total_web_pages = sum(
                    max(1, pseudo_pages_from_web_text(t)) for _, t in web_blocks
                )
                if not web_blocks:
                    total_web_pages = 0

                combined_web = "\n\n---\n\n".join(
                    f"Source: {u}\n\n{t}" for u, t in web_blocks
                )
                st.session_state.web_text = combined_web

                max_questions = max(MIN_QUESTIONS, min(MAX_QUESTIONS_CAP, total_web_pages // 2))
                max_questions = max(
                    MIN_QUESTIONS,
                    min(MAX_QUESTIONS_CAP, total_web_pages // 2, source_count * MAX_QUESTIONS_PER_SOURCE),
                )

            st.session_state.current_paths = processed_paths
            st.session_state.cleanup_paths = cleanup_paths

            if source_count == 0:
                st.warning("Materials loaded: 0 sources — check your URLs or switch to file upload.")
            else:
                st.success(f"Materials Loaded: {source_count} source(s) detected.")

            st.header("Quiz Settings")
            num_questions = st.number_input(
                "Number of Questions",
                min_value=MIN_QUESTIONS,
                max_value=max_questions,
                value=MIN_QUESTIONS,
            )

            st.session_state.current_paths = processed_paths
            if source_mode == "Upload files":
                can_generate = bool(processed_paths)
            else:
                can_generate = bool(web_blocks)

            if not can_generate:
                st.info("Add valid material (files or fetchable URLs) to generate a quiz.")
            generate_btn = st.button("Generate & Verify Quiz", type="primary", disabled=(not can_generate))

    # --- Main Area: Processing & Display ---
    

    # --- View 1: Quiz Execution ---
    # Error Notebook: full-height right rail only (no frame around quiz column)
    st.markdown(
        """
        <style>
        /* Keyed Error Notebook container fills the right rail height */
        section[data-testid="stMainBlockContainer"] [class*="st-key-quizzly_error_notebook"] {
            min-height: calc(100vh - 9.5rem) !important;
            box-sizing: border-box;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns([3, 1], gap="large", vertical_alignment="top")
    
    with col1:
        st.title("Quizzly: Automated Quiz Generator")
        st.markdown("Transform passive reading into active mastery. Upload documents or links to generate a verified, targeted quiz based on Bloom's Taxonomy.")

        if generate_btn:
            st.session_state.workflow_status_label = None
            st.session_state.workflow_status_lines = []
            # New quiz run: assign a stable quiz_id and place it in the URL so
            # a WebSocket reconnect can rehydrate state.
            quiz_id = uuid.uuid4().hex
            _set_query_params(client=client_id, quiz=quiz_id)

            start_time = time.time()

            def log_line(s: str):
                st.session_state.workflow_status_lines.append(s)

            client = None
            oai_file_ids: list[str] = []
            try:
                with st.spinner("Processing document workflow…"):
                    client = setup_api()

                    log_line("Uploading to secure environment...")
                    for fp in st.session_state.current_paths:
                        mime_type, _ = mimetypes.guess_type(fp)
                        if mime_type is None:
                            mime_type = "application/octet-stream"

                        with open(fp, "rb") as f:
                            try:
                                oai_file = client.files.create(
                                    file=(os.path.basename(fp), f, mime_type),
                                    purpose="user_data"
                                )
                            except Exception as e:
                                log_line(f"Failed to upload {os.path.basename(fp)}: {e}")
                                continue

                        oai_file_ids.append(oai_file.id)
                    if not oai_file_ids and not st.session_state.get("web_text", ""):
                        raise ValueError("No materials were uploaded successfully.")

                    log_line("Extracting core concepts...")
                    extractor = create_extraction_chain()
                    concepts_resp = extractor.invoke({
                        "file_ids": oai_file_ids,
                        "web_context": st.session_state.get("web_text", ""),
                    })
                    concepts = concepts_resp.get("concepts") or []
                    if not concepts:
                        raise ValueError("Failed to extract concepts from the provided materials (website may be unreadable).")

                    log_line(f"Generating {num_questions} questions...")
                    generator = create_generation_chain(num_questions)
                    quiz_data = generator.invoke({
                        "file_ids": oai_file_ids,
                        "concepts_list": ", ".join(concepts),
                        "web_context": st.session_state.get("web_text", ""),
                    })
                    log_line("Running output safety guard...")
                    quiz_data = run_quiz_output_guard(quiz_data)
                    quiz_data = validate_quiz_shape(quiz_data, num_questions)

                    log_line("Running quiz verification checks...")
                    report = verify_quiz(concepts, quiz_data, num_questions)

                    st.session_state.verification_report = report
                    st.session_state.quiz_data = quiz_data
                    st.session_state._persisted_answers = {}

                    # Persist quiz immediately (before any answers are chosen).
                    _persist_quiz_state(
                        client_id,
                        quiz_id,
                        quiz_data=st.session_state.quiz_data,
                        verification_report=st.session_state.verification_report,
                        error_notebook=st.session_state.error_notebook,
                        answers={},
                    )

                    elapsed_time = time.time() - start_time
                    st.session_state.generation_time = elapsed_time
                    st.session_state.workflow_status_label = (
                        f"Workflow complete in {elapsed_time:.1f} secs"
                    )
                st.rerun()

            except OpenAIError as e:
                st.error("There was a problem communicating with OpenAI. Check your API key, billing limits, or network connection.")
                st.info(f"**Details:** {str(e)}")

            except ValueError as e:
                st.error("A configuration or input value error occurred.")
                st.info(f"**Details:** {str(e)}")

            except Exception as e:
                error_type = type(e).__name__
                st.error(f"The workflow failed due to an unexpected {error_type}.")
                st.info(f"**Details:** {str(e)}")

                with st.expander("🛠️ Show Detailed Stack Trace (For Debugging)"):
                    st.code(traceback.format_exc(), language="python")
            finally:
                # Best-effort cleanup of uploaded temp files (local) and OpenAI file objects (remote).
                cleanup_list = st.session_state.get("cleanup_paths") or []
                for p in cleanup_list:
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                st.session_state.cleanup_paths = []
                try:
                    for fid in oai_file_ids:
                        try:
                            client.files.delete(fid)
                        except Exception:
                            pass
                except Exception:
                    pass

        if st.session_state.workflow_status_label:
            with st.expander(st.session_state.workflow_status_label, expanded=False):
                lines = st.session_state.workflow_status_lines or []
                if lines:
                    st.code("\n".join(lines))

        if st.session_state.quiz_data:
            # Show verification results in an expander
            if st.session_state.verification_report:
                report = st.session_state.verification_report
                with st.expander("View Comprehensive Verification Report"):
                    
                    # 1. Unpack all 6 metrics
                    passed = report.get('passed_constraints', 'Unknown')
                    c_score = report.get('constraint_score', 0.0)
                    c_feedback = report.get('constraint_feedback', [])
                    f_score = report.get('fidelity_score', 'N/A')
                    p_score = report.get('pedagogical_score', 'N/A')
                    reasoning = report.get('evaluator_reasoning', 'No reasoning provided by evaluator.')
                    
                    # 2. Render Overall Status
                    status_icon = "✅ PASSED" if passed else "❌ FAILED"
                    st.subheader(f"Pipeline Status: {status_icon}")
                    st.divider()
                    
                    # 3. Render Code-Based Metrics (Unit Tests)
                    st.markdown("#### 1. Structural Constraints (Code-Based Grading)")
                    st.write(f"**Constraint Score:** {c_score * 100}%")
                    for feedback_item in c_feedback:
                        # Use green checkmarks for passes, red X's for fails
                        icon = "✅" if "Pass" in feedback_item else "❌"
                        st.markdown(f"{icon} {feedback_item}")
                        
                    st.divider()
                    
                    # 4. Render LLM-Based Metrics (Integration Tests)
                    st.markdown("#### 2. Quality Evaluation (LLM-Based Grading)")
                    colA, colB = st.columns(2)
                    with colA:
                        st.metric(label="Task Fidelity Score", value=f"{f_score}/5")
                    with colB:
                        st.metric(label="Pedagogical Score", value=f"{p_score}/5")
                    
                    st.markdown("**Evaluator Reasoning:**")
                    st.info(reasoning)

            st.divider()
            st.subheader(st.session_state.quiz_data.get("quiz_title", "Assessment"))
            
            # Interactive Quiz
            with st.form("quiz_form"):
                user_answers = {}
                persisted_answers = st.session_state.get("_persisted_answers") or {}
                for i, q in enumerate(st.session_state.quiz_data.get("questions", [])):
                    # Add the Difficulty badge to the question header
                    difficulty = q.get('difficulty', 'Unrated')
                    st.markdown(f"**{i+1}. {q['question_text']}** *(Difficulty: {difficulty})*")
                    
                    options = q.get("options", [])
                    widget_key = f"q_{q['id']}"
                    if widget_key not in st.session_state:
                        saved_idx = persisted_answers.get(str(q["id"]))
                        if saved_idx is not None:
                            st.session_state[widget_key] = saved_idx
                    user_answers[q['id']] = st.radio(
                        "Select an option:",
                        options=range(len(options)),
                        format_func=lambda idx: options[idx],
                        key=widget_key,
                        index=None,
                    )
                    st.write("---")
                
                # Autosave current selections (best-effort) so a reconnect restores progress
                # even if the user hasn't pressed "Submit Answers" yet.
                qp_now = _get_query_params()
                quiz_id_now = (qp_now.get("quiz") or "").strip()
                if quiz_id_now:
                    answers_snapshot = {
                        str(qid): (None if idx is None else int(idx))
                        for qid, idx in user_answers.items()
                    }
                    snap_hash = _sha256_text(json.dumps(answers_snapshot, sort_keys=True))
                    if st.session_state.get("_last_autosave_hash") != snap_hash:
                        st.session_state._last_autosave_hash = snap_hash
                        st.session_state._persisted_answers = answers_snapshot
                        _persist_quiz_state(
                            client_id,
                            quiz_id_now,
                            quiz_data=st.session_state.quiz_data,
                            verification_report=st.session_state.verification_report,
                            error_notebook=st.session_state.error_notebook,
                            answers=answers_snapshot,
                        )

                submitted = st.form_submit_button("Submit Answers")
                
                if submitted:
                    for q in st.session_state.quiz_data["questions"]:
                        user_ans = user_answers[q['id']]
                        
                        if user_ans is None:
                            st.warning(f"Question {q['id']} was left blank.")
                            continue
                        try:
                            user_letter = ANSWER_LETTERS[int(user_ans)]
                        except Exception:
                            st.warning(f"Question {q['id']} answer format was unexpected.")
                            continue
                        
                        # Format the explanation so Markdown renders the newlines perfectly
                        formatted_explanation = q['explanation'].replace('\n', '\n\n')
                        
                        if user_letter == q['correct_option']:
                            st.success(f"**Q{q['id']}:** Correct! ✅")
                            # Optionally show the explanation even when correct, for reinforcement!
                            with st.expander("Show detailed explanation"):
                                st.markdown(formatted_explanation)
                        else:
                            st.error(f"**Q{q['id']}:** Incorrect. The answer is {q['correct_option']}.")
                            st.info(formatted_explanation)
                            
                            # Add to Error Notebook if not already there
                            error_entry = {
                                "question": q['question_text'],
                                "user_wrong": q["options"][int(user_ans)],
                                "explanation": formatted_explanation
                            }
                            if error_entry not in st.session_state.error_notebook:
                                st.session_state.error_notebook.append(error_entry)

                    # Persist after submit (includes any new error notebook entries).
                    quiz_id_now = (qp.get("quiz") or "").strip()
                    if quiz_id_now:
                        answers_snapshot = {
                            str(qid): (None if idx is None else int(idx))
                            for qid, idx in user_answers.items()
                        }
                        _persist_quiz_state(
                            client_id,
                            quiz_id_now,
                            quiz_data=st.session_state.quiz_data,
                            verification_report=st.session_state.verification_report,
                            error_notebook=st.session_state.error_notebook,
                            answers=answers_snapshot,
                        )

    # --- View 2: Error Notebook Right Panel ---
    with col2:
        with st.container(
            border=True,
            height="stretch",
            width="stretch",
            key="quizzly_error_notebook",
        ):
            st.header("Error Notebook")
            st.markdown("Review your mistakes to reinforce learning.")
            st.divider()

            if not st.session_state.error_notebook:
                st.info("No errors logged yet. Great job!")
            else:
                for idx, error in enumerate(st.session_state.error_notebook):
                    with st.expander(f"Review Question {idx + 1}"):
                        st.markdown(f"**Q:** {error['question']}")
                        st.markdown(f"❌ *You answered: {error['user_wrong']}*")
                        # Using markdown here guarantees the \n\n breaks render nicely
                        st.markdown(f"💡 **Correction:**\n\n{error['explanation']}")
                
                st.divider()
                # Adding use_container_width=True makes the button span the panel nicely
                if st.button("Clear Notebook", use_container_width=True):
                    st.session_state.error_notebook = []
                    # Keep disk/cache in sync so a reconnect doesn't resurrect old errors.
                    qp2 = _get_query_params()
                    quiz_id_now = (qp2.get("quiz") or "").strip()
                    if quiz_id_now and st.session_state.get("quiz_data"):
                        persisted_answers = st.session_state.get("_persisted_answers") or {}
                        _persist_quiz_state(
                            client_id,
                            quiz_id_now,
                            quiz_data=st.session_state.quiz_data,
                            verification_report=st.session_state.verification_report,
                            error_notebook=[],
                            answers=persisted_answers,
                        )
                    st.rerun()

if __name__ == "__main__":
    main()