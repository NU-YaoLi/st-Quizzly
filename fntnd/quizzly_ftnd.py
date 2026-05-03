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

from bknd.quizzly_question_gnrt import (
    create_extraction_chain,
    create_generation_chain,
    get_page_count,
    setup_api,
)
from bknd.quizzly_question_upldprcs import (
    PENDING_REMOVE_URL_INDEX,
    apply_pending_web_url_removal,
    docx_to_pdf,
    fetch_website_text,
    image_to_pdf,
    pptx_to_pdf,
    pseudo_pages_from_web_text,
)
from bknd.quizzly_rate_limit import (
    check_daily_generation_allowed,
    record_successful_generation,
)
from bknd.quizzly_usage_log import QuizGenerationUsageFields, token_triple_from_breakdown

try:
    # Streamlit Cloud can temporarily run a stale build; make this import robust.
    from bknd.quizzly_question_vrf import (
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
    MAX_WEB_URL_SLOTS,
    MIN_QUESTIONS,
    MODEL_PRICING_USD_PER_1K,
    QUIZZLY_MODEL,
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
    sign_client,
    sign_state,
)
from fntnd.views.quizzly_current_quiz_mistakes import render_current_quiz_mistakes
from fntnd.views.quizzly_data_analysis_view import render_data_analysis_view
from fntnd.views.quizzly_error_notebook_view import render_error_notebook_view
from fntnd.views.quizzly_howtouse_view import render_how_to_use_view

# Sidebar “utility” views: hide materials UI; quiz state stays in session.
_QUIZ_AUX_VIEWS = frozenset({"howto", "errors", "analytics"})


init_session_state()


@st.cache_data(ttl=WEB_FETCH_CACHE_TTL_SECS, show_spinner=False)
def _cached_fetch_website_text(url: str) -> tuple[bool, str, str]:
    return fetch_website_text(url)


