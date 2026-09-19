"""Celery tasks for async OMR processing."""
import os
import io
import json
import zipfile
import logging
import uuid
from app.celery_app import celery_app
from app.services.question_types import objective_result

logger = logging.getLogger("app")


def _run_omr(image_data: bytes, total_questions: int = 50, exam_id: str = "",
             page_index: int = 0) -> dict:
    """Shared OMR pipeline: process → grade → debug image.

    `page_index` is which page of a multi-page sheet this image is: the grid
    repeats across pages, so a reader that ignores it reads page 1's bubbles
    twice and calls the second set page 2's answers.
    """
    from app.services.omr_service import process_scan, load_image, draw_debug_image, find_registration_marks

    result = process_scan(image_data, total_questions=total_questions,
                          preprocess=True, page_index=page_index)
    if "error" in result:
        return result

    # Grade if exam_id provided
    if exam_id:
        from app.utils.auth import get_supabase
        try:
            supabase = get_supabase()
            # `question_types` rides along with the key: the grader needs the kind
            # of each question, and a column missing from a select list reads as
            # absent rather than as an error (see AGENTS.md). `total_questions` is
            # here for the same reason — it is the grader's denominator, and a
            # missing column would silently score against zero.
            exam = supabase.table("exams").select("answer_key,question_types,total_questions").eq("id", exam_id).single().execute().data
            if exam and exam.get("answer_key"):
                key = exam["answer_key"]
                if isinstance(key, str):
                    key = json.loads(key)
                qtypes = exam.get("question_types") or {}
                if isinstance(qtypes, str):
                    qtypes = json.loads(qtypes)
                detected = result.get("answers", {})
                # One rule for the whole app. This loop divided by the number of
                # answers the *key* had, so a teacher who had keyed 2 of 10
                # questions got a perfect 100 — the same defect the scan routes had,
                # reached by a different door (the Celery worker). The paper's own
                # count is the denominator, falling back to the sheet's geometry if
                # the exam row carries none.
                paper = int((exam or {}).get("total_questions") or 0) or total_questions
                objective = objective_result(qtypes, key, detected, paper)
                result["score"] = objective.score
                result["correct"] = objective.correct
                result["graded"] = objective.keyed
                result["unkeyed"] = len(objective.unkeyed)
        except Exception as e:
            logger.warning("OMR grading failed: %s", e)

    # Debug image
    try:
        img = load_image(image_data)
        if img is not None:
            corners = find_registration_marks(img)
            debug_jpg = draw_debug_image(img, corners, result.get("answers"),
                                         page_index=page_index)
            import base64
            result["debug_image"] = base64.b64encode(debug_jpg).decode()
    except Exception as e:
        logger.debug("OMR debug image failed: %s", e)

    return result


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5)
def process_omr_scan(self, image_path: str, total_questions: int = 50,
                     exam_id: str = "", page_index: int = 0):
    """Process a single OMR scan image."""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        return _run_omr(image_data, total_questions, exam_id, page_index=page_index)
    except Exception as e:
        logger.error("OMR task failed: %s", e, exc_info=True)
        try:
            self.retry(exc=e)
        except Exception:
            return {"error": f"Gagal memproses scan: {str(e)[:200]}"}


@celery_app.task(bind=True, max_retries=1)
def process_bulk_scan(self, zip_path: str, total_questions: int = 50,
                      exam_id: str = "", page_index: int = 0):
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

                    omr_result = _run_omr(image_data, total_questions, exam_id,
                                          page_index=page_index)

                    entry = {
                        "filename": fname,
                        # Which page this image was, and which questions it
                        # covered, so a 2-page sheet can be merged per student.
                        "page_index": omr_result.get("page_index", 0),
                        "first_question": omr_result.get("first_question"),
                        "last_question": omr_result.get("last_question"),
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
