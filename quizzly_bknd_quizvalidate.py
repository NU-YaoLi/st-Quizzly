from __future__ import annotations

from typing import Any

from quizzly_config import ANSWER_LETTERS


def validate_quiz_shape(quiz: Any, expected_count: int) -> dict:
    if not isinstance(quiz, dict):
        raise ValueError("Generated quiz is not a JSON object.")

    questions = quiz.get("questions")
    if not isinstance(questions, list):
        raise ValueError("Generated quiz JSON is missing 'questions' list.")

    if len(questions) != expected_count:
        raise ValueError(f"Expected {expected_count} questions, got {len(questions)}.")

    required = {"id", "difficulty", "question_text", "options", "correct_option", "explanation"}
    for q in questions:
        if not isinstance(q, dict):
            raise ValueError("A question item is not an object.")

        missing = required - set(q.keys())
        if missing:
            raise ValueError(f"Question {q.get('id', '?')} missing keys: {sorted(missing)}")

        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) != 4:
            raise ValueError(f"Question {q.get('id', '?')} must have exactly 4 options.")

        if q.get("correct_option") not in ANSWER_LETTERS:
            raise ValueError(f"Question {q.get('id', '?')} has invalid correct_option.")

    return quiz

