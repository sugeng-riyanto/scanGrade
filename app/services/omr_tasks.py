"""Celery tasks for async OMR processing."""
import os
import json
import logging
from app.celery_app import celery_app

logger = logging.getLogger("app")


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5)
def process_omr_scan(self, image_path: str, total_questions: int = 50, exam_id: str = ""):
    """Process an OMR scan image as background task.
    image_path: absolute path to saved image file
    Returns dict with answers, NISN, confidence, score (if exam_id provided)
    """
    from app.services.omr_service import process_scan, load_image, draw_debug_image

    try:
        with open(image_path, "rb") as f:
            image_data = f.read()

        result = process_scan(image_data, total_questions=total_questions, preprocess=True)

        if "error" in result:
            return result

        # Grade if exam_id provided
        if exam_id:
            from app.utils.auth import get_supabase
            supabase = get_supabase()
            try:
                exam = supabase.table("exams").select("answer_key").eq("id", exam_id).single().execute().data
                if exam and exam.get("answer_key"):
                    key = exam["answer_key"]
                    if isinstance(key, str):
                        key = json.loads(key)
                    detected = result.get("answers", {})
                    correct = 0
                    for k, v in key.items():
                        if k in detected and detected[k] == v and v not in ("essay", "essay_text", "essay_canvas"):
                            correct += 1
                    mcq_count = sum(1 for v in key.values() if v not in ("essay", "essay_text", "essay_canvas"))
                    result["score"] = round((correct / max(mcq_count, 1)) * 100, 2)
                    result["correct"] = correct
            except Exception as e:
                logger.warning("OMR task grading failed: %s", e)

        # Generate debug image
        try:
            img = load_image(image_data)
            if img is not None:
                corners = __import__("app.services.omr_service", fromlist=["find_registration_marks"]).find_registration_marks(img)
                debug_jpg = draw_debug_image(img, corners, result.get("answers"))
                import base64
                result["debug_image"] = base64.b64encode(debug_jpg).decode()
        except Exception as e:
            logger.debug("OMR task debug image failed: %s", e)

        return result

    except Exception as e:
        logger.error("OMR task failed: %s", e, exc_info=True)
        try:
            self.retry(exc=e)
        except Exception:
            return {"error": f"Gagal memproses scan: {str(e)[:200]}"}
