import json
import mimetypes
import os
import tempfile
import time
import traceback
import uuid
import hashlib

import streamlit as st
from openai import OpenAIError

from bknd.quizzly_bknd_gnrt import (
    create_extraction_chain,
    create_generation_chain,
    get_page_count,
    setup_api,
)
from bknd.quizzly_bknd_upldprcs import (
    PENDING_REMOVE_URL_INDEX,
    apply_pending_web_url_removal,
    docx_to_pdf,
    fetch_website_text,
    image_to_pdf,
    pptx_to_pdf,
    pseudo_pages_from_web_text,
)

try:
    # Streamlit Cloud can temporarily run a stale build; make this import robust.
    from bknd.quizzly_bknd_vrf import (
        run_quiz_output_guard,
        validate_quiz_shape,
        verify_quiz,
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
    FILE_FINGERPRINT_BYTES,
    MAX_QUESTIONS_CAP,
    MAX_QUESTIONS_PER_SOURCE,
    MAX_WEB_URL_SLOTS,
    MIN_QUESTIONS,
    WEB_FETCH_CACHE_TTL_SECS,
)

from fntnd.quizzly_state import (
    get_or_create_client_id,
    get_query_params,
    init_session_state,
    load_error_history,
    load_state_cached,
    persist_quiz_state,
    save_error_history,
    set_query_params,
    sha256_text,
)
from fntnd.views.quizzly_current_quiz_mistakes import render_current_quiz_mistakes
from fntnd.views.quizzly_error_notebook_view import render_error_notebook_view


st.set_page_config(page_title="Quizzly", page_icon="📖", layout="wide")

init_session_state()


@st.cache_data(ttl=WEB_FETCH_CACHE_TTL_SECS, show_spinner=False)
def _cached_fetch_website_text(url: str) -> tuple[bool, str, str]:
    return fetch_website_text(url)


