import os
import streamlit as st
import tempfile
import time
import traceback
from openai import OpenAIError

# Import backend modules
from quizzly_bknd_gnrt import setup_api, get_page_count, create_extraction_chain, create_generation_chain
from quizzly_bknd_vrf import verify_quiz

st.set_page_config(page_title="Quizzly", page_icon="🧠", layout="wide")

# Initialize Session States for stateful UI
if 'quiz_data' not in st.session_state:
    st.session_state.quiz_data = None
if 'error_notebook' not in st.session_state:
    st.session_state.error_notebook = []
if 'verification_report' not in st.session_state:
    st.session_state.verification_report = None

def main():
    st.title("🧠 Quizzly: Automated Quiz Generator")
    st.markdown("Transform passive reading into active mastery. Upload a document to generate a verified, targeted quiz based on Bloom's Taxonomy.")

    # API Key Check via Streamlit Secrets
    if "OPENAI_API_KEY" not in st.secrets:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return
    
    # Set it as an environment variable so LangChain and OpenAI clients pick it up automatically
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        st.header("Document Upload")
        uploaded_file = st.file_uploader("Upload study material (PDF, PPTX, DOCX)", type=["pdf", "pptx", "docx"])
        
        num_questions = 1 # Default
        
        if uploaded_file:
            # Save temp file to read pages and send to OpenAI
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, uploaded_file.name)
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
                
            page_count = get_page_count(temp_path)
            max_questions = max(1, page_count // 2)
            
            st.success(f"Document Loaded: {page_count} pages detected.")
            st.info(f"To maintain context quality, max questions is set to {max_questions}.")
            
            st.header("Quiz Settings")
            num_questions = st.number_input(
                "Number of Questions", 
                min_value=1, 
                max_value=max_questions, 
                value=min(3, max_questions)
            )
            
            generate_btn = st.button("Generate & Verify Quiz", type="primary")

    # --- Main Area: Processing & Display ---
    if uploaded_file and generate_btn:
        with st.status("Processing Document Workflow...", expanded=True) as status:
            try:
                client = setup_api()
                
                st.write("Uploading to secure environment...")
                oai_file = client.files.create(file=open(temp_path, "rb"), purpose="user_data")
                
                st.write("Extracting core concepts...")
                extractor = create_extraction_chain()
                concepts = extractor.invoke({"file_id": oai_file.id})["concepts"]
                
                st.write(f"Generating {num_questions} questions via LangChain...")
                generator = create_generation_chain(num_questions)
                quiz_data = generator.invoke({
                    "file_id": oai_file.id,
                    "concepts_list": ", ".join(concepts)
                })
                
                st.write("Running backend verification checks...")
                report = verify_quiz(concepts, quiz_data, num_questions)
                
                st.session_state.verification_report = report
                st.session_state.quiz_data = quiz_data
                
                # Cleanup
                os.remove(temp_path)
                status.update(label="Workflow Complete!", state="complete", expanded=False)
                
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

    # --- View 1: Quiz Execution ---
    # Added gap="large" for better spacing between the quiz and the notebook panel
    col1, col2 = st.columns([2, 1], gap="large")
    
    with col1:
        if st.session_state.quiz_data:
            # Show verification results in an expander
            if st.session_state.verification_report:
                report = st.session_state.verification_report
                with st.expander("🛠️ View Verification Report"):
                    st.write(f"**Structural Checks Passed:** {report['passed_constraints']}")
                    st.write(f"**Task Fidelity Score:** {report['fidelity_score']}/5")
                    st.write(f"**Evaluator Reasoning:** {report['fidelity_reasoning']}")

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
                    with st.expander(f"Review Item {idx + 1}"):
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