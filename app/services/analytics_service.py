def aggregate_scores(submissions: list) -> dict:
    scores = [s.get("score", 0) for s in submissions]
    if not scores:
        return {"avg": 0, "max": 0, "min": 0, "count": 0}
    return {
        "avg": sum(scores) / len(scores),
        "max": max(scores),
        "min": min(scores),
        "count": len(scores),
    }