def main():
    client_id = get_or_create_client_id()
    qp = get_query_params()
    quiz_id = (qp.get("quiz") or "").strip()
    debug_enabled = bool(st.secrets.get("DEBUG", False)) or (os.environ.get("DEBUG") == "1")
    view = (qp.get("view") or "").strip().lower()

    # If Streamlit reset our session_state (WebSocket reconnect / idle), try to rehydrate
    # from cached/disk state using URL params.
    if quiz_id and st.session_state.get("quiz_data") is None:
        hydrated = load_state_cached(client_id, quiz_id)
        if hydrated:
            st.session_state["quiz_data"] = hydrated.get("quiz_data")
            st.session_state["verification_report"] = hydrated.get("verification_report")
            st.session_state["_error_notebook_current"] = hydrated.get("error_notebook") or []
            st.session_state["_persisted_answers"] = hydrated.get("answers") or {}

    # Load all-time error notebook history once per session/client.
    # (If history is empty, avoid re-reading from disk on every rerun.)
    if not st.session_state.get("_error_history_loaded"):
        st.session_state["_error_notebook_history"] = load_error_history(client_id)
        st.session_state["_error_history_loaded"] = True

    # API Key Check via Streamlit Secrets
    if "OPENAI_API_KEY" not in st.secrets:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return

    # Set it as an environment variable so LangChain and OpenAI clients pick it up automatically
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        if view == "errors":
            if st.button("← Back to Quiz", use_container_width=True):
                set_query_params(client=client_id, quiz=quiz_id)
                st.rerun()
            st.markdown(
                """
                <style>
                section[data-testid="stSidebar"] [class*="st-key-quizzly_sidebar_controls"] {
                    display: none !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
        else:
            if st.button("📒 Error Notebook", use_container_width=True):
                set_query_params(client=client_id, quiz=quiz_id, view="errors")
                st.rerun()

        with st.container(key="quizzly_sidebar_controls"):
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
            st.session_state["_web_text"] = ""

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
            n_slots = min(int(st.session_state.get("web_url_slot_count", 1)), MAX_WEB_URL_SLOTS)
            st.caption("Website URLs")
            for i in range(n_slots):
                col_url, col_x = st.columns([1, 0.14], gap="small", vertical_alignment="center")
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
                    if "://" not in v:
                        v = "https://" + v
                    website_urls.append(v)
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
                st.session_state["web_url_slot_count"] = min(n_slots + 1, MAX_WEB_URL_SLOTS)
                st.rerun()

        num_questions = MIN_QUESTIONS
        generate_btn = False

        has_files = bool(uploaded_files)
        has_urls = bool(website_urls)

        if view != "errors" and (
            (source_mode == "Upload files" and has_files)
            or (source_mode == "Website links" and has_urls)
        ):
            temp_dir = tempfile.gettempdir()
            processed_paths: list[str] = []
            cleanup_paths: list[str] = []
            total_pages = 0
            web_blocks: list[tuple[str, str]] = []
            web_fetch_errors: list[str] = []
            source_count = 0

            if source_mode == "Upload files":
                st.session_state["_web_text"] = ""
                MAX_TOTAL_UPLOAD_BYTES = 10 * 1024 * 1024
                total_size = 0
                size_unknown = False
                for uf in uploaded_files:
                    sz = getattr(uf, "size", None)
                    if sz is None:
                        size_unknown = True
                        continue
                    total_size += int(sz)
                if size_unknown:
                    st.warning(
                        "Could not determine file sizes for one or more uploads. "
                        "If generation fails, reduce your upload sizes."
                    )
                elif total_size > MAX_TOTAL_UPLOAD_BYTES:
                    st.error(
                        "Total upload size must be ≤ 10 MB. "
                        f"Current total: {total_size / (1024 * 1024):.2f} MB."
                    )
                    uploaded_files = None
                    has_files = False
                    processed_paths = []
                    cleanup_paths = []
                    source_count = 0
                    max_questions = MIN_QUESTIONS
                    can_generate = False
                    generate_btn = False
                    # Skip further file processing
                    st.stop()
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
                st.session_state["current_paths"] = []
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
                total_web_pages = sum(max(1, pseudo_pages_from_web_text(t)) for _, t in web_blocks)
                if not web_blocks:
                    total_web_pages = 0

                combined_web = "\n\n---\n\n".join(f"Source: {u}\n\n{t}" for u, t in web_blocks)
                st.session_state["_web_text"] = combined_web

                max_questions = max(
                    MIN_QUESTIONS,
                    min(MAX_QUESTIONS_CAP, total_web_pages // 2, source_count * MAX_QUESTIONS_PER_SOURCE),
                )

            st.session_state["current_paths"] = processed_paths
            st.session_state["cleanup_paths"] = cleanup_paths

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
                help="The max number of questions is auto-set from your material pages/2 to keep quiz quality high.",
            )

            scenario_pct = st.slider(
                "Question Type (Scenario-based:Conceptual)",
                min_value=0,
                max_value=100,
                value=50,
                step=10,
                help="Controls the % of scenario-based questions (the rest are conceptual).",
            )

            if source_mode == "Upload files":
                can_generate = bool(processed_paths)
            else:
                can_generate = bool(web_blocks)

            if not can_generate:
                st.info("Add valid material (files or fetchable URLs) to generate a quiz.")

            st.write("")
            generate_btn = st.button("Generate & Verify Quiz", type="primary", disabled=(not can_generate))

    # --- Error Notebook view (all-time history) ---
    if view == "errors":
        render_error_notebook_view(client_id=client_id, quiz_id=quiz_id)
        return

    # --- Main Area: Processing & Display ---
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
    # Give main content most of the width; keep a narrow right rail.
    col1, col2 = st.columns([3, 1], gap="medium", vertical_alignment="top")

    with col1:
        st.title("Quizzly: Automated Quiz Generator")
        st.markdown(
            "Transform passive reading into active mastery. Upload documents or links to generate a verified, targeted quiz based on Bloom's Taxonomy."
        )

        if generate_btn:
            st.session_state["workflow_status_label"] = None
            st.session_state["workflow_status_lines"] = []
            quiz_id = uuid.uuid4().hex
            set_query_params(client=client_id, quiz=quiz_id)

            start_time = time.time()
            live_status = st.status("Starting workflow…", expanded=True)

            def log_line(s: str):
                st.session_state["workflow_status_lines"].append(s)
                try:
                    live_status.update(label=s)
                    live_status.write(s)
                except Exception:
                    pass

            client = None
            oai_file_ids: list[str] = []
            try:
                with st.spinner("Processing document workflow…"):
                    client = setup_api()

                    log_line("Uploading to secure environment...")
                    for fp in st.session_state.get("current_paths") or []:
                        mime_type, _ = mimetypes.guess_type(fp)
                        if mime_type is None:
                            mime_type = "application/octet-stream"

                        with open(fp, "rb") as f:
                            try:
                                oai_file = client.files.create(
                                    file=(os.path.basename(fp), f, mime_type),
                                    purpose="user_data",
                                )
                            except Exception as e:
                                log_line(f"Failed to upload {os.path.basename(fp)}: {e}")
                                continue

                        oai_file_ids.append(oai_file.id)
                    if not oai_file_ids and not st.session_state.get("_web_text", ""):
                        raise ValueError("No materials were uploaded successfully.")

                    log_line("Extracting core concepts...")
                    extractor = create_extraction_chain()
                    concepts_resp = extractor.invoke(
                        {
                            "file_ids": oai_file_ids,
                            "web_context": st.session_state.get("_web_text", ""),
                        }
                    )
                    concepts = concepts_resp.get("concepts") or []
                    if not concepts:
                        raise ValueError(
                            "Failed to extract concepts from the provided materials (website may be unreadable)."
                        )

                    log_line(f"Generating {num_questions} questions...")
                    generator = create_generation_chain(num_questions, scenario_pct=scenario_pct)
                    quiz_data = generator.invoke(
                        {
                            "file_ids": oai_file_ids,
                            "concepts_list": ", ".join(concepts),
                            "web_context": st.session_state.get("_web_text", ""),
                        }
                    )
                    log_line("Running output safety guard...")
                    quiz_data = run_quiz_output_guard(quiz_data)
                    quiz_data = validate_quiz_shape(quiz_data, num_questions)

                    log_line("Running quiz verification checks...")
                    report = verify_quiz(concepts, quiz_data, num_questions)

                    st.session_state["verification_report"] = report
                    st.session_state["quiz_data"] = quiz_data
                    st.session_state["_persisted_answers"] = {}
                    st.session_state["_quiz_submitted"] = False
                    st.session_state["_last_graded_hash"] = None
                    st.session_state["_current_quiz_score"] = None
                    st.session_state["_error_notebook_current"] = []

                    persist_quiz_state(
                        client_id,
                        quiz_id,
                        quiz_data=st.session_state.get("quiz_data"),
                        verification_report=st.session_state.get("verification_report"),
                        error_notebook=st.session_state.get("_error_notebook_current") or [],
                        answers={},
                    )

                    elapsed_time = time.time() - start_time
                    st.session_state["generation_time"] = elapsed_time
                    st.session_state["workflow_status_label"] = f"Workflow complete in {elapsed_time:.1f} secs"
                    try:
                        live_status.update(
                            label=st.session_state["workflow_status_label"], state="complete", expanded=False
                        )
                    except Exception:
                        pass
                st.rerun()

            except OpenAIError as e:
                st.error(
                    "There was a problem communicating with OpenAI. Check your API key, billing limits, or network connection."
                )
                if debug_enabled:
                    st.info(f"**Details:** {str(e)}")
                try:
                    live_status.update(label="Workflow failed.", state="error", expanded=False)
                except Exception:
                    pass

            except ValueError as e:
                st.error("A configuration or input value error occurred.")
                if debug_enabled:
                    st.info(f"**Details:** {str(e)}")
                try:
                    live_status.update(label="Workflow failed.", state="error", expanded=False)
                except Exception:
                    pass

            except Exception as e:
                error_type = type(e).__name__
                st.error(f"The workflow failed due to an unexpected {error_type}.")
                if debug_enabled:
                    st.info(f"**Details:** {str(e)}")
                    with st.expander("🛠️ Show Detailed Stack Trace (For Debugging)"):
                        st.code(traceback.format_exc(), language="python")
                try:
                    live_status.update(label="Workflow failed.", state="error", expanded=False)
                except Exception:
                    pass
            finally:
                cleanup_list = st.session_state.get("cleanup_paths") or []
                for p in cleanup_list:
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                st.session_state["cleanup_paths"] = []
                try:
                    for fid in oai_file_ids:
                        try:
                            client.files.delete(fid)
                        except Exception:
                            pass
                except Exception:
                    pass

        status_label = st.session_state.get("workflow_status_label")
        if status_label:
            with st.expander(status_label, expanded=False):
                lines = st.session_state.get("workflow_status_lines") or []
                if lines:
                    st.code("\n".join(lines))

        quiz_data = st.session_state.get("quiz_data")
        if quiz_data:
            report = st.session_state.get("verification_report")
            if report:
                with st.expander("View Comprehensive Verification Report"):
                    passed = report.get("passed_constraints", "Unknown")
                    c_score = report.get("constraint_score", 0.0)
                    c_feedback = report.get("constraint_feedback", [])
                    f_score = report.get("fidelity_score", "N/A")
                    p_score = report.get("pedagogical_score", "N/A")
                    reasoning = report.get(
                        "evaluator_reasoning", "No reasoning provided by evaluator."
                    )

                    status_icon = "✅ PASSED" if passed else "❌ FAILED"
                    st.subheader(f"Pipeline Status: {status_icon}")
                    st.divider()

                    st.markdown("#### 1. Structural Constraints (Code-Based Grading)")
                    st.write(f"**Constraint Score:** {c_score * 100}%")
                    for feedback_item in c_feedback:
                        icon = "✅" if "Pass" in feedback_item else "❌"
                        st.markdown(f"{icon} {feedback_item}")

                    st.divider()

                    st.markdown("#### 2. Quality Evaluation (LLM-Based Grading)")
                    colA, colB = st.columns(2)
                    with colA:
                        st.metric(label="Task Fidelity Score", value=f"{f_score}/5")
                    with colB:
                        st.metric(label="Pedagogical Score", value=f"{p_score}/5")

                    st.markdown("**Evaluator Reasoning:**")
                    st.info(reasoning)

            st.divider()
            st.subheader(quiz_data.get("quiz_title", "Assessment"))

            with st.form("quiz_form"):
                user_answers = {}
                persisted_answers = st.session_state.get("_persisted_answers") or {}
                show_feedback = bool(st.session_state.get("_quiz_submitted"))
                for i, q in enumerate(quiz_data.get("questions", [])):
                    difficulty = q.get("difficulty", "Unrated")
                    st.markdown(
                        f"**{i+1}. {q['question_text']}** *(Difficulty: {difficulty})*"
                    )

                    options = q.get("options", [])
                    widget_key = f"q_{q['id']}"
                    if widget_key not in st.session_state:
                        saved_idx = persisted_answers.get(str(q["id"]))
                        if saved_idx is not None:
                            st.session_state[widget_key] = saved_idx
                    user_answers[q["id"]] = st.radio(
                        "Answer",
                        options=range(len(options)),
                        format_func=lambda idx: options[idx],
                        key=widget_key,
                        index=None,
                        label_visibility="collapsed",
                    )

                    if show_feedback:
                        user_ans_now = st.session_state.get(widget_key)
                        if user_ans_now is None:
                            st.warning("This question was left blank.")
                        else:
                            try:
                                user_letter = ANSWER_LETTERS[int(user_ans_now)]
                            except Exception:
                                st.warning("Answer format was unexpected.")
                            else:
                                formatted_explanation = q["explanation"].replace("\n", "\n\n")
                                if user_letter == q["correct_option"]:
                                    st.success("Correct ✅")
                                    with st.expander("Show detailed explanation"):
                                        st.markdown(formatted_explanation)
                                else:
                                    st.error(
                                        f"Incorrect. The answer is {q['correct_option']}."
                                    )
                                    st.info(formatted_explanation)
                    st.write("---")

                qp_now = get_query_params()
                quiz_id_now = (qp_now.get("quiz") or "").strip()
                if quiz_id_now:
                    answers_snapshot = {
                        str(qid): (None if idx is None else int(idx))
                        for qid, idx in user_answers.items()
                    }
                    snap_hash = sha256_text(json.dumps(answers_snapshot, sort_keys=True))
                    if st.session_state.get("_last_autosave_hash") != snap_hash:
                        st.session_state["_last_autosave_hash"] = snap_hash
                        st.session_state["_persisted_answers"] = answers_snapshot
                        persist_quiz_state(
                            client_id,
                            quiz_id_now,
                            quiz_data=st.session_state.get("quiz_data"),
                            verification_report=st.session_state.get("verification_report"),
                            error_notebook=st.session_state.get("_error_notebook_current") or [],
                            answers=answers_snapshot,
                        )

                submitted = st.form_submit_button("Submit Answers")

                if submitted:
                    quiz_id_now = (qp.get("quiz") or "").strip()
                    if quiz_id_now:
                        answers_snapshot = {
                            str(qid): (None if idx is None else int(idx))
                            for qid, idx in user_answers.items()
                        }
                        grade_hash = sha256_text(json.dumps(answers_snapshot, sort_keys=True))
                        if st.session_state.get("_last_graded_hash") != grade_hash:
                            st.session_state["_last_graded_hash"] = grade_hash
                            correct_count = 0
                            total_count = len(quiz_data.get("questions", []))
                            for q in quiz_data.get("questions", []):
                                user_ans = user_answers.get(q["id"])
                                if user_ans is None:
                                    continue
                                try:
                                    user_letter = ANSWER_LETTERS[int(user_ans)]
                                except Exception:
                                    continue
                                if user_letter == q["correct_option"]:
                                    correct_count += 1
                                else:
                                    formatted_explanation = q["explanation"].replace("\n", "\n\n")
                                    options = q.get("options", [])
                                    correct_letter = q.get("correct_option")
                                    try:
                                        correct_idx = ANSWER_LETTERS.index(str(correct_letter))
                                        correct_text = (
                                            options[correct_idx] if 0 <= correct_idx < len(options) else None
                                        )
                                    except Exception:
                                        correct_idx = None
                                        correct_text = None
                                    error_entry = {
                                        "question_id": q.get("id"),
                                        "difficulty": q.get("difficulty"),
                                        "question": q.get("question_text"),
                                        "options": options,
                                        "user_answer_index": int(user_ans),
                                        "user_answer_letter": user_letter,
                                        "user_answer_text": options[int(user_ans)]
                                        if 0 <= int(user_ans) < len(options)
                                        else None,
                                        "correct_option": correct_letter,
                                        "correct_answer_index": correct_idx,
                                        "correct_answer_text": correct_text,
                                        "explanation": formatted_explanation,
                                    }
                                    cur = st.session_state.get("_error_notebook_current") or []
                                    if error_entry not in cur:
                                        cur.append(error_entry)
                                        st.session_state["_error_notebook_current"] = cur

                                    hist = st.session_state.get("_error_notebook_history") or []
                                    if error_entry not in hist:
                                        hist.append(error_entry)
                                        st.session_state["_error_notebook_history"] = hist
                                        save_error_history(client_id, hist)
                            st.session_state["_current_quiz_score"] = (correct_count, total_count)

                        st.session_state["_quiz_submitted"] = True
                        persist_quiz_state(
                            client_id,
                            quiz_id_now,
                            quiz_data=st.session_state.get("quiz_data"),
                            verification_report=st.session_state.get("verification_report"),
                            error_notebook=st.session_state.get("_error_notebook_current") or [],
                            answers=answers_snapshot,
                        )
                    st.rerun()

    with col2:
        render_current_quiz_mistakes(
            client_id=client_id,
            quiz_id=(qp.get("quiz") or "").strip(),
            persist_cb=lambda **kwargs: persist_quiz_state(
                client_id=kwargs["client_id"],
                quiz_id=kwargs["quiz_id"],
                quiz_data=st.session_state.get("quiz_data"),
                verification_report=st.session_state.get("verification_report"),
                error_notebook=kwargs["error_notebook_current"],
                answers=kwargs["answers"],
            ),
        )

if __name__ == "__main__":
    main()

