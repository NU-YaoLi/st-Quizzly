import os
import streamlit as st
import tempfile
import time

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
    st.title("🧠 Quizzly: Active Recall Generator")
    st.markdown("Transform passive reading into active mastery. Upload a document to generate a verified, targeted quiz based on Bloom's Taxonomy.")

    # API Key Check
    if not os.getenv('OPENAI_API_KEY'):
        st.error("⚠️ Please set the OPENAI_API_KEY environment variable.")
        return

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        st.header("1. Document Upload")
        uploaded_file = st.file_uploader("Upload PDF material", type=["pdf"])
        
        num_questions = 3 # Default
        
        if uploaded_file:
            # Save temp file to read pages and send to OpenAI
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, uploaded_file.name)
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
                
            page_count = get_page_count(temp_path)
            max_questions = max(1, page_count // 2)
            
            st.success(f"Document Loaded: {page_count} pages detected.")
            st.info(f"To maintain context quality, max questions is set to Page Count / 2 ({max_questions}).")
            
            st.header("2. Quiz Settings")
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
                
            except Exception as e:
                status.update(label="Error occurred", state="error")
                st.error(str(e))

    # --- View 1: Quiz Execution ---
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.session_state.quiz_data:
            st.subheader(st.session_state.quiz_data.get("quiz_title", "Assessment"))
            
            # Show verification results in an expander
            if st.session_state.verification_report:
                report = st.session_state.verification_report
                with st.expander("🛠️ View Verification Report"):
                    st.write(f"**Structural Checks Passed:** {report['passed_constraints']}")
                    st.write(f"**Task Fidelity Score:** {report['fidelity_score']}/5")
                    st.write(f"**Evaluator Reasoning:** {report['fidelity_reasoning']}")

            st.divider()
            
            # Interactive Quiz
            with st.form("quiz_form"):
                user_answers = {}
                for i, q in enumerate(st.session_state.quiz_data.get("questions", [])):
                    st.markdown(f"**{i+1}. {q['question_text']}**")
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
                        
                        if user_letter == q['correct_option']:
                            st.success(f"**Q{q['id']}:** Correct! ✅")
                        else:
                            st.error(f"**Q{q['id']}:** Incorrect. The answer is {q['correct_option']}.")
                            st.info(f"**Explanation:** {q['explanation']}")
                            
                            # Add to Error Notebook if not already there
                            error_entry = {
                                "question": q['question_text'],
                                "user_wrong": user_ans,
                                "explanation": q['explanation']
                            }
                            if error_entry not in st.session_state.error_notebook:
                                st.session_state.error_notebook.append(error_entry)

    # --- View 2: Error Notebook Sidebar ---
    with col2:
        st.header("📓 Error Notebook")
        st.markdown("Review your mistakes to reinforce learning.")
        if not st.session_state.error_notebook:
            st.info("No errors logged yet. Great job!")
        else:
            for idx, error in enumerate(st.session_state.error_notebook):
                with st.expander(f"Review Item {idx + 1}"):
                    st.write(f"**Q:** {error['question']}")
                    st.write(f"❌ *You answered: {error['user_wrong']}*")
                    st.write(f"💡 **Correction:** {error['explanation']}")
                    
            if st.button("Clear Notebook"):
                st.session_state.error_notebook = []
                st.rerun()

if __name__ == "__main__":
    main()