def main():
    client_id = get_or_create_client_id()
    qp = get_query_params()
    quiz_id = (qp.get("quiz") or "").strip()
    sig = (qp.get("sig") or "").strip()
    csig = (qp.get("csig") or "").strip()
    debug_enabled = bool(st.secrets.get("DEBUG", False)) or (os.environ.get("DEBUG") == "1")
    view = (qp.get("view") or "").strip().lower()

    # If Streamlit reset our session_state (WebSocket reconnect / idle), try to rehydrate
    # from cached/disk state using URL params.
    if quiz_id and st.session_state.get("quiz_data") is None:
        hydrated = load_state_cached(client_id, quiz_id, sig=sig)
        if hydrated:
            st.session_state["quiz_data"] = hydrated.get("quiz_data")
            st.session_state["verification_report"] = hydrated.get("verification_report")
            st.session_state["_error_notebook_current"] = hydrated.get("error_notebook") or []
            st.session_state["_persisted_answers"] = hydrated.get("answers") or {}
            st.session_state["_quiz_submitted"] = bool(hydrated.get("quiz_submitted") or False)
            score = hydrated.get("current_quiz_score")
            if isinstance(score, (list, tuple)) and len(score) == 2:
                try:
                    st.session_state["_current_quiz_score"] = (int(score[0]), int(score[1]))
                except Exception:
                    st.session_state["_current_quiz_score"] = None
            else:
                st.session_state["_current_quiz_score"] = None
            st.session_state["workflow_status_label"] = hydrated.get("workflow_status_label")
            st.session_state["workflow_status_lines"] = hydrated.get("workflow_status_lines") or []

    # Load all-time error notebook history once per session/client.
    # (If history is empty, avoid re-reading from disk on every rerun.)
    if not st.session_state.get("_error_history_loaded"):
        st.session_state["_error_notebook_history"] = load_error_history(client_id, csig=csig)
        st.session_state["_error_history_loaded"] = True

    # API Key Check via Streamlit Secrets (avoid KeyError if secrets layout differs)
    try:
        api_key = (st.secrets.get("OPENAI_API_KEY") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return

    # Set it as an environment variable so LangChain and OpenAI clients pick it up automatically
    os.environ["OPENAI_API_KEY"] = api_key

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        if view in _QUIZ_AUX_VIEWS:
            if st.button("← Back to Quiz", width="stretch"):
                set_query_params(
                    client=client_id,
                    quiz=quiz_id,
                    csig=csig or sign_client(client_id),
                    sig=sig or sign_state(client_id, quiz_id),
                )
                st.rerun()
        else:
            if st.button("❓ How to use", width="stretch"):
                set_query_params(
                    client=client_id,
                    quiz=quiz_id,
                    view="howto",
                    csig=csig or sign_client(client_id),
                    sig=sig or sign_state(client_id, quiz_id),
                )
                st.rerun()
            if st.button("📒 Error Notebook", width="stretch"):
                set_query_params(
                    client=client_id,
                    quiz=quiz_id,
                    view="errors",
                    csig=csig or sign_client(client_id),
                    sig=sig or sign_state(client_id, quiz_id),
                )
                st.rerun()
            if st.button("📊 Data analysis", width="stretch"):
                set_query_params(
                    client=client_id,
                    quiz=quiz_id,
                    view="analytics",
                    csig=csig or sign_client(client_id),
                    sig=sig or sign_state(client_id, quiz_id),
                )
                st.rerun()

        # In non-quiz views, sidebar should ONLY show "Back to Quiz".
        if view in _QUIZ_AUX_VIEWS:
            source_mode = "Upload files"
            files_mode = True
            uploaded_files = None
            website_urls: list[str] = []
        else:
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
                        width="stretch",
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

        if view not in _QUIZ_AUX_VIEWS and (
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
                    min(MAX_QUESTIONS_CAP, total_pages // 2),
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
                    min(MAX_QUESTIONS_CAP, total_web_pages // 2),
                )

            st.session_state["current_paths"] = processed_paths
            st.session_state["cleanup_paths"] = cleanup_paths

            if source_mode == "Upload files":
                st.session_state["_usage_upload_total_bytes"] = sum(
                    int(len(uf.getbuffer())) for uf in unique_uploads
                )
                st.session_state["_usage_material_quantity"] = len(processed_paths)
                st.session_state["_usage_material_source"] = "upload_files"
                st.session_state["_usage_web_text_chars"] = None
            else:
                st.session_state["_usage_upload_total_bytes"] = None
                st.session_state["_usage_material_quantity"] = len(web_blocks)
                st.session_state["_usage_material_source"] = "website_links"
                st.session_state["_usage_web_text_chars"] = len(
                    (st.session_state.get("_web_text") or "")
                )

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

            generation_mode = st.selectbox(
                "Quiz Generation Mode",
                ["Full", "Fast"],
                index=0,
                help=(
                    "Full: concept extraction + quiz generation + LLM grading verification.\n\n"
                    "Fast: skip extraction and extraction, slightly compromises quiz quality)."
                ),
            )
            fast_mode = generation_mode == "Fast"

            if source_mode == "Upload files":
                can_generate = bool(processed_paths)
            else:
                can_generate = bool(web_blocks)

            if not can_generate:
                st.info("Add valid material (files or fetchable URLs) to generate a quiz.")

            st.write("")
            generate_btn = st.button("Generate & Verify Quiz", type="primary", disabled=(not can_generate))

    # --- How-to-use view ---
    if view == "howto":
        render_how_to_use_view()
        return

    # --- Error Notebook view (all-time history) ---
    if view == "errors":
        render_error_notebook_view(client_id=client_id, quiz_id=quiz_id)
        return

    # --- Usage / cost analytics (all visitors, Supabase) ---
    if view == "analytics":
        render_data_analysis_view()
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

        _usage_log_err = st.session_state.get("_usage_record_error")
        if _usage_log_err:
            st.error(
                f"**Usage was not saved to Supabase** (last run). "
                f"{_usage_log_err}"
            )
            if st.button("Dismiss", key="dismiss_usage_log_error"):
                st.session_state.pop("_usage_record_error", None)
                st.rerun()

        # Dedicated slot so workflow progress is always visible in the same place.
        workflow_slot = st.container()

        if generate_btn:
            _rl = check_daily_generation_allowed()
            if not _rl.allowed:
                st.error(_rl.message)
                st.stop()

            st.session_state["workflow_status_label"] = None
            st.session_state["workflow_status_lines"] = []
            st.session_state["workflow_running"] = True
            quiz_id = uuid.uuid4().hex
            set_query_params(
                client=client_id,
                quiz=quiz_id,
                csig=csig or sign_client(client_id),
                sig=sign_state(client_id, quiz_id),
            )

            start_time = time.time()
            with workflow_slot:
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

                concepts: list[str] = []
                ext_usage: dict = {}
                if fast_mode:
                    log_line("Fast mode: skipping concept extraction…")
                    st.session_state["workflow_token_usage_extraction"] = {}
                else:
                    log_line("Extracting core concepts...")
                    extractor = create_extraction_chain(return_usage=True)
                    concepts_resp, ext_usage = extractor(
                        {
                            "file_ids": oai_file_ids,
                            "web_context": st.session_state.get("_web_text", ""),
                        }
                    )
                    st.session_state["workflow_token_usage_extraction"] = ext_usage
                    concepts = concepts_resp.get("concepts") or []
                    if not concepts:
                        raise ValueError(
                            "Failed to extract concepts from the provided materials (website may be unreadable)."
                        )

                log_line(f"Generating {num_questions} questions...")
                generator = create_generation_chain(
                    num_questions, scenario_pct=scenario_pct, return_usage=True
                )
                quiz_data, gen_usage = generator(
                    {
                        "file_ids": oai_file_ids,
                        "concepts_list": "" if fast_mode else ", ".join(concepts),
                        "web_context": st.session_state.get("_web_text", ""),
                    }
                )
                st.session_state["workflow_token_usage_generation"] = gen_usage
                log_line("Running output safety guard...")
                quiz_data = run_quiz_output_guard(quiz_data)
                quiz_data = validate_quiz_shape(quiz_data, num_questions)

                report: dict | None = None
                vrf_usage: dict = {}
                if fast_mode:
                    log_line("Fast mode: skipping LLM grading verification…")
                    report = {
                        "passed_constraints": True,
                        "constraint_score": 1.0,
                        "constraint_feedback": [
                            "Fast mode enabled: skipped LLM grading verification.",
                            "Schema validation and output guard still ran.",
                        ],
                        "fidelity_score": None,
                        "pedagogical_score": None,
                        "evaluator_reasoning": "Skipped in Fast mode.",
                    }
                    st.session_state["workflow_token_usage_verification"] = {}
                else:
                    log_line("Running quiz verification checks...")
                    report, vrf_usage = verify_quiz(concepts, quiz_data, num_questions, return_usage=True)
                    st.session_state["workflow_token_usage_verification"] = vrf_usage

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
                    quiz_submitted=st.session_state.get("_quiz_submitted"),
                    current_quiz_score=st.session_state.get("_current_quiz_score"),
                    workflow_status_label=st.session_state.get("workflow_status_label"),
                    workflow_status_lines=st.session_state.get("workflow_status_lines") or [],
                )

                elapsed_time = time.time() - start_time
                st.session_state["generation_time"] = elapsed_time
                st.session_state["workflow_status_label"] = f"Workflow complete in {elapsed_time:.1f} secs"
                ext_tokens = st.session_state.get("workflow_token_usage_extraction") or {}
                gen_tokens = st.session_state.get("workflow_token_usage_generation") or {}
                vrf_tokens = st.session_state.get("workflow_token_usage_verification") or {}


                def _as_int(v) -> int | None:
                    try:
                        if v is None:
                            return None
                        return int(v)
                    except Exception:
                        return None

                def _split_tokens_precise(u: dict) -> tuple[int | None, int | None, int | None]:
                    """
                    Return (input_tokens, cached_input_tokens, output_tokens) when available.

                    Supports multiple usage metadata shapes emitted by OpenAI / LangChain.
                    """
                    if not isinstance(u, dict):
                        return None, None, None

                    # Common fields
                    input_tokens = (
                        u.get("input_tokens")
                        or u.get("prompt_tokens")
                        or u.get("prompt")
                        or u.get("input")
                        or None
                    )
                    output_tokens = (
                        u.get("output_tokens")
                        or u.get("completion_tokens")
                        or u.get("completion")
                        or u.get("output")
                        or None
                    )

                    # Cached input tokens can appear in nested detail objects.
                    cached_tokens = None
                    details = (
                        u.get("input_tokens_details")
                        or u.get("prompt_tokens_details")
                        or u.get("input_details")
                        or u.get("prompt_details")
                        or None
                    )
                    if isinstance(details, dict):
                        cached_tokens = (
                            details.get("cached_tokens")
                            or details.get("cache_read_tokens")
                            or details.get("cached")
                            or None
                        )

                    return _as_int(input_tokens), _as_int(cached_tokens) or 0, _as_int(output_tokens)

                def _estimate_cost_precise(model: str, usage: dict) -> tuple[float | None, dict]:
                    """
                    Compute cost using input + cached_input + output token buckets.
                    Returns (cost_or_none, breakdown_dict).
                    """
                    pricing = MODEL_PRICING_USD_PER_1K.get(model) or {}
                    in_rate = pricing.get("input")
                    cached_rate = pricing.get("cached_input")
                    out_rate = pricing.get("output")
                    if in_rate is None or cached_rate is None or out_rate is None:
                        return None, {}

                    in_toks, cached_toks, out_toks = _split_tokens_precise(usage)
                    if in_toks is None or out_toks is None or cached_toks is None:
                        return None, {}

                    # If cached tokens are reported, they are a subset of input tokens.
                    # Bill non-cached input at input rate and cached part at cached rate.
                    cached_toks = max(0, int(cached_toks))
                    non_cached_in = max(0, int(in_toks) - cached_toks)

                    cost = (non_cached_in / 1000.0) * float(in_rate)
                    cost += (cached_toks / 1000.0) * float(cached_rate)
                    cost += (int(out_toks) / 1000.0) * float(out_rate)

                    return float(cost), {
                        "input_tokens": int(in_toks),
                        "cached_input_tokens": int(cached_toks),
                        "output_tokens": int(out_toks),
                    }

                ext_cost, ext_bd = _estimate_cost_precise(QUIZZLY_MODEL, ext_tokens)
                gen_cost, gen_bd = _estimate_cost_precise(QUIZZLY_MODEL, gen_tokens)
                vrf_cost, vrf_bd = _estimate_cost_precise(QUIZZLY_MODEL, vrf_tokens)
                _total_for_db: float | None = None
                # In Fast mode, extraction/verif may be skipped. We still want a precise
                # total for the steps that have usage metadata.
                if gen_cost is None:
                    st.session_state["workflow_status_lines"].append(
                        "Estimated cost — N/A (set MODEL_PRICING_USD_PER_1K in quizzly_config.py)"
                    )
                else:
                    total_cost = float(gen_cost) + float(ext_cost or 0.0) + float(vrf_cost or 0.0)
                    _total_for_db = total_cost
                    st.session_state["workflow_status_lines"].append(
                        f"Estimated cost — total: ${total_cost:.4f}"
                    )
                    st.session_state["workflow_status_lines"].append(
                        "Cost breakdown (tokens: input/cached/output) — "
                        f"extraction: {ext_bd.get('input_tokens','-')}/{ext_bd.get('cached_input_tokens','-')}/{ext_bd.get('output_tokens','-')}, "
                        f"generation: {gen_bd.get('input_tokens','?')}/{gen_bd.get('cached_input_tokens','?')}/{gen_bd.get('output_tokens','?')}, "
                        f"verification: {vrf_bd.get('input_tokens','-')}/{vrf_bd.get('cached_input_tokens','-')}/{vrf_bd.get('output_tokens','-')}"
                    )
                ei_t, ei_c, ei_o = token_triple_from_breakdown(ext_bd)
                gi_t, gi_c, gi_o = token_triple_from_breakdown(gen_bd)
                vi_t, vi_c, vi_o = token_triple_from_breakdown(vrf_bd)
                _usage_log = QuizGenerationUsageFields(
                    estimated_cost_usd=_total_for_db,
                    num_questions=int(num_questions),
                    generation_mode="fast" if fast_mode else "full",
                    material_source=st.session_state.get("_usage_material_source"),
                    material_quantity=st.session_state.get("_usage_material_quantity"),
                    upload_total_bytes=st.session_state.get("_usage_upload_total_bytes"),
                    web_text_chars=st.session_state.get("_usage_web_text_chars"),
                    ext_input_tokens=ei_t,
                    ext_cached_input_tokens=ei_c,
                    ext_output_tokens=ei_o,
                    gen_input_tokens=gi_t,
                    gen_cached_input_tokens=gi_c,
                    gen_output_tokens=gi_o,
                    vrf_input_tokens=vi_t,
                    vrf_cached_input_tokens=vi_c,
                    vrf_output_tokens=vi_o,
                    generation_duration_sec=float(elapsed_time),
                )
                _rl_err = record_successful_generation(
                    st.session_state.get("_quizzly_user_ip_id"),
                    usage=_usage_log,
                )
                if _rl_err:
                    st.session_state["_usage_record_error"] = _rl_err
                    st.session_state["workflow_status_lines"].append(
                        f"Warning: could not record usage in Supabase ({_rl_err})."
                    )
                    st.warning(
                        f"**Usage was not saved to the database** (quiz still works). "
                        f"Details: {_rl_err}"
                    )
                    if debug_enabled:
                        st.info("Set DEBUG=1 in secrets for more context, or check Streamlit / Supabase logs.")
                else:
                    st.session_state.pop("_usage_record_error", None)
                    st.session_state["workflow_status_lines"].append(
                        "Usage saved: one row in Supabase `quiz_generation_usage`."
                    )
                try:
                    live_status.update(
                        label=st.session_state["workflow_status_label"], state="complete", expanded=False
                    )
                except Exception:
                    pass
                st.session_state["workflow_running"] = False
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
                st.session_state["workflow_running"] = False
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
            with workflow_slot:
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
                            quiz_submitted=st.session_state.get("_quiz_submitted"),
                            current_quiz_score=st.session_state.get("_current_quiz_score"),
                            workflow_status_label=st.session_state.get("workflow_status_label"),
                            workflow_status_lines=st.session_state.get("workflow_status_lines") or [],
                        )

                _btn_l, _btn_m, _btn_r = st.columns([1, 1, 1])
                with _btn_m:
                    submitted = st.form_submit_button("Submit Answers", width="stretch")

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
                            quiz_submitted=st.session_state.get("_quiz_submitted"),
                            current_quiz_score=st.session_state.get("_current_quiz_score"),
                            workflow_status_label=st.session_state.get("workflow_status_label"),
                            workflow_status_lines=st.session_state.get("workflow_status_lines") or [],
                        )
                    st.rerun()

    with col2:
        with st.container(border=True):
            title_col, redo_col = st.columns([2, 1], gap="small", vertical_alignment="center")
            with title_col:
                st.subheader("Quiz Score")
            with redo_col:
                redo_clicked = st.button("Redo Quiz", width="stretch")

            if redo_clicked:
                # Reset quiz-taking state but keep the generated quiz + verification report.
                quiz = st.session_state.get("quiz_data") or {}
                for q in quiz.get("questions", []) or []:
                    qid = q.get("id")
                    if qid is None:
                        continue
                    st.session_state.pop(f"q_{qid}", None)

                st.session_state["_persisted_answers"] = {}
                st.session_state["_last_autosave_hash"] = None
                st.session_state["_last_graded_hash"] = None
                st.session_state["_quiz_submitted"] = False
                st.session_state["_current_quiz_score"] = None
                st.session_state["_error_notebook_current"] = []

                quiz_id_now = (qp.get("quiz") or "").strip()
                if quiz_id_now:
                    persist_quiz_state(
                        client_id,
                        quiz_id_now,
                        quiz_data=st.session_state.get("quiz_data"),
                        verification_report=st.session_state.get("verification_report"),
                        error_notebook=[],
                        answers={},
                        quiz_submitted=False,
                        current_quiz_score=None,
                        workflow_status_label=st.session_state.get("workflow_status_label"),
                        workflow_status_lines=st.session_state.get("workflow_status_lines") or [],
                    )
                st.rerun()

            score = st.session_state.get("_current_quiz_score")
            if not score:
                st.write("N/A")
            else:
                correct, total = score
                st.write(f"{correct}/{total}")

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
                quiz_submitted=st.session_state.get("_quiz_submitted"),
                current_quiz_score=st.session_state.get("_current_quiz_score"),
                workflow_status_label=st.session_state.get("workflow_status_label"),
                workflow_status_lines=st.session_state.get("workflow_status_lines") or [],
            ),
        )

if __name__ == "__main__":
    main()

