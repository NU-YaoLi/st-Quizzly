import os
import streamlit as st
import tempfile
import time
import traceback
from openai import OpenAIError

# Cleaned imports: no file conversion dependencies needed
from quizzly_bknd_gnrt import setup_api, get_page_count, create_extraction_chain, create_generation_chain, process_link
from quizzly_bknd_vrf import verify_quiz

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

def main():
    st.title("📖 Quizzly: Automated Quiz Generator")
    st.markdown("Transform passive reading into active mastery. Upload documents or links to generate a verified, targeted quiz based on Bloom's Taxonomy.")

    # API Key Check via Streamlit Secrets
    if "OPENAI_API_KEY" not in st.secrets:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return
    
    # Set it as an environment variable so LangChain and OpenAI clients pick it up automatically
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        st.header("Study Materials")
        
        # Support for multiple files and various types natively
        uploaded_files = st.file_uploader(
            "Upload files (PDF, DOCX, PPTX, TXT, PNG)", 
            type=["pdf", "docx", "pptx", "txt", "png"], 
            accept_multiple_files=True
        )
        
        # Support for a website link
        website_link = st.text_input("Or enter a website link")
        
        num_questions = 1 # Default
        generate_btn = False # Initialize button state
        
        if uploaded_files or website_link:
            total_pages = 0
            temp_dir = tempfile.gettempdir()
            valid_paths = []
            
            # Process uploaded files directly
            if uploaded_files:
                for uf in uploaded_files:
                    temp_path = os.path.join(temp_dir, uf.name)
                    with open(temp_path, "wb") as f:
                        f.write(uf.getbuffer())
                    
                    # Store native path directly for OpenAI
                    valid_paths.append(temp_path)
                    total_pages += get_page_count(temp_path)
                    
            # Process website link
            if website_link:
                link_path = process_link(website_link, temp_dir)
                if link_path:
                    valid_paths.append(link_path)
                    total_pages += get_page_count(link_path)
                    
            # Fallback if page count couldn't be parsed
            if total_pages == 0:
                total_pages = 1
                
            max_questions = max(1, total_pages // 2)
            
            st.success(f"Materials Loaded: {len(valid_paths)} sources detected.")
            st.info(f"To maintain context quality, max questions is set to {max_questions}.")
            
            st.header("Quiz Settings")
            num_questions = st.number_input(
                "Number of Questions", 
                min_value=1, 
                max_value=max_questions, 
                value=min(3, max_questions)
            )
            
            # Store validated paths in session state for processing block
            st.session_state.current_paths = valid_paths
            generate_btn = st.button("Generate & Verify Quiz", type="primary")

    # --- Main Area: Processing & Display ---
    

    # --- View 1: Quiz Execution ---
    col1, col2 = st.columns([2, 1], gap="large")
    
    with col1:
        if (uploaded_files or website_link) and generate_btn:
            # 1. Start the timer right as the button is clicked
            start_time = time.time() 
            
            with st.status("Processing Document Workflow...", expanded=True) as status:
                try:
                    client = setup_api()
                    
                    st.write("Uploading to secure environment...")
                    oai_file_ids = []
                    for fp in st.session_state.current_paths:
                        # Files are uploaded natively to OpenAI without local conversion
                        oai_file = client.files.create(file=open(fp, "rb"), purpose="user_data")
                        oai_file_ids.append(oai_file.id)
                    
                    st.write("Extracting core concepts...")
                    extractor = create_extraction_chain()
                    concepts = extractor.invoke({"file_ids": oai_file_ids})["concepts"]
                    
                    st.write(f"Generating {num_questions} questions via LangChain...")
                    generator = create_generation_chain(num_questions)
                    quiz_data = generator.invoke({
                        "file_ids": oai_file_ids,
                        "concepts_list": ", ".join(concepts)
                    })
                    
                    st.write("Running question verification checks...")
                    report = verify_quiz(concepts, quiz_data, num_questions)
                    
                    st.session_state.verification_report = report
                    st.session_state.quiz_data = quiz_data
                    
                    # Cleanup
                    for fp in st.session_state.current_paths:
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
                    
                    # 3. Calculate elapsed time and update the status label dynamically
                    elapsed_time = time.time() - start_time
                    st.session_state.generation_time = elapsed_time
                    status.update(label=f"Workflow Complete in {elapsed_time:.1f} secs!", state="complete", expanded=False)
                    
                except OpenAIError as e:
                    # Catches issues specifically related to OpenAI (Auth, Rate Limits, Timeouts)
                    status.update(label="OpenAI API Error", state="error")
                    st.error("There was a problem communicating with OpenAI. Check your API key, billing limits, or network connection.")
                    st.info(f"**Details:** {str(e)}")
                    
                except ValueError as e:
                    # Catches missing environment variables or bad inputs
                    status.update(label="Configuration Error", state="error")
                    st.error("A configuration or input value error occurred.")
                    st.info(f"**Details:** {str(e)}")
                    
                except Exception as e:
                    # The fallback for any other unexpected Python or LangChain errors
                    error_type = type(e).__name__
                    status.update(label=f"System Error: {error_type}", state="error")
                    st.error(f"The workflow failed due to an unexpected {error_type}.")
                    st.info(f"**Details:** {str(e)}")
                    
                    # Hidden expander for developers to see the exact line number of the crash
                    with st.expander("🛠️ Show Detailed Stack Trace (For Debugging)"):
                        st.code(traceback.format_exc(), language="python")

        if st.session_state.quiz_data:
            # Show verification results in an expander
            if st.session_state.verification_report:
                report = st.session_state.verification_report
                with st.expander("🛠️ View Comprehensive Verification Report"):
                    
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
                    
                    user_answers[q['id']] = st.radio(
                        "Select an option:", 
                        q['options'], 
                        key=f"q_{q['id']}", 
                        index=None
                    )
                    st.write("---")
                
                submitted = st.form_submit_button("Submit Answers")
                
                if submitted:
                    for q in st.session_state.quiz_data["questions"]:
                        user_ans = user_answers[q['id']]
                        
                        if not user_ans:
                            st.warning(f"Question {q['id']} was left blank.")
                            continue
                            
                        user_letter = user_ans[0] # Extracts "A", "B", etc.
                        
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
                                "user_wrong": user_ans,
                                "explanation": formatted_explanation
                            }
                            if error_entry not in st.session_state.error_notebook:
                                st.session_state.error_notebook.append(error_entry)

    # --- View 2: Error Notebook Right Panel ---
    with col2:
        # Wrap the entire right section in a bordered container with a set height
        with st.container(border=True, height=750):
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