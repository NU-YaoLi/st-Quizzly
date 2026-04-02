import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser

def code_based_grading(quiz_data, expected_count):
    """Verifies strict constraints (JSON format and Question Count)."""
    score = 0
    feedback = []

    if isinstance(quiz_data, dict) and "questions" in quiz_data:
        score += 0.5
        feedback.append("Valid JSON schema detected.")
    else:
        feedback.append("Invalid JSON structure.")

    num_questions = len(quiz_data.get("questions", []))
    if num_questions == expected_count:
        score += 0.5
        feedback.append(f"Generated exactly {expected_count} questions.")
    else:
        feedback.append(f"Expected {expected_count} questions, got {num_questions}.")

    return score, feedback

def llm_based_grading(concepts, quiz_data):
    """Evaluates Task Fidelity to ensure questions match the extracted concepts."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0.0, model_kwargs={"response_format": {"type": "json_object"}}) 
    
    eval_prompt = """You are an expert curriculum evaluator. 
    Review the following multiple-choice quiz and the core concepts it was supposed to cover.
    Rate the 'Task Fidelity' on a scale of 1 to 5.
    Output a JSON object strictly with the keys: "score" (integer) and "reasoning" (string)."""
    
    human_content = f"CONCEPTS:\n{concepts}\n\nQUIZ:\n{json.dumps(quiz_data)}"
    
    messages = [
        SystemMessage(content=eval_prompt),
        HumanMessage(content=human_content)
    ]
    
    parser = JsonOutputParser()
    chain = llm | parser
    return chain.invoke(messages)

def verify_quiz(concepts, quiz_data, expected_count):
    """Runs all verifications and returns a report dictionary."""
    code_score, code_feedback = code_based_grading(quiz_data, expected_count)
    llm_eval = llm_based_grading(concepts, quiz_data)
    
    return {
        "passed_constraints": code_score == 1.0,
        "constraint_feedback": code_feedback,
        "fidelity_score": llm_eval.get('score'),
        "fidelity_reasoning": llm_eval.get('reasoning')
    }
