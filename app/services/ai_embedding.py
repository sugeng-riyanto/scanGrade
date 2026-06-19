"""Generate embeddings for essay questions via API (no local PyTorch)."""
import json
import logging
from typing import Optional

logger = logging.getLogger("app")

OPENAI_EMBEDDING_URL = "https://api.openai.com/v1/embeddings"


def _call_embedding_api(text: str) -> Optional[list]:
    """Call OpenAI embedding API. Cheap: $0.02/1M tokens."""
    try:
        from app.services.ai_service import _get_active_key
        key = _get_active_key(None)
        api_key = (key or {}).get("api_key", "")
        if not api_key:
            logger.warning("No AI API key for embedding — skipping")
            return None
        import requests
        resp = requests.post(OPENAI_EMBEDDING_URL,
                             headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                             json={"model": "text-embedding-3-small", "input": text[:2048]},
                             timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data["data"][0]["embedding"]
        logger.warning("Embedding API error: %s", resp.status_code)
        return None
    except Exception as e:
        logger.warning("Embedding API failed: %s", e)
        return None


def generate_embedding(text: str) -> Optional[list]:
    """Generate embedding vector for a text via API. Returns None if unavailable."""
    return _call_embedding_api(text)


def preprocess_exam_questions(exam_id: str, questions: list, supabase) -> dict:
    """Preprocess all essay questions for an exam via API embedding."""
    processed = 0
    for q in questions:
        if q.get("type") != "essay":
            continue
        q_idx = q.get("number", 0) - 1
        question_text = q.get("text", "")
        rubric = json.dumps(q.get("rubric", []))
        embedding = generate_embedding(question_text)

        data = {
            "exam_id": exam_id,
            "question_index": q_idx,
            "question_text": question_text,
            "question_type": "essay",
            "rubric": rubric,
        }
        if embedding:
            data["embedding"] = json.dumps(embedding)

        try:
            supabase.table("question_embeddings").upsert(data, on_conflict=["exam_id", "question_index"]).execute()
            processed += 1
        except Exception as e:
            logger.error("Failed to save embedding for question %d: %s", q_idx, e)

    return {"processed": processed, "total": len([q for q in questions if q.get("type") == "essay"])}
