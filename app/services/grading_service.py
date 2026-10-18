"""Grading service — bubble sheet, and the essay path that no longer invents marks.

`grade_essay` used to fall back to `word_count // 10` when the AI was unavailable,
and return that as a real score with the reassuring line "Nilai diberikan berdasarkan
panjang jawaban". A teacher scrolling a class of 200 would see those numbers in the
same column as every other mark, with nothing on screen distinguishing a mark from a
word count — and a pupil who wrote three short correct sentences scored below one
who padded five paragraphs. A fallback that guesses is worse than an error, because
an error is visible.

So there is no fallback any more. The AI path returns a validated suggestion or a
message a teacher can act on, and the mark is set by the teacher either way.
"""
import logging

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
    """Ask for a suggestion on one essay. Never returns a mark of its own.

    The reply carries `suggestion: True` and the caveat that a teacher has to accept
    it, so a caller that renders `score` cannot present it as a finished mark by
    accident.
    """
    if not teacher_id:
        return {
            "error": "Koreksi esai memerlukan akun guru dan API key AI yang aktif.",
            "suggestion": True,
        }

    from app.services.ai_service import suggest_grade

    result = suggest_grade(
        teacher_id=teacher_id,
        question_text=question_text,
        student_answer=answer,
        max_score=max_score,
        rubric=rubric,
    )
    if "error" in result:
        return {**result, "suggestion": True}
    return {
        **result,
        "suggestion": True,
        "submission_id": submission_id,
        "question_index": question_index,
        "needs_teacher_acceptance": True,
    }
