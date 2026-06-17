"""Grading service — bubble sheet + AI essay grading."""
import json
import logging
from flask import g

logger = logging.getLogger("app")


def grade_bubble_sheet(answers: dict, key: dict) -> dict:
    correct = 0
    total = len(key)
    for q, ans in answers.items():
        if key.get(q) == ans:
            correct += 1
    return {
        "correct": correct,
        "total": total,
        "score": round((correct / total) * 100, 2) if total else 0,
    }


def grade_essay(answer: str, rubric: str = "", teacher_id: str = None,
                submission_id: str = None, question_index: int = 0,
                question_text: str = "", max_score: int = 100) -> dict:
    """Grade an essay answer — delegates to AI grading pipeline.
    
    Falls back to simple scoring if AI unavailable.
    """
    if teacher_id and submission_id:
        try:
            from app.services.ai_grading import grade_essay as ai_grade
            result = ai_grade(
                teacher_id=teacher_id,
                submission_id=submission_id,
                question_index=question_index,
                question_text=question_text,
                student_answer=answer,
                max_score=max_score,
                rubric=rubric,
            )
            if "error" not in result:
                return result
            logger.warning("AI grading fallback: %s", result.get("error"))
        except Exception as e:
            logger.warning("AI grading error, using fallback: %s", e)

    # Fallback: simple scoring
    word_count = len(answer.split()) if answer else 0
    if max_score <= 0:
        max_score = 100
    # Heuristic: more words = higher score (not accurate, just fallback)
    base = min(max_score, max(0, word_count // 10))
    return {
        "score": base,
        "max_score": max_score,
        "feedback": "Maaf, koreksi AI sedang tidak tersedia. Nilai diberikan berdasarkan panjang jawaban.",
        "cached": False,
    }
