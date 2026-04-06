import os
import PyPDF2
from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser

def setup_api():
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Please set it in your environment.")
    return OpenAI(api_key=api_key)

def get_total_page_count(file_paths):
    """Calculates the total pages across multiple files (PDFs and PNGs)."""
    total_pages = 0
    for file_path in file_paths:
        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == '.pdf':
                with open(file_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    total_pages += len(reader.pages)
            elif ext == '.png':
                total_pages += 1 # An image counts as 1 page contextually
            else:
                total_pages += 1
        except Exception as e:
            print(f"Warning: Could not read page count for {file_path}: {e}")
            total_pages += 1
    return total_pages

def create_extraction_chain():
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.3)
    
    system_instructions = """### ROLE
You are a Meta-Expert Analyst. Identify the most critical concepts from the provided documents suitable for high-level testing.
### OUTPUT FORMAT
Return a simple JSON list of strings: {"concepts": ["concept1", "concept2"]}"""

    def build_extraction_msg(inputs):
        # Dynamically append multiple file_ids to the message content
        content_array = [{"type": "text", "text": "Extract the core concepts from these files."}]
        for file_id in inputs["file_ids"]:
            content_array.append({"type": "file", "file": {"file_id": file_id}})
            
        return [
            SystemMessage(content=system_instructions),
            HumanMessage(content=content_array)
        ]

    return build_extraction_msg | llm | JsonOutputParser()

def create_generation_chain(num_questions):
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5, model_kwargs={"response_format": {"type": "json_object"}})
    parser = JsonOutputParser()
    
    base = num_questions // 3
    remainder = num_questions % 3
    
    easy_qty = base + (1 if remainder >= 1 else 0)
    medium_qty = base + (1 if remainder == 2 else 0)
    hard_qty = base
    
    system_instructions = f"""You are a Senior Instructional Designer and Subject Matter Expert. Your goal is to create active recall assessment materials that help students master concepts from their study documents.

### OBJECTIVE
Analyze the provided user text/documents and generate a multiple-choice quiz. The quiz must assess the user's understanding of the core concepts found strictly within the texts.

### PEDAGOGICAL GUIDELINES
1. **Difficulty Distribution:** You must generate exactly {num_questions} questions in total, distributed EXACTLY as follows:
   - {easy_qty} Easy questions (direct recall of facts).
   - {medium_qty} Medium questions (application of concepts).
   - {hard_qty} Hard questions (analysis/evaluation based on Bloom's Taxonomy).
2. **Question Styles (Conceptual vs. Scenario):** Within each difficulty tier, strive for a 50/50 split between standard conceptual questions and scenario-based questions. 
3. **Ordering:** You MUST present the questions strictly in ascending order of difficulty (Easy -> Medium -> Hard). Assign the correct "difficulty" label to each.
4. **Distractors:** The wrong options (distractors) must be plausible but clearly incorrect based on the text. Avoid obvious joke answers.
5. **Explanation Formatting:** The "explanation" field is critical. To maximize readability, you MUST separate the explanation of the correct answer and the breakdowns of each wrong option using double newlines (\\n\\n).

### STRICT CONSTRAINTS
1. **Source Truth:** The logic to answer the question MUST come strictly from the provided documents. You are encouraged to invent fictional characters or hypothetical scenarios for the questions, but the core academic concepts and correct answers must be 100% grounded in the text. 
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
      "explanation": "The text states that self-testing (active recall) strengthens neural pathways.\\n\\nOption A is incorrect because active recall explicitly requires more mental effort.\\n\\nOption C is incorrect because the text focuses on retention, not reading speed.\\n\\nOption D is incorrect as textbooks are still needed."
    }}
  ]
}}

### FINAL INSTRUCTION
Generate the JSON format quiz now based on the attached documents.
"""

    def build_generation_msg(inputs):
        concepts = inputs["concepts_list"]
        
        # Dynamically append multiple file_ids alongside the concepts instruction
        content_array = [{"type": "text", "text": f"Focus the quiz strictly on these extracted core concepts: {concepts}"}]
        for file_id in inputs["file_ids"]:
            content_array.append({"type": "file", "file": {"file_id": file_id}})
            
        return [
            SystemMessage(content=system_instructions),
            HumanMessage(content=content_array)
        ]

    return build_generation_msg | llm | parser