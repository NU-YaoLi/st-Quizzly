import os
import streamlit as st
import tempfile
import time
import traceback
import subprocess
import requests
import uuid
from bs4 import BeautifulSoup
from openai import OpenAIError

# Import backend modules
from quizzly_bknd_gnrt import setup_api, get_total_page_count, create_extraction_chain, create_generation_chain
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

def convert_to_pdf(input_path, output_dir):
    """Converts DOCX/PPTX/TXT to PDF using headless LibreOffice."""
    try:
        subprocess.run([
            'libreoffice', '--headless', '--convert-to', 'pdf',
            input_path, '--outdir', output_dir
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        return os.path.join(output_dir, f"{base_name}.pdf")
    except Exception as e:
        raise RuntimeError(f"Document conversion failed. Error: {str(e)}")

def scrape_url_to_pdf(url, output_dir):
    """Fetches an article from a URL, extracts text, and converts it to a PDF."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, 'html.parser')
        # Remove noisy elements
        for script in soup(["script", "style", "nav", "footer"]):
            script.extract()
            
        text = soup.get_text(separator='\n', strip=True)
        
        unique_name = f"scraped_article_{uuid.uuid4().hex[:8]}.txt"
        temp_txt_path = os.path.join(output_dir, unique_name)
        
        with open(temp_txt_path, "w", encoding="utf-8") as f:
            f.write(text)
            
        return convert_to_pdf(temp_txt_path, output_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to extract content from URL. Error: {str(e)}")

def main():
    st.title("📖 Quizzly: Automated Quiz Generator")
    st.markdown("Transform passive reading into active mastery. Upload documents, images, or URLs to generate a verified, targeted quiz based on Bloom's Taxonomy.")

    if "OPENAI_API_KEY" not in st.secrets:
        st.error("⚠️ Please set the OPENAI_API_KEY in the Streamlit secrets.")
        return
    
    os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]

    # --- Sidebar: Upload & Settings ---
    with st.sidebar:
        st.header("Data Sources")
        
        uploaded_files = st.file_uploader(
            "Upload study materials", 
            type=["pdf", "docx", "pptx", "png"], 
            accept_multiple_files=True
        )
        
        article_url = st.text_input("Or enter an article URL:")
        
        num_questions = 1 
        generate_btn = False 
        temp_dir = tempfile.gettempdir()
        active_file_paths = []
        
        if uploaded_files or article_url:
            with st.spinner("Processing ingestion pipeline..."):
                
                # 1. Process local file uploads
                if uploaded_files:
                    for uploaded_file in uploaded_files:
                        ext = uploaded_file.name.lower().split('.')[-1]
                        safe_name = f"{uuid.uuid4().hex[:8]}_{uploaded_file.name}"
                        original_temp_path = os.path.join(temp_dir, safe_name)
                        
                        with open(original_temp_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                            
                        # Convert non-vision formats to PDF
                        if ext in ['docx', 'pptx']:
                            pdf_path = convert_to_pdf(original_temp_path, temp_dir)
                            active_file_paths.append(pdf_path)
                        else:
                            # PDF and PNG natively support vision extraction
                            active_file_paths.append(original_temp_path)
                
                # 2. Process external URL
                if article_url:
                    try:
                        pdf_path = scrape_url_to_pdf(article_url, temp_dir)
                        active_file_paths.append(pdf_path)
                    except Exception as e:
                        st.error(str(e))
                        st.stop()
                        
            total_pages = get_total_page_count(active_file_paths)
            max_questions = max(1, total_pages // 2)
            
            st.success(f"Sources Loaded: {len(active_file_paths)} file(s), {total_pages} total pages/images detected.")
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
    col1, col2 = st.columns([2, 1], gap="large")
    
    with col1:
        if (uploaded_files or article_url) and generate_btn:
            start_time = time.time() 
            
            with st.status("Processing Document Workflow...", expanded=True) as status:
                try:
                    client = setup_api()
                    
                    st.write("Uploading multiple files to secure environment...")
                    file_ids = []
                    for file_path in active_file_paths:
                        oai_file = client.files.create(file=open(file_path, "rb"), purpose="user_data")
                        file_ids.append(oai_file.id)
                    
                    st.write("Extracting core concepts...")
                    extractor = create_extraction_chain()
                    concepts = extractor.invoke({"file_ids": file_ids})["concepts"]
                    
                    st.write(f"Generating {num_questions} questions via LangChain...")
                    generator = create_generation_chain(num_questions)
                    quiz_data = generator.invoke({
                        "file_ids": file_ids,
                        "concepts_list": ", ".join(concepts)
                    })
                    
                    st.write("Running question verification checks...")
                    report = verify_quiz(concepts, quiz_data, num_questions)
                    
                    st.session_state.verification_report = report
                    st.session_state.quiz_data = quiz_data
                    
                    elapsed_time = time.time() - start_time
                    st.session_state.generation_time = elapsed_time
                    status.update(label=f"Workflow Complete in {elapsed_time:.1f} secs!", state="complete", expanded=False)
                    
                except Exception as e:
                    error_type = type(e).__name__
                    status.update(label=f"System Error: {error_type}", state="error")
                    st.error(f"The workflow failed due to an unexpected {error_type}.")
                    st.info(f"**Details:** {str(e)}")
                    with st.expander("🛠️ Show Detailed Stack Trace"):
                        st.code(traceback.format_exc(), language="python")

        if st.session_state.quiz_data:
            # ... (Keep all existing UI logic for the verification report and quiz rendering exactly the same) ...
            st.success("Quiz Generated Successfully!")
            st.json(st.session_state.quiz_data)

if __name__ == "__main__":
    main()