import mimetypes
import os
import tempfile
import time
import traceback
import uuid

import streamlit as st
from openai import OpenAIError

from quizzly_bknd_fileprcs import (
    docx_to_pdf,
    fetch_website_text,
    image_to_pdf,
    pptx_to_pdf,
    pseudo_pages_from_web_text,
)
from quizzly_bknd_quizvalidate import validate_quiz_shape
from quizzly_bknd_gnrt import setup_api, get_page_count, create_extraction_chain, create_generation_chain
from quizzly_bknd_vrf import verify_quiz

from quizzly_config import (
    ANSWER_LETTERS,
    MAX_QUESTIONS_CAP,
    MAX_WEB_URL_SLOTS,
    MIN_QUESTIONS,
)

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

_PENDING_REMOVE_URL_INDEX = "_pending_remove_url_index"


def _apply_pending_web_url_removal() -> None:
    """Apply a URL row removal on a fresh run, before widgets mount (avoids Streamlit widget state errors)."""
    pending = st.session_state.pop(_PENDING_REMOVE_URL_INDEX, None)
    if pending is None:
        return
    n = min(int(st.session_state.web_url_slot_count), MAX_WEB_URL_SLOTS)
    if n <= 1 or pending < 0 or pending >= n:
        return
    vals = [str(st.session_state.get(f"web_url_{i}", "") or "") for i in range(n)]
    new_vals = vals[:pending] + vals[pending + 1 :]
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith("web_url_") and k[8:].isdigit():
            del st.session_state[k]
    st.session_state.web_url_slot_count = max(1, len(new_vals))
    for i, v in enumerate(new_vals):
        st.session_state[f"web_url_{i}"] = v
    # No rerun here; the widgets are created after this function returns.


def main():

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
            _apply_pending_web_url_removal()
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
                        st.session_state[_PENDING_REMOVE_URL_INDEX] = i
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
                seen_names = set()
                unique_uploads = []
                dup_names = []
                for uf in uploaded_files:
                    name = (os.path.basename(uf.name) or "upload").lower()
                    if name in seen_names:
                        dup_names.append(os.path.basename(uf.name))
                        continue
                    seen_names.add(name)
                    unique_uploads.append(uf)
                if dup_names:
                    st.warning("Duplicate file name(s) detected and will be ignored: " + ", ".join(dup_names))

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
                max_questions = max(MIN_QUESTIONS, min(MAX_QUESTIONS_CAP, total_pages // 2))

            else:
                st.session_state.current_paths = []
                for url in website_urls:
                    try:
                        ok, chunk = fetch_website_text(url)
                        if ok and chunk:
                            web_blocks.append((url, chunk))
                        else:
                            web_fetch_errors.append(
                                f"{url}: too little readable text (try a direct article, not a search results page)."
                            )
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

                        log_line(f"Uploading: {os.path.basename(fp)} | MIME: {mime_type}")

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
                    quiz_data = validate_quiz_shape(quiz_data, num_questions)

                    log_line("Running quiz verification checks...")
                    report = verify_quiz(concepts, quiz_data, num_questions)

                    st.session_state.verification_report = report
                    st.session_state.quiz_data = quiz_data

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
                for i, q in enumerate(st.session_state.quiz_data.get("questions", [])):
                    # Add the Difficulty badge to the question header
                    difficulty = q.get('difficulty', 'Unrated')
                    st.markdown(f"**{i+1}. {q['question_text']}** *(Difficulty: {difficulty})*")
                    
                    options = q.get("options", [])
                    user_answers[q['id']] = st.radio(
                        "Select an option:",
                        options=range(len(options)),
                        format_func=lambda idx: options[idx],
                        key=f"q_{q['id']}",
                        index=None,
                    )
                    st.write("---")
                
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

    # --- View 2: Error Notebook Right Panel ---
    with col2:
        with st.container(
            border=True,
            height="stretch",
            width="stretch",
            key="quizzly_error_notebook",
        ):
            st.header("📓 Error Notebook")
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
                    st.rerun()

if __name__ == "__main__":
    main()