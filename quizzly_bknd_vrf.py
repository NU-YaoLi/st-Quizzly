import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.output_parsers import JsonOutputParser

def code_based_grading(quiz_data, expected_count):
    """
    Verifies strict constraints (Code-Based Grading).
    Checks JSON schema, question count, required keys, and option formatting.
    """
    score = 0
    max_score = 4.0 # Increased max score for more granular testing
    feedback = []

    # 1. Check valid JSON dictionary
    if isinstance(quiz_data, dict) and "questions" in quiz_data:
        score += 1.0
        feedback.append("Pass: Valid JSON root schema detected.")
    else:
        feedback.append("Fail: Invalid JSON structure. Missing 'questions' key.")
        return score, feedback # Fatal error, stop checking

    questions = quiz_data.get("questions", [])

    # 2. Check Question Count
    num_questions = len(questions)
    if num_questions == expected_count:
        score += 1.0
        feedback.append(f"Pass: Generated exactly {expected_count} questions.")
    else:
        feedback.append(f"Fail: Expected {expected_count} questions, got {num_questions}.")

    # 3. Check Required Keys per Question
    required_keys = {"id", "difficulty", "question_text", "options", "correct_option", "explanation"}
    keys_passed = True
    for q in questions:
        if not required_keys.issubset(q.keys()):
            keys_passed = False
            missing = required_keys - q.keys()
            feedback.append(f"Fail: Question {q.get('id', 'Unknown')} missing keys: {missing}")
            break
            
    if keys_passed:
        score += 1.0
        feedback.append("Pass: All questions contain required pedagogical keys.")

    # 4. Check Option Formatting (Must have 4 options, Correct option must be A, B, C, or D)
    options_passed = True
    valid_answers = ["A", "B", "C", "D"]
    for q in questions:
        if len(q.get("options", [])) != 4:
            options_passed = False
            feedback.append(f"Fail: Question {q.get('id')} does not have exactly 4 options.")
            break
            
        correct_opt = q.get("correct_option", "")
        # Check if the correct option is just the letter (e.g., "A", not "A) The answer")
        if correct_opt not in valid_answers:
            options_passed = False
            feedback.append(f"Fail: Question {q.get('id')} has invalid correct_option format: '{correct_opt}'")
            break

    if options_passed:
        score += 1.0
        feedback.append("Pass: All questions have 4 options and valid answer keys.")

    # Normalize score to a 0.0 - 1.0 scale
    final_normalized_score = score / max_score
    return final_normalized_score, feedback


def llm_based_grading(concepts, quiz_data):
    """
    Evaluates Task Fidelity and Pedagogical Quality using an LLM-as-a-judge.
    """
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0.0, model_kwargs={"response_format": {"type": "json_object"}}) 
    
    eval_prompt = """You are an expert curriculum evaluator and instructional design auditor. 
    Review the following multiple-choice quiz and the core concepts it was supposed to cover.
    
    Evaluate the quiz on two metrics, each on a scale of 1 to 5:
    1. 'task_fidelity': Do the questions accurately test the provided concepts without hallucinating outside information?
    2. 'pedagogical_value': Are the distractors (wrong answers) plausible? Do the questions align with Bloom's Taxonomy (requiring analysis/application, not just memorization)?
    
    Output a JSON object strictly with the keys:
    - "task_fidelity_score" (integer 1-5)
    - "pedagogical_score" (integer 1-5)
    - "reasoning" (string explaining the scores)
    """
    
    human_content = f"CONCEPTS:\n{concepts}\n\nQUIZ:\n{json.dumps(quiz_data)}"
    
    messages = [
        SystemMessage(content=eval_prompt),
        HumanMessage(content=human_content)
    ]
    
    parser = JsonOutputParser()
    chain = llm | parser
    return chain.invoke(messages)


def verify_quiz(concepts, quiz_data, expected_count):
    """
    Runs all verifications and returns a comprehensive report dictionary.
    """
    code_score, code_feedback = code_based_grading(quiz_data, expected_count)
    llm_eval = llm_based_grading(concepts, quiz_data)
    
    # Calculate an overall pass/fail boolean for the UI
    # We consider it passed if code grading is perfect and fidelity is >= 4
    passed = (code_score == 1.0) and (llm_eval.get('task_fidelity_score', 0) >= 4)
    
    return {
        "passed_constraints": passed,
        "constraint_score": code_score,
        "constraint_feedback": code_feedback,
        "fidelity_score": llm_eval.get('task_fidelity_score'),
        "pedagogical_score": llm_eval.get('pedagogical_score'),
        "evaluator_reasoning": llm_eval.get('reasoning')
    }