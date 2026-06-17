"""Generate embeddings for essay questions (local, free)."""
import json
import logging
from typing import Optional, List

logger = logging.getLogger("app")

_model = None


def _load_model():
    """Lazy-load sentence-transformers model (downloaded once)."""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model... (first load ~30s)")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Embedding model loaded")
    except ImportError:
        logger.warning("sentence-transformers not installed. Embedding disabled.")
        _model = False
    return _model


def generate_embedding(text: str) -> Optional[List[float]]:
    """Generate embedding vector for a text. Returns None if unavailable."""
    model = _load_model()
    if not model:
        return None
    try:
        emb = model.encode(text[:512])  # Truncate to 512 chars
        return emb.tolist()
    except Exception as e:
        logger.error("Embedding error: %s", e)
        return None


def preprocess_exam_questions(exam_id: str, questions: list, supabase) -> dict:
    """Preprocess all essay questions for an exam:
    - Generate embeddings
    - Save to question_embeddings table
    Returns count of processed questions.
    """
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
