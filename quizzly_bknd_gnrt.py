import os
import PyPDF2
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser


def setup_api():
    """Validates the API key and returns the native OpenAI client."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Please set it in your environment.")
    return OpenAI(api_key=api_key)

def get_page_count(file_path):
    """Calculates the number of pages in the uploaded PDF document."""
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == '.pdf':
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                return len(reader.pages)
        else:
            return 1 # Fallback for non-PDFs natively supported by OpenAI
            
    except Exception as e:
        print(f"Warning: Could not read PDF page count: {e}")
        return 1

def process_link(url, temp_dir):
    """Extracts text from a website link and saves it as a txt file."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        link_path = os.path.join(temp_dir, "website_content.txt")
        with open(link_path, "w", encoding="utf-8") as f:
            f.write(text)
        return link_path
    except Exception as e:
        print(f"Failed to process website link: {e}")
        return None

def create_extraction_chain():
    """Extracts the core concepts from the document."""
    # Updated to gpt-5.4-mini and removed temperature parameter
    llm = ChatOpenAI(model="gpt-5.4-mini")
    
    system_instructions = """### ROLE
You are a Meta-Expert Analyst. Identify the most critical concepts from a document suitable for high-level testing.
### OUTPUT FORMAT
Return a simple JSON list of strings: {"concepts": ["concept1", "concept2"]}"""

    def build_extraction_msg(inputs):
        content = [{"type": "text", "text": "Extract the core concepts from the provided materials."}]
        
        # Add File IDs if they exist
        for file_id in inputs.get("file_ids", []):
            content.append({"type": "file", "file": {"file_id": file_id}})
            
        # Add Website Text directly if it exists
        if inputs.get("web_context"):
            content.append({"type": "text", "text": f"Web Content: {inputs['web_context']}"})
            
        return [
            SystemMessage(content=system_instructions),
            HumanMessage(content=content)
        ]

    return build_extraction_msg | llm | JsonOutputParser()


def create_generation_chain(num_questions):
    """Generates the quiz using the dynamically injected question count."""
    # Updated to gpt-5.4-mini and removed temperature parameter
    llm = ChatOpenAI(model="gpt-5.4-mini", model_kwargs={"response_format": {"type": "json_object"}})
    parser = JsonOutputParser()
    
    # Calculate exact distribution favoring Easy -> Medium -> Hard
    base = num_questions // 3
    remainder = num_questions % 3
    
    easy_qty = base + (1 if remainder >= 1 else 0)
    medium_qty = base + (1 if remainder == 2 else 0)
    hard_qty = base
    
    # The prompt now receives exact integers instead of fractions
    system_instructions = f"""You are a Senior Instructional Designer and Subject Matter Expert. Your goal is to create active recall assessment materials that help students master concepts from their study documents.

### OBJECTIVE
Analyze the provided user text/document and generate a multiple-choice quiz. The quiz must assess the user's understanding of the core concepts found strictly within the text.

### PEDAGOGICAL GUIDELINES
1. **Difficulty Distribution:** You must generate exactly {num_questions} questions in total, distributed EXACTLY as follows:
   - {easy_qty} Easy questions (direct recall of facts).
   - {medium_qty} Medium questions (application of concepts).
   - {hard_qty} Hard questions (analysis/evaluation based on Bloom's Taxonomy).
2. **Question Styles (Conceptual vs. Scenario):** Within each difficulty tier, strive for a 50/50 split between standard conceptual questions and scenario-based questions. 
   - **Conceptual Questions:** Ask directly about definitions, theories, or facts stated in the text.
   - **Scenario-Based Questions:** Present a brief, hypothetical story, case study, or practical situation where the user must actively apply the document's concepts to deduce the correct answer.
3. **Ordering:** You MUST present the questions strictly in ascending order of difficulty (Easy -> Medium -> Hard). Assign the correct "difficulty" label to each.
4. **Distractors:** The wrong options (distractors) must be plausible but clearly incorrect based on the text. Avoid obvious joke answers.
5. **Explanation Formatting:** The "explanation" field is critical. To maximize readability, you MUST separate the explanation of the correct answer and the breakdowns of each wrong option using double newlines (\n\n).

### STRICT CONSTRAINTS
1. **Source Truth:** The logic to answer the question MUST come strictly from the provided document. You are encouraged to invent fictional characters or hypothetical scenarios for the questions, but the core academic concepts and correct answers must be 100% grounded in the text. 
2. **Output Format:** You must output valid JSON only. Do not output conversational text before or after the JSON.

### FEW-SHOT EXAMPLE
{{
 "quiz_title": "Topic Summary",
 "questions": [
    {{
      "id": 1,
      "difficulty": "Easy",
      "question_text": "According to the document, why is active recall preferred over passive reading?",
      "options": [
        "A) It requires less mental effort.",
        "B) It strengthens neural pathways through testing.",
        "C) It allows students to read faster.",
        "D) It eliminates the need for textbooks."
      ],
      "correct_option": "B",
      "explanation": "The text states that self-testing (active recall) strengthens neural pathways.\n\nOption A is incorrect because active recall explicitly requires more mental effort.\n\nOption C is incorrect because the text focuses on retention, not reading speed.\n\nOption D is incorrect as textbooks are still needed."
    }},
    {{
      "id": 2,
      "difficulty": "Medium",
      "question_text": "Marcus is studying for a history exam. He spends three hours reading his textbook cover-to-cover and highlighting text, but does not take any practice tests. Based on the document, what is the most likely outcome of his study strategy?",
      "options": [
        "A) He will have deep, long-term retention of the dates and events.",
        "B) He may suffer from the 'illusion of competence' and perform poorly on the actual exam.",
        "C) He is utilizing the most effective pedagogical framework available.",
        "D) He will avoid context window degradation."
      ],
      "correct_option": "B",
      "explanation": "The document explains that passive reading without feedback loops leads to the 'illusion of competence' where a student feels prepared but fails the exam.\n\nOption A is incorrect because passive reading is cited as the least effective method for long-term retention.\n\nOption C is incorrect because active recall, not passive reading, is the effective framework.\n\nOption D is incorrect as context windows relate to AI, not human studying."
    }}
  ]
}}

### FINAL INSTRUCTION
Generate the JSON format quiz now based on the attached document.
"""

    def build_generation_msg(inputs):
        file_ids = inputs.get("file_ids", [])
        concepts = inputs.get("concepts_list", "")
        web_text = inputs.get("web_context", "")
        
        content = [{"type": "text", "text": f"Focus the quiz strictly on these extracted core concepts: {concepts}"}]
        
        for file_id in file_ids:
            content.append({"type": "file", "file": {"file_id": file_id}})
            
        if web_text:
            content.append({"type": "text", "text": f"Additional Web Source Material: {web_text}"})
            
        return [
            SystemMessage(content=system_instructions),
            HumanMessage(content=content)
        ]
        
    return build_generation_msg | llm | parser