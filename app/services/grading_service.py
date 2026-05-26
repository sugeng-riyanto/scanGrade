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


def grade_essay(answer: str, rubric: str) -> dict:
    score = 0
    max_score = 100
    return {
        "score": score,
        "max_score": max_score,
        "feedback": "",
    }
