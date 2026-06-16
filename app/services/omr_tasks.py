"""Celery tasks for async OMR processing."""
import os
import io
import json
import zipfile
import logging
import uuid
from app.celery_app import celery_app

logger = logging.getLogger("app")


def _run_omr(image_data: bytes, total_questions: int = 50, exam_id: str = "") -> dict:
    """Shared OMR pipeline: process → grade → debug image."""
    from app.services.omr_service import process_scan, load_image, draw_debug_image, find_registration_marks

    result = process_scan(image_data, total_questions=total_questions, preprocess=True)
    if "error" in result:
        return result

    # Grade if exam_id provided
    if exam_id:
        from app.utils.auth import get_supabase
        try:
            supabase = get_supabase()
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
            logger.warning("OMR grading failed: %s", e)

    # Debug image
    try:
        img = load_image(image_data)
        if img is not None:
            corners = find_registration_marks(img)
            debug_jpg = draw_debug_image(img, corners, result.get("answers"))
            import base64
            result["debug_image"] = base64.b64encode(debug_jpg).decode()
    except Exception as e:
        logger.debug("OMR debug image failed: %s", e)

    return result


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5)
def process_omr_scan(self, image_path: str, total_questions: int = 50, exam_id: str = ""):
    """Process a single OMR scan image."""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        return _run_omr(image_data, total_questions, exam_id)
    except Exception as e:
        logger.error("OMR task failed: %s", e, exc_info=True)
        try:
            self.retry(exc=e)
        except Exception:
            return {"error": f"Gagal memproses scan: {str(e)[:200]}"}


@celery_app.task(bind=True, max_retries=1)
def process_bulk_scan(self, zip_path: str, total_questions: int = 50, exam_id: str = ""):
    """Process a ZIP file containing multiple LJK scans as background task."""
    from PIL import Image

    results = []
    errors = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            images = sorted([n for n in zf.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png"))])

            for idx, fname in enumerate(images):
                # Update progress
                self.update_state(state="PROCESSING", meta={"current": idx + 1, "total": len(images), "file": fname})

                try:
                    raw = zf.read(fname)
                    buf = io.BytesIO(raw)
                    img = Image.open(buf)
                    img.verify()
                    buf.seek(0)
                    img = Image.open(buf)
                    clean = io.BytesIO()
                    fmt = "PNG" if fname.lower().endswith(".png") else "JPEG"
                    if fmt == "JPEG" and img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(clean, format=fmt)
                    image_data = clean.getvalue()

                    omr_result = _run_omr(image_data, total_questions, exam_id)

                    entry = {
                        "filename": fname,
                        "nisn": omr_result.get("nisn", "????????"),
                        "nisn_confidence": omr_result.get("nisn_confidence", 0),
                        "answers": omr_result.get("answers", {}),
                        "detected": omr_result.get("detected", 0),
                        "confidence": omr_result.get("confidence", {}),
                        "avg_confidence": omr_result.get("avg_confidence", 0),
                        "needs_review": omr_result.get("needs_review", []),
                        "score": omr_result.get("score"),
                        "correct": omr_result.get("correct"),
                        "error": omr_result.get("error"),
                    }
                    if omr_result.get("error"):
                        errors.append(entry)
                    else:
                        results.append(entry)

                except Exception as e:
                    errors.append({"filename": fname, "error": str(e)[:150]})

    except zipfile.BadZipFile:
        return {"error": "File ZIP rusak atau tidak valid"}
    except Exception as e:
        return {"error": f"Gagal memproses ZIP: {str(e)[:200]}"}
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass

    return {
        "success": True,
        "total": len(results) + len(errors),
        "processed": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }
