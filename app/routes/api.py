import io
import os
import json
import zipfile
import uuid
import time
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g, render_template, redirect, send_file, current_app
from app.utils.auth import login_required, get_supabase
from app.utils.helpers import row_or_none
from app.utils.exam_access import exam_sitting_allowed
from app.utils import exam_window
from app.utils import denials
from app.decorators.security import require_role, STAFF_ROLES
from app.services.anti_cheat_service import validate_violation_log
from app.services.question_types import (
    earned_points, grade_answer, is_objective, objective_result,
)
from app.services.student_import import create_student_account
from app.utils.logger import get_logger
from app.errors import ValidationError, NotFoundError, GradingError, AIProcessingError
from app.utils.rate_limiter import limiter

def _rate_limit(n):
    return limiter.limit(n) if limiter else (lambda f: f)

api_bp = Blueprint("api", __name__)

# ── Upload security constants ──────────────────────────
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB
UPLOAD_SCAN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "uploads", "scans")

def _redis_lock(key, timeout=10):
    """Redis-based cross-worker lock (SETNX pattern)."""
    try:
        from redis import Redis
        r = Redis.from_url(current_app.config.get("REDIS_URL", "redis://localhost:6379/0"))
        lock_key = f"scan_grade:lock:{key}"
        # SETNX: only set if key doesn't exist, with expiry
        if r.setnx(lock_key, "1"):
            r.expire(lock_key, timeout)
            return r
        return None
    except Exception:
        return None

def _release_lock(redis_conn, key):
    """Release a Redis lock."""
    try:
        if redis_conn:
            redis_conn.delete(f"scan_grade:lock:{key}")
    except Exception:
        pass

_sync_last = {}
_SYNC_CLEANUP_INTERVAL = 600  # seconds
_sync_last_cleanup = time.time()


def _check_rate_limit(user_id, exam_id, min_interval=5):
    global _sync_last_cleanup
    now = time.time()
    key = f"{user_id}:{exam_id}"
    last = _sync_last.get(key, 0)
    if now - last < min_interval:
        return False
    _sync_last[key] = now
    # Periodic cleanup of stale entries (every 10 min)
    if now - _sync_last_cleanup > _SYNC_CLEANUP_INTERVAL:
        _sync_last_cleanup = now
        cutoff = now - 3600  # remove entries older than 1 hour
        stale_keys = [k for k, v in _sync_last.items() if v < cutoff]
        for k in stale_keys:
            del _sync_last[k]
        with _sync_lock_mutex:
            stale_locks = [k for k in _sync_locks if k not in _sync_last]
            for k in stale_locks:
                del _sync_locks[k]
    return True


@api_bp.route("/violation/log", methods=["POST"])
@login_required
def log_violation():
    """Record anti-cheat events sent by the exam page.

    Accepts ``{"_csrf_token": ..., "logs": [...]}``. The body has to be an
    object, not a bare list: ``validate_csrf`` can only read the token out of a
    JSON *object*, so the list this endpoint used to receive could never carry
    one — every event was answered 403 and discarded, which left the whole
    penalty ladder with nothing to count.
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        logs = data.get("logs") or []
    else:
        logs = data if isinstance(data, list) else []
    if not logs:
        return jsonify({"violations": []})

    supabase = get_supabase()
    results = []
    for log in logs:
        valid = validate_violation_log(
            g.user_id,
            log.get("exam_id", ""),
            log.get("timestamp", 0),
        )
        if valid["valid"]:
            exam_id = log.get("exam_id", "")
            try:
                supabase.table("violation_logs").insert({
                    "exam_id": exam_id,
                    "user_id": g.user_id,
                    "violation_type": log.get("violation_type", "unknown"),
                    "metadata": log.get("metadata", {}),
                }).execute()
            except Exception:
                current_app.logger.warning("Violation log insert failed for exam %s", exam_id)
                results.append({"logged": False, "reason": "db_error"})
                continue
            from app.services.anti_cheat_service import (
                calculate_graduated_penalty, count_penalized_violations,
            )
            # The count is the source of truth for the ladder, and the exam row is
            # read before it so the response carries the penalty the server would
            # actually apply. The client displays that number: it must never show a
            # penalty the server will not charge (or hide one it will).
            exam = row_or_none(
                supabase.table("exams")
                .select("anti_cheat_enabled, penalty_per_violation, max_violations, auto_submit_on_max")
                .eq("id", exam_id).maybe_single().execute()
            ) or {}
            total_count = count_penalized_violations(supabase, g.user_id, exam_id)
            penalty_info = calculate_graduated_penalty(total_count, exam)
            # Keep the submission's penalty in step with the ladder so the results
            # screen agrees with the score, without ever double-charging: the value
            # is the ladder's total, not another increment.
            try:
                sub = supabase.table("submissions").select("id").eq("exam_id", exam_id).eq("student_id", g.user_id).order("created_at", desc=True).limit(1).execute()
                if sub.data:
                    supabase.table("submissions").update({"violations": total_count, "penalty": penalty_info["penalty"]}).eq("id", sub.data[0]["id"]).execute()
            except Exception:
                current_app.logger.exception("Could not sync penalty for exam %s", exam_id)
            results.append({"logged": True, "violation_count": total_count, **penalty_info})
        else:
            results.append({"logged": False, "reason": valid.get("reason")})

    return jsonify({"violations": results})


@api_bp.route("/student/force-submit", methods=["POST"])
@login_required
def force_submit():
    """Force-submit an exam when anti-cheat max violations reached.
    This is a fallback when the Alpine component cannot be triggered.
    """
    data = request.get_json()
    exam_id = (data or {}).get("exam_id", "")
    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    supabase = get_supabase()
    # Find the latest draft submission and submit it
    try:
        sub = supabase.table("submissions") \
            .select("id,answers") \
            .eq("exam_id", exam_id) \
            .eq("student_id", g.user_id) \
            .eq("status", "draft") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if sub.data:
            answers = sub.data[0].get("answers") or {}
            # Re-fetch submission for answer key
            exam = supabase.table("exams").select("answer_key,question_types,question_weights,total_questions,penalty_per_violation").eq("id", exam_id).single().execute().data or {}
            # Parse JSON fields that may be strings
            for _fld in ("answer_key", "question_types", "question_weights"):
                _v = exam.get(_fld)
                if isinstance(_v, str):
                    try: exam[_fld] = json.loads(_v)
                    except (json.JSONDecodeError, TypeError): exam[_fld] = {}
            key = exam.get("answer_key") or {}
            qtypes = exam.get("question_types") or {}
            weights = exam.get("question_weights") or {}
            total_q = exam.get("total_questions", 0)
            penalty = float(exam.get("penalty_per_violation", 5))
            # Calculate MCQ score
            earned, _graded = earned_points(qtypes, key, answers, weights, total_q)
            # The stored objective score is the app's one rule
            # (`question_types.objective_result`): a percentage of the paper's
            # objective questions, so a partly keyed paper cannot pay this pupil
            # 100. `earned` above still carries the weighted total that the final
            # score is built from — they are two different numbers on purpose, and
            # this route used to write the wrong one to this column.
            score = objective_result(qtypes, key, answers, total_q).score
            # Get actual penalty from violation logs
            from app.services.anti_cheat_service import (
                calculate_graduated_penalty, count_penalized_violations,
            )
            viol_count = count_penalized_violations(supabase, g.user_id, exam_id)
            pinfo = calculate_graduated_penalty(viol_count, exam)
            total_penalty = pinfo.get("penalty", 0)
            final = max(0.0, round(earned - total_penalty, 2))
            supabase.table("submissions") \
                .update({"status": "submitted", "answers": answers, "score": score, "final_score": final, "penalty": total_penalty}) \
                .eq("id", sub.data[0]["id"]) \
                .execute()
            return jsonify({"success": True, "score": score, "final_score": final, "penalty": total_penalty})
        return jsonify({"error": "No draft submission found"}), 404
    except Exception as e:
        current_app.logger.error("force_submit error: %s", e)
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/violation/count", methods=["GET"])
@login_required
def violation_count():
    """The count and the penalty the server would apply right now.

    The exam page calls this when it starts: the penalty ladder lives in the
    database and survives a reload, while the page's own counter started at zero
    every time — so after a refresh a student saw "0 pelanggaran" and earned
    warnings they had already used, and the auto-submit threshold moved.
    """
    exam_id = request.args.get("exam_id") or ""
    supabase = get_supabase()
    from app.services.anti_cheat_service import (
        calculate_graduated_penalty, count_penalized_violations,
    )
    exam = row_or_none(
        supabase.table("exams")
        .select("anti_cheat_enabled, penalty_per_violation, max_violations, auto_submit_on_max")
        .eq("id", exam_id).maybe_single().execute()
    ) or {}
    count = count_penalized_violations(supabase, g.user_id, exam_id)
    info = calculate_graduated_penalty(count, exam)
    return jsonify({"count": count, **info})


@api_bp.route("/scan/process", methods=["POST"])
@_rate_limit("20 per minute")
@login_required
def scan_process():
    if g.get("user_role") not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya guru/admin yang boleh scan OMR"}), 403
    """Process a scanned bubble sheet image and return detected answers.

    Security: validates extension, MIME type, image integrity, strips EXIF.
    """
    if "image" not in request.files:
        return jsonify({"error": "Tidak ada gambar yang dikirim"}), 400

    image_file = request.files["image"]
    raw_bytes = image_file.read()

    # 0. Handle PDF upload — convert first page to image
    ext = os.path.splitext(image_file.filename or "")[1].lower()
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_bytes
            pages = convert_from_bytes(raw_bytes, first_page=1, last_page=1)
            if not pages:
                return jsonify({"error": "Gagal membaca PDF"}), 422
            out = io.BytesIO()
            pages[0].save(out, format="JPEG", quality=90)
            raw_bytes = out.getvalue()
        except ImportError:
            return jsonify({"error": "PDF support tidak tersedia (install pdf2image)"}), 422
        except Exception as e:
            return jsonify({"error": f"Gagal memproses PDF: {str(e)[:100]}"}), 422

    # 1. Validate extension
    if ext not in ALLOWED_EXTENSIONS and ext != ".pdf":
        return jsonify({"error": "Format file tidak didukung. Gunakan .jpg, .jpeg, .png, atau .pdf"}), 422

    # 2. Validate MIME type via python-magic
    mime_type = None
    try:
        import magic
        header = raw_bytes[:2048]
        mime_type = magic.from_buffer(header, mime=True)
        if ext != ".pdf" and mime_type not in ("image/jpeg", "image/png"):
            return jsonify({"error": f"Tipe file tidak valid ({mime_type}). Hanya jpg/png yang diizinkan."}), 422
    except ImportError:
        current_app.logger.warning("python-magic not installed; skipping MIME validation")

    # 3. Validate size
    file_size = len(raw_bytes)
    if file_size > MAX_IMAGE_SIZE:
        return jsonify({"error": f"Gambar terlalu besar ({file_size/1024/1024:.1f}MB). Maksimal 20MB."}), 413
    if file_size == 0:
        return jsonify({"error": "File kosong"}), 422

    # 4. Verify image integrity + strip EXIF via Pillow
    try:
        from PIL import Image
        buf = io.BytesIO(raw_bytes)
        img_pil = Image.open(buf)
        img_pil.verify()
        buf.seek(0)
        img_pil = Image.open(buf)
        clean_buf = io.BytesIO()
        save_format = "PNG" if mime_type == "image/png" else "JPEG"
        if "exif" in img_pil.info:
            img_pil.info.pop("exif")
        if save_format == "JPEG" and img_pil.mode != "RGB":
            img_pil = img_pil.convert("RGB")
        img_pil.save(clean_buf, format=save_format)
        image_data = clean_buf.getvalue()
        current_app.logger.info("EXIF stripped from scan image, cleaned size=%d", len(image_data))
    except Exception as e:
        return jsonify({"error": "Gambar tidak valid atau corrupt", "detail": str(e)[:100]}), 422

    exam_id = request.form.get("exam_id", "")
    total_questions = int(request.form.get("total_questions", 50))
    # Which page of the sheet this photograph is. A 2-page LJK (over 80
    # questions) is scanned one page at a time; without this the reader could
    # not tell page 2 from page 1 and reported page 1's marks as page 2's
    # answers. See omr_service.read_answers.
    page_index = max(0, int(request.form.get("page", 0) or 0))

    _cleanup_scan_tmp()

    # Save original image to temp
    scan_file_id = str(uuid.uuid4())[:8]
    scan_dir = os.path.join(UPLOAD_SCAN_DIR, "tmp")
    os.makedirs(scan_dir, exist_ok=True)
    scan_ext = ".png" if mime_type == "image/png" else ".jpg"
    scan_temp_path = os.path.join(scan_dir, scan_file_id + scan_ext)
    try:
        with open(scan_temp_path, "wb") as f:
            f.write(image_data)
    except Exception as e:
        current_app.logger.warning("Failed to save scan temp: %s", e)

    # Enqueue async Celery task
    try:
        from app.services.omr_tasks import process_omr_scan
        task = process_omr_scan.delay(
            image_path=scan_temp_path,
            total_questions=total_questions,
            exam_id=exam_id,
            page_index=page_index,
        )
        current_app.logger.info("OMR task enqueued: %s", task.id)
        return jsonify({
            "async": True,
            "task_id": task.id,
            "scan_file_id": scan_file_id,
            "status": "processing",
        })
    except Exception as e:
        current_app.logger.error("Failed to enqueue OMR task: %s", e)
        # Fallback to synchronous processing
        from app.services.omr_service import process_scan, draw_debug_image, preprocess_scan
        try:
            result = process_scan(image_data, total_questions=total_questions,
                                  preprocess=True, page_index=page_index)
        except Exception as e2:
            return jsonify({"error": f"Gagal memproses: {str(e2)[:200]}"}), 422

        if "error" not in result and exam_id:
            supabase = get_supabase()
            exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
            if exam and exam.get("answer_key"):
                key = exam["answer_key"]
                if isinstance(key, str):
                    key = json.loads(key)
                detected = result.get("answers", {})
                qtypes = exam.get("question_types") or {}
                if isinstance(qtypes, str):
                    qtypes = json.loads(qtypes)
                correct = 0
                graded = 0
                for k, v in key.items():
                    # The key decides which questions the reader may mark, and the
                    # grader decides whether an answer is right — one comparison of
                    # letters here is what made a scanned true/false question score
                    # nothing at all.
                    if not key_has_answer(qtypes.get(str(k)), v):
                        continue
                    graded += 1
                    if k in detected and grade_answer(qtypes.get(str(k)), v, detected[k]):
                        correct += 1
                result["score"] = round((correct / max(graded, 1)) * 100, 2)
                result["correct"] = correct
                result["graded"] = graded

        if "error" not in result:
            from app.services.omr_service import load_image, find_registration_marks
            img = load_image(image_data)
            if img is not None:
                corners = find_registration_marks(img)
                debug_jpg = draw_debug_image(img, corners, result.get("answers"),
                                             page_index=page_index)
                import base64
                result["debug_image"] = base64.b64encode(debug_jpg).decode()
                result["scan_file_id"] = scan_file_id

        result["async"] = False
        return jsonify(result)




def _scan_essay_vision(image_bytes, api_key="", lang="en"):
    try:
        from google import genai
        import PIL.Image
        import io
        client = genai.Client(api_key=api_key)
        prompt = "Extract ALL handwritten text from this exam answer sheet. Preserve the original language. Output only the text content."
        if lang and not lang.startswith("en"):
            prompt += " The handwriting is in " + lang + "."
        buf = io.BytesIO(image_bytes)
        img = PIL.Image.open(buf)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt, img]
        )
        return response.text.strip()
    except ImportError:
        raise ImportError("google-genai tidak terinstall. Install: pip install google-genai")
    except ValueError:
        raise
    except Exception as e:
        err = str(e)
        if "API_KEY" in err or "not found" in err.lower() or "unauthorized" in err.lower():
            raise ValueError("API Key Gemini tidak valid. Setup di Pengaturan AI.")
        if "quota" in err.lower() or "429" in err or "RATE_LIMIT" in err.upper():
            raise ValueError("Kuota Gemini habis. Tunggu sebentar.")
        raise


@api_bp.route("/scan/essay", methods=["POST"])
@login_required
@require_role(*STAFF_ROLES)
def scan_essay():
    if "image" not in request.files:
        return jsonify({"error": "Tidak ada gambar"}), 400
    image_file = request.files["image"]
    raw = image_file.read()
    if len(raw) > 20 * 1024 * 1024:
        return jsonify({"error": "Gambar terlalu besar. Maksimal 20MB"}), 413
    from app.services.ai_service import _get_active_key
    key_data = _get_active_key(g.user_id)
    api_key = ""
    if key_data and key_data.get("provider") == "gemini":
        api_key = key_data.get("api_key", "")
    elif key_data:
        supabase = get_supabase()
        gemini_keys = supabase.table("teacher_ai_keys").select("*").eq("teacher_id", g.user_id).eq("provider", "gemini").limit(1).execute().data
        if gemini_keys:
            api_key = gemini_keys[0].get("api_key", "")
    if not api_key:
        return jsonify({"error": "API Key Gemini tidak ditemukan. Setup Gemini di Pengaturan AI."}), 400
    try:
        text = _scan_essay_vision(raw, api_key=api_key)
        return jsonify({"success": True, "text": text})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Gagal OCR: " + str(e)[:200]}), 500


@api_bp.route("/grade/vision-canvas", methods=["POST"])
@login_required
@require_role(*STAFF_ROLES)
def vision_canvas_ocr():
    data = request.get_json() or {}
    image_data = data.get("image", "")
    if not image_data or not image_data.startswith("data:image/"):
        return jsonify({"error": "Data URL gambar tidak valid"}), 400
    try:
        import base64
        header, encoded = image_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
    except Exception as e:
        return jsonify({"error": "Gagal decode: " + str(e)[:100]}), 400
    from app.services.ai_service import _get_active_key
    key_data = _get_active_key(g.user_id)
    api_key = ""
    if key_data and key_data.get("provider") == "gemini":
        api_key = key_data.get("api_key", "")
    elif key_data:
        supabase = get_supabase()
        gemini_keys = supabase.table("teacher_ai_keys").select("*").eq("teacher_id", g.user_id).eq("provider", "gemini").limit(1).execute().data
        if gemini_keys:
            api_key = gemini_keys[0].get("api_key", "")
    if not api_key:
        return jsonify({"error": "API Key Gemini tidak ditemukan. Setup Gemini di Pengaturan AI."}), 400
    try:
        from google import genai
        import PIL.Image
        import io
        client = genai.Client(api_key=api_key)
        prompt = "Extract ALL handwritten text, formulas, equations, and diagram labels from this student exam drawing. Output only the text."
        buf = io.BytesIO(img_bytes)
        img = PIL.Image.open(buf)
        response = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, img])
        text = response.text.strip()
        return jsonify({"success": True, "text": text})
    except ImportError:
        return jsonify({"error": "google-genai tidak terinstall. Install: pip install google-genai"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Gagal OCR: " + str(e)[:200]}), 500


@api_bp.route("/scan/task/<task_id>", methods=["GET"])
@login_required
@require_role(*STAFF_ROLES)
def scan_task_status(task_id):
    """Poll Celery task status and get result when done."""
    from app.celery_app import celery_app
    task = celery_app.AsyncResult(task_id)
    response = {"task_id": task_id, "status": task.state}

    if task.state == "PENDING":
        response["status"] = "pending"
    elif task.state == "PROCESSING":
        response["status"] = "processing"
    elif task.state == "PROGRESS":
        response["status"] = "processing"
        response["progress"] = task.info
    elif task.state == "SUCCESS":
        response["status"] = "done"
        response["result"] = task.result
    elif task.state == "FAILURE":
        response["status"] = "error"
        response["error"] = str(task.info)
        # Clean up task result
        task.forget()

    return jsonify(response)


@api_bp.route("/scan/bulk", methods=["POST"])
@_rate_limit("10 per minute")
@login_required
def scan_bulk():
    """Process a ZIP file containing multiple LJK scan images."""
    if g.get("user_role") not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya guru/admin yang boleh scan OMR"}), 403
    if "archive" not in request.files:
        return jsonify({"error": "Tidak ada file ZIP yang dikirim"}), 400

    archive_file = request.files["archive"]
    ext = os.path.splitext(archive_file.filename or "")[1].lower()
    if ext not in (".zip",):
        return jsonify({"error": "Hanya file ZIP yang didukung"}), 400

    exam_id = request.form.get("exam_id", "")
    total_questions = int(request.form.get("total_questions", 50))

    _cleanup_scan_tmp()

    # Save ZIP to temp
    zip_id = str(uuid.uuid4())[:8]
    zip_dir = os.path.join(UPLOAD_SCAN_DIR, "tmp")
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, f"bulk_{zip_id}.zip")
    archive_file.save(zip_path)

    # Enqueue async Celery task
    try:
        from app.services.omr_tasks import process_bulk_scan
        task = process_bulk_scan.delay(
            zip_path=zip_path,
            total_questions=total_questions,
            exam_id=exam_id,
            page_index=max(0, int(request.form.get("page", 0) or 0)),
        )
        current_app.logger.info("Bulk OMR task enqueued: %s (%d images)", task.id, 0)
        return jsonify({
            "async": True,
            "task_id": task.id,
            "status": "processing",
        })
    except Exception as e:
        current_app.logger.error("Failed to enqueue bulk task: %s", e)
        # Fallback: remove temp ZIP and return error
        try:
            os.remove(zip_path)
        except OSError:
            pass
        return jsonify({"error": "Gagal mengantrekan pemrosesan. Coba lagi."}), 500


@api_bp.route("/scan/bulk-save", methods=["POST"])
@login_required
@_rate_limit("10 per minute")
def scan_bulk_save():
    """Save multiple scan results as submissions at once."""
    if g.get("user_role") not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya guru/admin yang boleh menyimpan hasil scan"}), 403
    data = request.get_json()
    if not data or "submissions" not in data:
        return jsonify({"error": "No submissions data"}), 400

    exam_id = data.get("exam_id")
    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    supabase = get_supabase()
    saved = []
    failed = []

    # Read once, outside the loop. It used to be inside it, so a class of thirty
    # sheets paid thirty identical round-trips to Supabase for one exam row — and
    # that row now also carries the two clocks a late mark is computed from:
    # a window column left out of this select reads as *absent*, which is how a
    # missing field becomes "never late" rather than an error (see AGENTS.md).
    exam = supabase.table("exams").select(
        "answer_key,question_types,total_questions,"
        "start_at,end_at,duration_minutes,auto_submit_on_window_end"
    ).eq("id", exam_id).single().execute().data
    key = exam.get("answer_key", {}) if exam else {}
    if isinstance(key, str):
        key = json.loads(key)
    qtypes = exam.get("question_types") or {} if exam else {}
    if isinstance(qtypes, str):
        qtypes = json.loads(qtypes)
    total_q = int((exam or {}).get("total_questions") or 0)
    arrived_at = datetime.now(timezone.utc)

    for sub in data["submissions"]:
        student_id = sub.get("student_id")
        answers = sub.get("answers", {})
        nisn = sub.get("nisn", "")
        confidence = sub.get("confidence", {})
        needs_review = sub.get("needs_review", [])
        scan_file_id = sub.get("scan_file_id", "")

        if not student_id or not answers:
            failed.append({"student_id": student_id, "error": "Missing student_id or answers"})
            continue

        # Grade
        # One rule for the whole app. This loop used to divide by the number of
        # answers the *key* had, so a teacher who had keyed 2 of 10 questions got
        # 100% for a pupil who answered those two — and `mcq_count` below was
        # referenced without ever being assigned, so this route raised NameError on
        # the first sheet it saved.
        objective = objective_result(qtypes, key, answers, total_q)
        score = objective.score
        correct = objective.correct
        mcq_count = objective.out_of

        enriched = {}
        for k, v in answers.items():
            entry = {"answer": v}
            if k in confidence:
                entry["confidence"] = confidence[k]
            if k in needs_review:
                entry["review"] = True
            enriched[k] = entry
        if nisn:
            enriched["_nisn"] = nisn

        # Move scan image from tmp to permanent storage
        if scan_file_id:
            for ext in (".png", ".jpg"):
                src = os.path.join(UPLOAD_SCAN_DIR, "tmp", scan_file_id + ext)
                if os.path.exists(src):
                    dst_dir = os.path.join(UPLOAD_SCAN_DIR, exam_id)
                    os.makedirs(dst_dir, exist_ok=True)
                    dst = os.path.join(dst_dir, student_id + ext)
                    try:
                        os.rename(src, dst)
                        enriched["_scan_image"] = f"/static/uploads/scans/{exam_id}/{student_id}{ext}"
                    except Exception as e:
                        current_app.logger.warning("Failed to move scan image: %s", e)
                    break

        try:
            existing = supabase.table("submissions").select("id,started_at,submitted_late") \
                .eq("exam_id", exam_id).eq("student_id", student_id).execute().data
            # Was this paper late? The same rule the single-sheet save uses, from
            # the same module: the student's own sitting if the app saw one, else
            # the exam's start, else nothing — with the teacher's own answer winning
            # when the bulk table sent one for this row. `stated` absent means "let
            # the clock decide", and a stored True is never cleared by a later
            # page's arrival time.
            stated_late = sub.get("late")
            stored_late = bool(existing[0].get("submitted_late")) if existing else False
            late = exam_window.late_arrival(
                exam, (existing[0].get("started_at") if existing else None),
                arrived_at, stated_late)
            if stated_late is None and stored_late:
                late = True
            # `score` is a percentage (see `objective_result`), so its maximum is
            # 100. This stored a question *count* here — a different unit in the same
            # column as every other writer, which is how one reader ended up
            # dividing by a paper and another by a key.
            update_data = {"answers": enriched, "score": score, "max_score": 100.0,
                           "status": "graded", "submitted_late": late}
            if existing:
                supabase.table("submissions").update(update_data).eq("id", existing[0]["id"]).execute()
            else:
                update_data.update({"exam_id": exam_id, "student_id": student_id})
                supabase.table("submissions").insert(update_data).execute()
            saved.append({"student_id": student_id, "score": score, "correct": correct,
                          "total": mcq_count, "unkeyed": len(objective.unkeyed),
                          "nisn": nisn, "late": late})
        except Exception as e:
            failed.append({"student_id": student_id, "error": str(e)[:100]})

    return jsonify({
        "success": True,
        "saved": len(saved),
        "failed": len(failed),
        "details": {"saved": saved, "failed": failed},
    })


def _cleanup_scan_tmp(age_hours=1):
    """Remove stale temp scan files older than age_hours."""
    from app.services.cleanup_service import clean_temp_files
    clean_temp_files(max_age=age_hours * 3600)


@api_bp.route("/student/auto-save", methods=["POST"])
@login_required
def student_auto_save():
    """Auto-save student's in-progress exam draft — saves to localStorage mirror on server."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    exam_id = data.get("exam_id")
    answers = data.get("answers", {})
    if not exam_id or not answers:
        return jsonify({"saved": True, "at": int(time.time())})
    if not _check_rate_limit(g.user_id, exam_id, min_interval=5):
        return jsonify({"saved": True, "at": int(time.time()), "throttled": True})
    lock = _get_sync_lock(g.user_id, exam_id)
    if not lock.acquire(blocking=False):
        return jsonify({"saved": True, "at": int(time.time()), "busy": True})
    try:
        supabase = get_supabase()
        # This route previously accepted ANY exam_id and wrote to whatever
        # submission it found. Two holes followed: it created a draft for exams
        # belonging to another school or not published yet, and it accepted a
        # write to a submission already marked "submitted" — so answers could be
        # replaced after the exam had been handed in. Both are closed here.
        exam = row_or_none(
            supabase.table("exams")
            .select("id,school_id,class_ids,is_published,status")
            .eq("id", exam_id).maybe_single().execute()
        )
        allowed, _reason = exam_sitting_allowed(supabase, exam or {}, exam_id, g.user_id)
        if not allowed:
            current_app.logger.warning("auto-save denied: exam %s user %s", exam_id, g.user_id)
            return jsonify({"saved": True, "at": int(time.time()), "denied": True})

        existing = supabase.table("submissions").select("id,status,answers").eq("exam_id", exam_id).eq("student_id", g.user_id).execute().data
        if existing:
            sub = existing[0]
            # Only an open attempt may be auto-saved. A handed-in, marked, or
            # released submission is final.
            if sub.get("status") != "draft":
                return jsonify({"saved": True, "at": int(time.time()), "note": "not_draft"})
            merged = sub.get("answers") or {}
            if isinstance(merged, dict):
                merged.update(answers)
                answers = merged
            supabase.table("submissions").update({"answers": answers}).eq("id", sub["id"]).execute()
        else:
            supabase.table("submissions").insert({
                "exam_id": exam_id,
                "student_id": g.user_id,
                "answers": answers,
                "score": 0,
                "max_score": 100,
                "status": "draft",
            }).execute()
    except Exception as e:
        current_app.logger.warning("Auto-save failed for exam %s user %s: %s", exam_id, g.user_id, str(e))
    finally:
        lock.release()
    return jsonify({"saved": True, "at": int(time.time())})


def _get_exam_cached(exam_id, supabase):
    """Cache exam data per-request to avoid repeated queries for the same exam.
    Multiple students submitting to the same exam = 1 DB query instead of N."""
    from flask import g as flask_g
    if not hasattr(flask_g, '_exam_cache'):
        flask_g._exam_cache = {}
    if exam_id not in flask_g._exam_cache:
        flask_g._exam_cache[exam_id] = supabase.table("exams").select(
            "id,duration_minutes,total_questions,answer_key,question_types,"
            "question_weights,max_attempts,publish_mode,is_published,status,"
            "start_at,end_at,auto_submit_on_window_end"
        ).eq("id", exam_id).single().execute().data
    return flask_g._exam_cache[exam_id]


@api_bp.route("/student/sync-draft", methods=["POST"])
@login_required
def student_sync_draft():
    """Sync student draft — lightweight MCQ/text every 20s, canvas every 60s."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"saved": True, "at": int(time.time())})
    exam_id = data.get("exam_id")
    answers = data.get("answers", {})
    is_light = data.get("light", True)
    if not exam_id:
        return jsonify({"saved": True, "at": int(time.time())})
    if not _check_rate_limit(g.user_id, exam_id, min_interval=3 if is_light else 10):
        return jsonify({"saved": True, "at": int(time.time()), "throttled": True})
    # Verify the student may sit this exam. Delegated to the shared rule so this
    # path and the page routes cannot drift apart again — and it DENIES on error.
    # The previous version wrapped the whole check in `except Exception: pass`,
    # so a failed lookup let the write through, which is the opposite of a check.
    if g.get("user_role") == "murid":
        from app.utils.auth import get_supabase as _gs
        _sb = _gs()
        _exam_check = row_or_none(
            _sb.table("exams").select("school_id, class_ids, is_published, status")
            .eq("id", exam_id).maybe_single().execute()
        )
        allowed, _why = exam_sitting_allowed(_sb, _exam_check or {}, exam_id, g.user_id)
        if not allowed:
            current_app.logger.warning("sync-draft denied: exam %s user %s", exam_id, g.user_id)
            return jsonify({"saved": True, "at": int(time.time()), "denied": True})
    lock_key = f"sync:{g.user_id}:{exam_id}"
    rlock = _redis_lock(lock_key)
    if not rlock:
        return jsonify({"saved": True, "at": int(time.time()), "busy": True})
    server_now = int(time.time())
    server_time_left = None
    try:
        from app.utils.auth import get_supabase
        supabase = get_supabase()
        # Use cached exam fetch — avoids duplicate queries when many students sync simultaneously
        exam_data = _get_exam_cached(exam_id, supabase)
        duration = (exam_data["duration_minutes"] * 60) if exam_data and exam_data.get("duration_minutes") else None
        existing = supabase.table("submissions").select("id,status,answers,started_at").eq("exam_id", exam_id).eq("student_id", g.user_id).limit(1).execute().data
        if existing:
            sub = existing[0]
            if sub.get("status") in ("submitted", "graded", "published"):
                current_app.logger.warning("sync-draft blocked: submission %s already %s", sub["id"], sub.get("status"))
                return jsonify({"saved": True, "at": int(time.time()), "note": "already_submitted"})
            if is_light and sub.get("answers"):
                merged = sub["answers"]
                if isinstance(merged, dict):
                    # Deep merge: preserve existing canvas pages + paragraphs
                    for k, v in answers.items():
                        if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                            # Preserve canvas URLs from existing that aren't in incoming
                            ex_pages = merged[k].get("pages", {})
                            in_pages = v.get("pages", {})
                            if isinstance(ex_pages, dict) and isinstance(in_pages, dict):
                                for pk, pdata in ex_pages.items():
                                    if isinstance(pdata, dict) and "canvas" in pdata:
                                        if pk not in in_pages:
                                            in_pages[pk] = {}
                                        if "canvas" not in in_pages[pk]:
                                            in_pages[pk]["canvas"] = pdata["canvas"]
                            # Preserve paragraphs from existing if incoming doesn't have them
                            if "paragraphs" not in v and "paragraphs" in merged[k]:
                                v["paragraphs"] = merged[k]["paragraphs"]
                    merged.update(answers)
                    answers = merged
            # Inject dirty_canvases into answers for device-switch recovery
            if data.get("dirty_canvases"):
                for qk, pages in data["dirty_canvases"].items():
                    try:
                        if qk not in answers:
                            answers[qk] = {"type": "essay", "text": "", "pages": {}}
                        if isinstance(answers[qk], dict):
                            if "pages" not in answers[qk]:
                                answers[qk]["pages"] = {}
                            for pk, canvasUrl in pages.items():
                                if str(pk) not in answers[qk]["pages"]:
                                    answers[qk]["pages"][str(pk)] = {}
                                answers[qk]["pages"][str(pk)]["canvas"] = canvasUrl
                    except Exception:
                        pass
            supabase.table("submissions").update({"answers": answers}).eq("id", sub["id"]).execute()
            # Timer reconciliation: validasi started_at antar device
            client_started = data.get("started_at")
            existing_started = sub.get("started_at")
            if client_started and existing_started:
                try:
                    if isinstance(existing_started, str):
                        existing_ts = int(datetime.fromisoformat(existing_started.replace("Z", "+00:00")).timestamp())
                    else:
                        existing_ts = int(existing_started.timestamp())
                    client_ts = client_started // 1000 if client_started > 1e10 else client_started
                    if abs(client_ts - existing_ts) > 300:
                        return jsonify({"saved": True, "at": server_now, "error": "timer_mismatch", "server_time_left": max(0, duration - (server_now - existing_ts)) if duration else None}), 409
                except Exception:
                    pass
            # ── Where this sitting ends ──────────────────────────────────────
            # Counted from the *stored* start and the exam's own window, never from
            # what the client claims: `started_at` is the origin of the deadline, so
            # a client that could move it could extend its own exam. Both clocks —
            # the duration and the assignment window end — come from
            # app/utils/exam_window.py, the same functions the exam page and the
            # submit route use, so the countdown a student sees and the number
            # enforced here are one number.
            if existing_started:
                try:
                    started_dt = exam_window.parse_dt(existing_started)
                    now_dt = datetime.fromtimestamp(server_now, tz=timezone.utc)
                    left = exam_window.seconds_left(exam_data or {}, existing_started, now_dt)
                    running_for = int((now_dt - started_dt).total_seconds()) if started_dt else 0
                    # A sitting that began seconds ago is not "out of time": without
                    # this guard a clock skew between the phone and the box would end
                    # an exam at the door, which is what the old 30-second floor was
                    # for.
                    if left is not None and (left > 0 or running_for >= 30):
                        server_time_left = left
                except Exception:
                    pass
        else:
            client_started = data.get("started_at")
            started_at_ts = client_started if client_started else server_now
            if client_started:
                client_ts = client_started // 1000 if client_started > 1e10 else client_started
                if client_ts > server_now + 30:
                    return jsonify({"saved": True, "at": server_now, "error": "invalid_timer", "server_time_left": duration if duration else None}), 400
            started_at_dt = datetime.fromtimestamp(started_at_ts / 1000 if started_at_ts > 1e10 else started_at_ts, tz=timezone.utc).isoformat()
            if isinstance(answers, dict):
                answers["_device_info"] = {
                    "user_agent": request.headers.get("User-Agent", ""),
                    "ip_address": request.remote_addr,
                    "recorded_at": int(time.time()),
                }
            supabase.table("submissions").insert({
                "exam_id": exam_id,
                "student_id": g.user_id,
                "answers": answers,
                "score": 0,
                "max_score": 100,
                "status": "draft",
                "started_at": started_at_dt,
            }).execute()
            # A brand-new sitting gets the same answer as an existing one, so a
            # device that opens the page late in the window is told the truth about
            # the window rather than the full duration.
            _left = exam_window.seconds_left(
                exam_data or {}, started_at_dt,
                datetime.fromtimestamp(server_now, tz=timezone.utc))
            if _left is not None:
                server_time_left = _left
    except Exception as e:
        current_app.logger.warning("sync-draft failed for exam %s user %s: %s", exam_id, g.user_id, str(e))
    finally:
        _release_lock(rlock, lock_key)
    resp = {"saved": True, "at": server_now}
    if server_time_left is not None:
        resp["server_time_left"] = server_time_left
    return jsonify(resp)


@api_bp.route("/grade/auto-save/<submission_id>", methods=["POST"])
@login_required
def grade_auto_save(submission_id):
    """Auto-save teacher's in-progress grading."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    supabase = get_supabase()
    # Verify ownership: submission must belong to an exam the user owns or is in their school
    role = g.get("user_role")
    if role not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    sub_data = supabase.table("submissions").select("id, exam_id").eq("id", submission_id).single().execute().data
    if not sub_data:
        return jsonify({"error": "Submission tidak ditemukan"}), 404
    exam = supabase.table("exams").select("teacher_id, school_id").eq("id", sub_data["exam_id"]).single().execute().data
    if not exam:
        return jsonify({"error": "Exam tidak ditemukan"}), 404
    if role == "guru" and exam.get("teacher_id") != g.user_id:
        return jsonify({"error": "Forbidden"}), 403
    if role == "admin_sekolah" and str(exam.get("school_id")) != str(g.get("user_school_id")):
        return jsonify({"error": "Forbidden"}), 403
    # Save teacher_feedback draft into submission
    feedback = data.get("teacher_feedback", {})
    supabase.table("submissions") \
        .update({"teacher_feedback": feedback}) \
        .eq("id", submission_id) \
        .execute()
    return jsonify({"saved": True, "at": int(time.time())})


@api_bp.route("/grade/batch", methods=["POST"])
@login_required
def grade_batch():
    """Batch grade all submissions for an exam. Optimized for <2s grading speed."""
    import json, time as _time
    from app.utils.auth import get_supabase, teacher_or_admin_required
    data = request.get_json()
    exam_id = data.get("exam_id") if data else None
    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400
    if g.user_role not in ("guru", "super_admin", "admin_sekolah"):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    t0 = _time.time()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    # School/ownership access check
    if g.user_role == "guru" and exam.get("teacher_id") != g.user_id:
        return jsonify({"error": "Forbidden"}), 403
    if g.user_role == "admin_sekolah" and str(exam.get("school_id")) != str(g.get("user_school_id")):
        return jsonify({"error": "Forbidden"}), 403
    # Parse JSON fields that may be strings
    for _fld in ("answer_key", "question_types", "question_weights", "question_pages"):
        _v = exam.get(_fld)
        if isinstance(_v, str):
            try: exam[_fld] = json.loads(_v)
            except (json.JSONDecodeError, TypeError): exam[_fld] = {}
    answer_key = exam.get("answer_key") or {}
    question_types = exam.get("question_types") or {}
    question_weights = exam.get("question_weights") or {}
    total_q = exam.get("total_questions", 0)
    if not question_weights and total_q > 0:
        # This route's fallback has always been flatter than the student route's:
        # the objective questions share 100%, and the essays take their marks from
        # the teacher's own scores below. Kept as-is, only with the family decided
        # by the shared vocabulary, so a true/false question is not silently left
        # with no weight at all.
        objective = [i for i in range(total_q)
                     if is_objective(question_types.get(str(i)))]
        if objective:
            each = round(100 / len(objective), 2)
            for i in objective:
                question_weights[str(i)] = each
    subs = supabase.table("submissions").select("id,answers,penalty,teacher_feedback").eq("exam_id", exam_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
    graded = 0
    for sub in subs:
        # Parse JSON fields in each submission
        for _sf in ("answers", "teacher_feedback"):
            _sv = sub.get(_sf)
            if isinstance(_sv, str):
                try: sub[_sf] = json.loads(_sv)
                except (json.JSONDecodeError, TypeError): sub[_sf] = {}
        answers = sub.get("answers") or {}
        earned, _graded = earned_points(
            question_types, answer_key, answers, question_weights, total_q)
        fb = sub.get("teacher_feedback") or {}
        fb_scores = fb.get("scores", {}) or {}
        for qi, sv in fb_scores.items():
            if sv is not None and sv != "":
                ew = float(question_weights.get(str(qi), 0))
                if ew > 0:
                    earned += float(sv) / 100.0 * ew
        final = round(min(earned, 100), 2)
        penalty = float(sub.get("penalty") or 0)
        final = max(0, round(final - penalty, 2))
        # The same one rule as every other writer (this route's denominator was
        # already the paper's objective count; it is now the shared function, so
        # the numerator needs a key to be right against and an unkeyed question
        # cannot be counted correct).
        objective = objective_result(question_types, answer_key, answers, total_q)
        mcq_score = objective.score
        supabase.table("submissions").update({
            "score": mcq_score,
            "final_score": final,
        }).eq("id", sub["id"]).execute()
        graded += 1
    elapsed = round((_time.time() - t0) * 1000)
    return jsonify({
        "success": True,
        "graded": graded,
        "elapsed_ms": elapsed,
        "per_submission_ms": round(elapsed / max(graded, 1), 1),
    })


@api_bp.route("/student/penalty-appeal", methods=["POST"])
@login_required
def api_penalty_appeal():
    """Student submits a penalty appeal with reasoning."""
    data = request.get_json(silent=True) or {}
    submission_id = data.get("submission_id")
    reason = str(data.get("reason", "")).strip()
    if not submission_id or not reason:
        return jsonify({"error": "submission_id dan reason wajib diisi"}), 400
    if len(reason) < 10:
        return jsonify({"error": "Alasan minimal 10 karakter"}), 400

    supabase = get_supabase()
    # maybe_single(), not single(): single() RAISES when nothing matches, so an
    # unknown id — or another student's — answered 500 instead of 404/403.
    try:
        sub = row_or_none(
            supabase.table("submissions")
            .select("id,student_id,penalty,answers,status")
            .eq("id", submission_id).maybe_single().execute()
        )
    except Exception:
        sub = None
    if not sub:
        return jsonify({"error": "Submission tidak ditemukan"}), 404
    if sub["student_id"] != g.user_id:
        return jsonify({"error": "Bukan submission Anda"}), 403
    if sub["status"] not in ("submitted", "graded", "published"):
        return jsonify({"error": "Status submission tidak memungkinkan banding"}), 400

    answers = sub.get("answers") or {}
    if isinstance(answers, str):
        try: answers = json.loads(answers)
        except: answers = {}
    if not isinstance(answers, dict): answers = {}
    # Check existing pending appeal
    existing = answers.get("_penalty_appeal", {})
    if isinstance(existing, dict) and existing.get("status") == "pending":
        return jsonify({"error": "Sudah ada banding yang menunggu"}), 400

    answers["_penalty_appeal"] = {
        "reason": reason,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "current_penalty": float(sub.get("penalty") or 0),
        "responded_at": None,
        "response": None,
        "penalty_reduction": None,
    }
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    return jsonify({"success": True, "message": "Banding penalti telah dikirim. Guru akan mereview."})


@api_bp.route("/scan/save", methods=["POST"])
@login_required
@_rate_limit("30 per minute")
def scan_save():
    """Save scanned answers as a submission."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    exam_id = data.get("exam_id")
    student_id = data.get("student_id")
    answers = data.get("answers")
    nisn = data.get("nisn") or ""
    confidence = data.get("confidence") or {}
    needs_review = data.get("needs_review") or []
    scan_file_id = data.get("scan_file_id") or ""

    if not all([exam_id, student_id, answers]):
        return jsonify({"error": "exam_id, student_id, and answers are required"}), 400

    supabase = get_supabase()

    # Verify exam exists and user has access (teacher or admin_sekolah)
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    user_role = g.get("user_role")
    user_school = g.get("user_school_id")
    if user_role == "guru" and str(exam.get("teacher_id")) != g.user_id:
        return jsonify({"error": "Forbidden"}), 403
    if user_role == "admin_sekolah" and str(exam.get("school_id")) != str(user_school):
        return jsonify({"error": "Forbidden"}), 403
    if user_role not in ("guru", "admin_sekolah"):
        return jsonify({"error": "Forbidden"}), 403

    # Verify student belongs to same school (if profile has school_id)
    try:
        student_profile = supabase.table("profiles").select("school_id").eq("id", student_id).single().execute().data
        if student_profile and student_profile.get("school_id"):
            if user_role == "guru" and str(student_profile.get("school_id")) != str(user_school):
                return jsonify({"error": "Siswa bukan dari sekolah Anda"}), 403
    except Exception:
        pass

    # ── One sheet, one page at a time ────────────────────────────────────────
    # A sheet with more than 80 questions is printed as two pages, so it cannot
    # be one photograph and is scanned page by page. A save must therefore
    # *merge* the page it saw and leave the rest of the answer set alone —
    # replacing wholesale meant page 2 erased page 1 — and grading has to run
    # over the merged set, or the score would come from a single page's
    # questions. `first_question`/`last_question` come back from the scan
    # response; with neither, this is one sheet, one page, and the whole answer
    # set is replaced exactly as it was before.
    existing = supabase.table("submissions") \
        .select("*") \
        .eq("exam_id", exam_id) \
        .eq("student_id", student_id) \
        .execute().data

    stored = {}
    stored_late = False
    sitting_started = None
    if existing:
        stored = existing[0].get("answers") or {}
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except (ValueError, TypeError):
                stored = {}
        if not isinstance(stored, dict):
            stored = {}
        # The paper's own sitting and its existing late mark. Both come from the
        # row this save is about to write, not from the client: a client that could
        # name its own sitting start could move its own deadline.
        sitting_started = existing[0].get("started_at")
        stored_late = bool(existing[0].get("submitted_late"))

    # ── Was this paper late? ─────────────────────────────────────────────────
    # A scanned sheet has no session to measure, so `exam_window.late_arrival`
    # answers it from the student's own sitting, or the exam's start, or not at
    # all — and lets the teacher's own answer win when the request carries one.
    # `late` absent from the payload means "let the clock decide" (see the
    # checkbox on the scan screen: it sends a value only when a teacher ticks it).
    stated_late = data.get("late")
    arrived_at = datetime.now(timezone.utc)
    late = exam_window.late_arrival(exam, sitting_started, arrived_at, stated_late)
    if stated_late is None and stored_late:
        # Page 2 of a sheet never un-lates page 1: the paper's answers arrived late
        # once, whatever the next page's arrival time says. Only the teacher can
        # clear it, which is what `stated_late` is for.
        late = True

    def _qkey(k):
        """The question number of a stored key, or None for a metadata key."""
        s = str(k)
        return int(s) if s.isdigit() else None

    # Only a *filled* saved answer counts as answered, so a page that fills in a
    # question left blank by an earlier page still counts as new work.
    stored_marks = set()
    for k, v in stored.items():
        q = _qkey(k)
        if q is None:
            continue
        ans = v.get("answer", "") if isinstance(v, dict) else v
        if ans not in (None, ""):
            stored_marks.add(q)

    first = data.get("first_question")
    last = data.get("last_question")
    if first is None or last is None:
        covered = {int(k) for k in answers if str(k).isdigit()}
    else:
        covered = set(range(int(first), int(last) + 1))
    covered = {q for q in covered if str(q) in answers}
    adds_new = bool(covered - stored_marks)

    merged = {k: v for k, v in stored.items() if _qkey(k) not in covered}
    merged.update(answers)

    stored_review = stored.get("_needs_review") or []
    if isinstance(stored_review, (str, int)):
        stored_review = [stored_review]
    review = sorted({int(q) for q in stored_review
                     if str(q).lstrip("-").isdigit() and int(q) not in covered}
                    | {int(q) for q in needs_review
                       if str(q).lstrip("-").isdigit()})

    # Grade MCQ answers (handle string + dict formats, multi-value keys, bonus)
    key = exam.get("answer_key", {})
    if isinstance(key, str):
        key = json.loads(key)
    detected = merged
    qtypes = exam.get("question_types") or {}
    if isinstance(qtypes, str):
        try:
            qtypes = json.loads(qtypes)
        except (json.JSONDecodeError, TypeError):
            qtypes = {}
    # One rule for the whole app. This route divided by the number of answers the
    # *key* had, so a teacher who had keyed 2 of 10 questions got a perfect 100 for a
    # pupil who answered those two — a mark out of a paper that was two-tenths
    # marked, and the one a teacher sees on this screen. `total_questions` is what
    # the sheet was printed with, so `score` and `total` below now name the same
    # denominator and cannot disagree with the pupil's own result page.
    total_q = int(exam.get("total_questions") or 0)
    objective = objective_result(qtypes, key, detected, total_q)
    correct = objective.correct
    score = objective.score
    mcq_count = objective.out_of

    # Build enriched answers dict with confidence metadata
    enriched_answers = {}
    for k, v in merged.items():
        q = _qkey(k)
        if q is None:
            continue
        if q in covered:
            entry = {"answer": v}
            if k in confidence:
                entry["confidence"] = confidence[k]
            if str(k) in {str(x) for x in needs_review}:
                entry["review"] = True
            enriched_answers[k] = entry
        elif isinstance(v, dict):
            # A page this scan did not see keeps its own saved entry, confidence
            # and review flag included.
            enriched_answers[k] = v
        else:
            enriched_answers[k] = {"answer": v}
    if nisn:
        enriched_answers["_nisn"] = nisn
    elif stored.get("_nisn"):
        enriched_answers["_nisn"] = stored["_nisn"]
    if review:
        enriched_answers["_needs_review"] = review

    # Save original scan image permanently
    if scan_file_id:
        for ext in (".png", ".jpg"):
            src = os.path.join(UPLOAD_SCAN_DIR, "tmp", scan_file_id + ext)
            if os.path.exists(src):
                dst_dir = os.path.join(UPLOAD_SCAN_DIR, exam_id)
                os.makedirs(dst_dir, exist_ok=True)
                dst = os.path.join(dst_dir, student_id + ext)
                try:
                    os.rename(src, dst)
                    enriched_answers["_scan_image"] = f"/static/uploads/scans/{exam_id}/{student_id}{ext}"
                except Exception as e:
                    current_app.logger.warning("Failed to move scan image: %s", e)
                break

    update_data = {
        "answers": enriched_answers,
        "score": score,
        # `score` is a percentage (see `objective_result`), so its maximum is 100.
        # This used to store a question *count*, which is a different unit in the
        # same column — the shape that let one reader divide by a paper and another
        # by a key.
        "max_score": 100.0,
        "status": "graded",
        "submitted_late": late,
    }

    if existing:
        current_status = existing[0].get("status", "")
        # Don't overwrite already-graded/published submissions — but a scan of a
        # page that was never saved is not an overwrite, it is the other half of
        # the sheet, so it goes through and the union is re-graded. Re-scanning a
        # page already saved is still refused.
        if current_status in ("graded", "published") and not adds_new:
            # …except a late mark the teacher stated, which is a correction to the
            # record rather than an overwrite of the paper. Without this the wrong
            # automatic mark on an already-graded sheet could never be cleared from
            # the scan screen, because the answers are refused as a rewrite.
            if stated_late is not None and bool(stated_late) != stored_late:
                supabase.table("submissions") \
                    .update({"submitted_late": bool(stated_late)}) \
                    .eq("id", existing[0]["id"]).execute()
                return jsonify({"warning": "Sudah dinilai/dipublikasi, tidak ditimpa",
                                "score": score, "late": bool(stated_late),
                                "late_updated": True}), 200
            return jsonify({"warning": "Sudah dinilai/dipublikasi, tidak ditimpa",
                            "score": score, "late": stored_late}), 200
        new_status = "submitted" if (current_status == "submitted" and not adds_new) else "graded"
        sub = supabase.table("submissions") \
            .update(update_data) \
            .eq("id", existing[0]["id"]) \
            .execute().data
    else:
        update_data.update({"exam_id": exam_id, "student_id": student_id})
        sub = supabase.table("submissions") \
            .insert(update_data) \
            .execute().data

    return jsonify({
        "success": True,
        "score": score,
        "correct": correct,
        "total": mcq_count,
        # How many objective questions have no key yet, so the scan screen can say
        # why a sheet that looks perfect did not score 100 instead of leaving the
        # teacher to guess — the dashboard carries the same warning (teacher.py).
        "unkeyed": len(objective.unkeyed),
        "needs_review": len(review),
        # What the record now says about when this paper arrived, so the screen can
        # show it instead of leaving the teacher to open the results list to find
        # out what the clock just decided about their pupil.
        "late": late,
        "submission": sub[0] if sub else None,
        # What this save covered, and how much of the sheet is answered now, so
        # a two-page sheet can be tracked page by page instead of hoping.
        "questions_covered": (sorted(covered)[:1] + sorted(covered)[-1:]) if covered else [],
        "answered": sum(1 for k in enriched_answers if not str(k).startswith("_")),
    })


@api_bp.route("/scan/image/<exam_id>/<student_id>", methods=["GET"])
@login_required
def scan_image(exam_id, student_id):
    """Serve the annotated scan image with answer overlays."""
    # Verify school access: exam must belong to user's school (except super_admin)
    role = g.get("user_role")
    if role != "super_admin":
        supabase = get_supabase()
        exam = supabase.table("exams").select("school_id, teacher_id").eq("id", exam_id).single().execute().data
        if not exam:
            return jsonify({"error": "Exam tidak ditemukan"}), 404
        user_school = g.get("user_school_id")
        if role == "guru" and exam.get("teacher_id") != g.user_id:
            return jsonify({"error": "Forbidden"}), 403
        if role in ("guru", "admin_sekolah") and str(exam.get("school_id")) != str(user_school):
            return jsonify({"error": "Forbidden"}), 403
    for ext in (".png", ".jpg"):
        path = os.path.join(UPLOAD_SCAN_DIR, exam_id, student_id + ext)
        if os.path.exists(path):
            from app.services.omr_service import load_image, find_registration_marks
            img = load_image(open(path, "rb").read())
            if img is not None:
                # Get answers from submission
                supabase = get_supabase()
                sub = supabase.table("submissions").select("answers").eq("exam_id", exam_id).eq("student_id", student_id).limit(1).execute().data
                answers = {}
                if sub and sub[0].get("answers"):
                    raw = sub[0]["answers"]
                    if isinstance(raw, str):
                        raw = json.loads(raw)
                    for k, v in raw.items():
                        if isinstance(v, dict) and "answer" in v:
                            answers[k] = v["answer"]
                        elif not k.startswith("_"):
                            answers[k] = v
                # Draw annotated image
                from app.services.omr_service import draw_debug_image
                corners = find_registration_marks(img)
                annotated = draw_debug_image(img, corners, answers)
                return send_file(
                    io.BytesIO(annotated),
                    mimetype="image/jpeg",
                    as_attachment=False,
                )
            return send_file(path, mimetype=f"image/{ext[1:]}")
    return jsonify({"error": "Scan image not found"}), 404


@api_bp.route("/transaction/status", methods=["GET"])
@login_required
@require_role("admin_sekolah", "super_admin")
def transaction_status():
    order_id = request.args.get("order_id", "")
    if not order_id:
        return jsonify({"status": "error", "message": "order_id required"}), 400
    supabase = get_supabase()
    try:
        res = supabase.table("payment_transactions").select("status, gross_amount, activation_code, payment_type, payment_details").eq("order_id", order_id).limit(1).execute()
        if not res.data:
            return jsonify({"status": "not_found"})

        tx = res.data[0]
        # If pending, also check Midtrans for latest status
        if tx.get("status") == "pending":
            try:
                from app.services.midtrans_service import _load_midtrans_config
                cfg = _load_midtrans_config()
                if cfg.get("server_key"):
                    import midtransclient
                    snap = midtransclient.Snap(
                        is_production=cfg.get("is_production", False),
                        server_key=cfg["server_key"],
                        client_key=cfg.get("client_key", ""),
                    )
                    status_resp = snap.transaction.status(order_id)
                    trans_status = status_resp.get("transaction_status", "")
                    fraud_status = status_resp.get("fraud_status", "")
                    payment_type = status_resp.get("payment_type", "")

                    # Store payment details (VA, etc.)
                    details = {}
                    va = status_resp.get("va_numbers")
                    if va:
                        details["va_numbers"] = va
                    permata_va = status_resp.get("permata_va_number")
                    if permata_va:
                        details["permata_va"] = permata_va
                    payment_code = status_resp.get("payment_code")
                    if payment_code:
                        details["payment_code"] = payment_code

                    new_status = tx["status"]
                    if trans_status in ("settlement", "capture") and fraud_status != "deny":
                        new_status = "success"
                    elif trans_status == "expire":
                        new_status = "expired"
                    elif trans_status in ("deny", "cancel", "failure"):
                        new_status = "failure"

                    update = {"payment_type": payment_type, "payment_details": details}
                    if new_status != tx["status"]:
                        update["status"] = new_status
                        if new_status == "success":
                            update["activation_code"] = None  # will be set by _activate_subscription

                    supabase.table("payment_transactions").update(update).eq("id", tx["id"]).execute()

                    if new_status == "success" and tx.get("status") != "success":
                        from app.services.midtrans_service import _activate_subscription
                        _activate_subscription(tx["school_id"], tx.get("plan_id"), order_id, supabase)

                    tx["status"] = new_status
                    tx["payment_type"] = payment_type
                    tx["payment_details"] = details
            except Exception as e:
                current_app.logger.error(f"Midtrans status check error: {e}")

        return jsonify(tx)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)[:60]}), 500


@api_bp.route("/activation/redeem", methods=["POST"])
@login_required
@require_role("admin_sekolah", "super_admin")
def redeem_activation_code():
    data = request.get_json()
    code = (data or {}).get("code", "").strip().upper()
    if not code:
        return jsonify({"success": False, "message": "Kode aktivasi tidak boleh kosong"}), 400

    school_id = g.get("user_school_id")
    if not school_id:
        return jsonify({"success": False, "message": "Sekolah tidak terdaftar"}), 400

    supabase = get_supabase()

    # Find transaction with this activation code
    tx = supabase.table("payment_transactions") \
        .select("*") \
        .eq("activation_code", code) \
        .limit(1) \
        .execute()

    if not tx.data:
        # Also check school_subscriptions
        sub = supabase.table("school_subscriptions") \
            .select("*") \
            .eq("activation_code", code) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if sub.data:
            return jsonify({"success": False, "message": "Kode aktivasi ini sudah digunakan"}), 400
        return jsonify({"success": False, "message": "Kode aktivasi tidak ditemukan. Periksa kembali kode Anda."}), 404

    tx_data = tx.data[0]
    if tx_data["school_id"] != school_id:
        return jsonify({"success": False, "message": "Kode aktivasi bukan untuk sekolah Anda"}), 403

    # Check if already used
    existing = supabase.table("school_subscriptions") \
        .select("id") \
        .eq("activation_code", code) \
        .limit(1) \
        .execute()
    if existing.data:
        return jsonify({"success": False, "message": "Kode aktivasi sudah digunakan sebelumnya"}), 400

    # Activate
    from app.services.midtrans_service import _activate_subscription
    _activate_subscription(school_id, tx_data.get("plan_id"), tx_data["order_id"], supabase)

    return jsonify({"success": True, "message": "Langganan berhasil diaktifkan!"})


@api_bp.route("/ai/test-key", methods=["POST"])
@login_required
@require_role(*STAFF_ROLES)
def ai_test_key():
    data = request.get_json()
    key_id = (data or {}).get("key_id", "")
    if not key_id:
        raise ValidationError("key_id", "Parameter key_id diperlukan")
    from app.services.ai_service import test_api_key
    result = test_api_key(g.user_id, key_id)
    if result.get("error"):
        current_app.logger.warning("AI key test failed: %s", result["error"], extra={"user_id": g.user_id, "key_id": key_id})
    return jsonify(result)


@api_bp.route("/ai/test-key-raw", methods=["POST"])
@login_required
def ai_test_key_raw():
    """Test a raw API key (before saving to DB). Teachers/admins only."""
    if g.user_role not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    api_key = data.get("api_key", "")
    provider = data.get("provider", "gemini")
    if not api_key:
        return jsonify({"error": "API Key tidak boleh kosong"}), 400
    from app.services.ai_service import _test_key_internal
    key = {"api_key": api_key, "provider": provider, "label": "Test"}
    result = _test_key_internal(key)
    return jsonify(result)


@api_bp.route("/grade/ai-suggest", methods=["POST"])
@login_required
def ai_suggest():
    """AI grading suggestion — teachers/admins only."""
    if g.user_role not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json()
    if not data:
        raise ValidationError("request body", "Data tidak boleh kosong")
    question = data.get("question", "")
    answer = data.get("answer", "")
    max_score = data.get("max_score", 100)
    rubric = data.get("rubric", "")
    if not answer:
        raise ValidationError("answer", "Jawaban siswa kosong")
    from app.services.ai_service import suggest_grade
    result = suggest_grade(g.user_id, question, answer, max_score, rubric)
    if result.get("error"):
        raise AIProcessingError(result.get("provider", "unknown"), result["error"])
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
# STUDENT BULK IMPORT (via API)
# ═══════════════════════════════════════════════════════════

@api_bp.route("/students/import", methods=["POST"])
@login_required
def api_import_students():
    """Bulk import students via CSV (API version, uses pandas).

    Accepts multipart/form-data with a 'csv_file' field.
    CSV must have columns: nama, nisn (required); kelas, email, password (optional).

    Returns: { success, failed, total, errors, message }
    """
    if g.get("user_role") not in ("admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya admin sekolah yang boleh import siswa"}), 403
    import pandas as pd

    school_id = g.get("user_school_id")
    if not school_id:
        return jsonify({"error": denials.NO_SCHOOL}), 403

    if "csv_file" not in request.files:
        return jsonify({"error": "File CSV diperlukan"}), 400

    file = request.files["csv_file"]
    if file.filename == "":
        return jsonify({"error": "Pilih file terlebih dahulu"}), 400

    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "File harus berformat .csv"}), 422

    class_id = request.form.get("class_id") or None

    try:
        df = pd.read_csv(file, dtype=str).fillna("")
    except Exception as e:
        return jsonify({"error": f"Gagal membaca CSV: {str(e)[:100]}"}), 422

    # Validate required columns
    required = {"nama", "nisn"}
    missing_cols = required - set(df.columns.str.lower())
    if missing_cols:
        return jsonify({
            "error": f"Kolom wajib tidak ditemukan: {', '.join(sorted(missing_cols))}",
        }), 422

    # Normalize column names to lowercase
    df.columns = df.columns.str.lower()

    supabase = get_supabase()
    results = {"success": 0, "failed": 0, "total": len(df), "errors": []}

    # Process in chunks for memory efficiency
    chunk_size = 100
    for start in range(0, len(df), chunk_size):
        chunk = df.iloc[start:start + chunk_size]
        for idx, row in chunk.iterrows():
            row_num = idx + 2  # 1-indexed + header row
            try:
                nama = str(row.get("nama", "")).strip()
                nisn = str(row.get("nisn", "")).strip()
                if not nama or not nisn:
                    results["failed"] += 1
                    continue

                email = str(row.get("email", "")).strip() or None
                kelas = str(row.get("kelas", "")).strip()
                password = str(row.get("password", "")).strip() or "siswa123"

                # Resolve class from name
                resolved_class_id = class_id
                if not resolved_class_id and kelas:
                    c = row_or_none(
                        supabase.table("classes")
                        .select("id").eq("school_id", school_id)
                        .eq("name", kelas).maybe_single().execute()
                    )
                    if c:
                        resolved_class_id = c["id"]

                # The NISN check lived here and was scoped to this school, while
                # `students.nisn` is UNIQUE across the whole database -- so a NISN
                # used at another school passed the check and was rejected by the
                # index afterwards, leaving an auth user and a profile with no
                # students row. The helper checks globally and rolls back.
                create_student_account(
                    supabase, school_id=school_id, nisn=nisn, full_name=nama,
                    email=email or f"{nisn}@siswa.scan-grade.app",
                    password=password, class_id=resolved_class_id,
                )
                results["success"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "row": row_num,
                    "nisn": str(row.get("nisn", "")),
                    "message": getattr(e, "user_message", str(e))[:120],
                })

    current_app.logger.info(
        "API import: %d success, %d failed of %d",
        results["success"], results["failed"], results["total"],
        extra={"school_id": str(school_id)},
    )
    return jsonify({
        "success": True,
        "results": results,
        "message": f"Berhasil: {results['success']}, Gagal: {results['failed']}",
    })


# ═══════════════════════════════════════════════════════════
# EXAM REPORT & EXPORT
# ═══════════════════════════════════════════════════════════

@api_bp.route("/exams/<exam_id>/report", methods=["GET"])
@login_required
@require_role(*STAFF_ROLES)
def exam_report(exam_id):
    """Generate exam report with statistics. Supports ?format=excel for XLSX download.

    Returns JSON stats by default. With ?format=excel returns a formatted XLSX file.
    """
    from app.decorators.security import require_school_access

    # Manually apply school access check
    deco = require_school_access("exams", "exam_id")
    result = deco(lambda: None)(exam_id=exam_id)
    # If the decorator returns a non-None tuple, it's an error response
    if isinstance(result, tuple):
        return result

    supabase = get_supabase()
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Ujian tidak ditemukan"}), 404

    subs = supabase.table("submissions") \
        .select("*, profiles!inner(full_name, nisn)") \
        .eq("exam_id", exam_id) \
        .in_("status", ["submitted", "graded", "published"]) \
        .execute().data or []

    # Build student list
    students = []
    scores = []
    for s in subs:
        profile = s.get("profiles") or {}
        score = s.get("final_score") or s.get("score") or 0
        students.append({
            "nama": profile.get("full_name", ""),
            "nisn": profile.get("nisn", ""),
            "nilai": float(score),
            "status": s.get("status", ""),
            "penalty": float(s.get("penalty") or 0),
        })
        scores.append(float(score))

    if not scores:
        stats = {"mean": 0, "median": 0, "highest": 0, "lowest": 0, "count": 0}
    else:
        sorted_s = sorted(scores)
        n = len(sorted_s)
        stats = {
            "mean": round(sum(scores) / n, 2),
            "median": round(sorted_s[n // 2] if n % 2 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2, 2),
            "highest": max(scores),
            "lowest": min(scores),
            "count": n,
        }

    # Check format parameter
    fmt = request.args.get("format", "").lower()

    if fmt == "excel":
        return _generate_report_excel(exam, students, stats)

    # Default: JSON
    passing_score = exam.get("passing_score") or 0
    for s in students:
        s["keterangan"] = "Lulus" if s["nilai"] >= passing_score else "Tidak Lulus"

    return jsonify({
        "exam_title": exam.get("title", ""),
        "exam_id": exam_id,
        "stats": stats,
        "students": students,
        "passing_score": passing_score,
    })


def _generate_report_excel(exam, students, stats):
    """Generate formatted Excel report with Indonesian column names."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Laporan Nilai"

    # ── Styles ──
    title_font = Font(bold=True, size=14, color="1E293B")
    header_font = Font(bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    stat_label_font = Font(bold=True, size=11, color="1E293B")
    stat_value_font = Font(size=11, color="334155")
    border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    # ── Title ──
    ws.merge_cells("A1:E1")
    ws["A1"] = f"Laporan Nilai — {exam.get('title', 'Ujian')}"
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center")

    # ── Statistics section ──
    stat_start = 3
    row = stat_start
    for label, key in [("Rata-rata", "mean"), ("Median", "median"), ("Tertinggi", "highest"), ("Terendah", "lowest"), ("Jumlah Siswa", "count")]:
        ws.cell(row=row, column=1, value=label).font = stat_label_font
        ws.cell(row=row, column=2, value=stats.get(key, 0)).font = stat_value_font
        row += 1

    # ── Student table header ──
    header_row = row + 1
    headers = ["No", "Nama Siswa", "NISN", "Nilai", "Keterangan"]
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = border

    # ── Student data ──
    passing_score = exam.get("passing_score") or 0
    for i, s in enumerate(students, 1):
        row_idx = header_row + i
        ws.cell(row=row_idx, column=1, value=i).border = border
        ws.cell(row=row_idx, column=2, value=s["nama"]).border = border
        ws.cell(row=row_idx, column=3, value=s.get("nisn", "")).border = border
        ws.cell(row=row_idx, column=4, value=s["nilai"]).border = border
        ket = "Lulus" if s["nilai"] >= passing_score else "Tidak Lulus"
        ws.cell(row=row_idx, column=5, value=ket).border = border

    # ── Auto-width ──
    for col in range(1, 6):
        max_len = len(str(headers[col - 1]))
        for row_idx in range(header_row, header_row + len(students) + 1):
            val = ws.cell(row=row_idx, column=col).value
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[get_column_letter(col)].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    title_slug = "".join(c for c in exam.get("title", "laporan") if c.isalnum() or c in " _").strip()[:20]
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"laporan_{title_slug}.xlsx",
    )


# ── AI Essay Grading API ──

@api_bp.route("/ai/grade-essay", methods=["POST"])
@login_required
def ai_grade_essay():
    """Grade a single essay answer."""
    if g.get("user_role") not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya guru/admin yang boleh mengoreksi esai"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    submission_id = data.get("submission_id")
    question_index = data.get("question_index", 0)
    question_text = data.get("question_text", "")
    student_answer = data.get("student_answer", "")
    max_score = int(data.get("max_score", 100))
    rubric = data.get("rubric", "")
    diagram_context = data.get("diagram_context", "")
    lang = data.get("lang", "en")

    if not student_answer:
        return jsonify({"error": "Tidak ada jawaban siswa"}), 400

    from app.services.ai_grading import grade_essay as ai_grade
    result = ai_grade(
        teacher_id=g.user_id,
        submission_id=submission_id,
        question_index=question_index,
        question_text=question_text,
        student_answer=student_answer,
        max_score=max_score,
        rubric=rubric,
        diagram_context=diagram_context,
        lang=lang,
    )
    if "error" in result:
        return jsonify(result), 422
    return jsonify(result)


@api_bp.route("/ai/grade-bulk", methods=["POST"])
@login_required
def ai_grade_bulk():
    """Grade all pending essay questions for an exam or submission list."""
    if g.get("user_role") not in ("guru", "admin_sekolah", "super_admin"):
        return jsonify({"error": "Hanya guru/admin yang boleh mengoreksi esai"}), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    exam_id = data.get("exam_id")
    submission_ids = data.get("submission_ids")

    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    from app.services.ai_grading import grade_bulk_essays
    result = grade_bulk_essays(
        teacher_id=g.user_id,
        exam_id=exam_id,
        submission_ids=submission_ids,
    )
    return jsonify(result)


# ═══════════════════════════════════════════════════════════
# PENGUMUMAN (Broadcast Notifications)
# ═══════════════════════════════════════════════════════════

@api_bp.route("/pengumuman/unread-count", methods=["GET"])
@login_required
def api_pengumuman_unread_count():
    """Get unread pengumuman count for current user."""
    supabase = get_supabase()
    try:
        result = supabase.rpc("get_unread_pengumuman_count", {"user_id": g.user_id}).execute()
        return jsonify({"unread_count": result.data or 0})
    except Exception:
        try:
            res = supabase.table("pengumuman_read").select("pengumuman_id", count="exact").eq("reader_id", g.user_id).execute()
            read_ids = set(r["pengumuman_id"] for r in (res.data or []))
            role = g.get("user_role")
            all_p = supabase.table("pengumuman").select("id").eq("target_role", role).is_("is_archived", "false").execute().data or []
            unread = sum(1 for p in all_p if p["id"] not in read_ids)
            return jsonify({"unread_count": unread})
        except Exception:
            return jsonify({"unread_count": 0})


@api_bp.route("/pengumuman", methods=["POST"])
@login_required
def api_create_pengumuman():
    """Create broadcast notification."""
    role = g.get("user_role")
    if role == "murid":
        return jsonify({"error": "Siswa tidak boleh mengirim pengumuman"}), 403
    data = request.get_json() or {}
    title = str(data.get("title", "")).strip()
    content = str(data.get("content", "")).strip()
    target_role = data.get("target_role")
    school_id = data.get("school_id")
    class_id = data.get("class_id")
    recipient_ids = data.get("recipient_ids")
    attachment_url = data.get("attachment_url")

    if not title or not content or not target_role:
        return jsonify({"error": "Judul, isi, dan target_role wajib diisi"}), 400

    supabase = get_supabase()
    role = g.get("user_role")
    uid = g.user_id

    # RBAC validation — per User-matrix
    if role == "murid":
        return jsonify({"error": "Murid tidak bisa membuat pengumuman"}), 403
    allowed_targets = _get_allowed_recipient_roles(role)
    if target_role not in allowed_targets:
        return jsonify({"error": f"Anda tidak bisa mengirim pengumuman ke {target_role}"}), 403
    if role in ("admin_sekolah", "guru"):
        school_id = g.get("user_school_id")
        # Without a school this row would be written with school_id NULL, which no
        # reader's school filter matches -- silently invisible rather than a leak.
        # Say so instead of pretending it was sent.
        if not school_id:
            return jsonify({"error": "Akun Anda belum terhubung ke sekolah, pengumuman tidak bisa dikirim"}), 400

    payload = {
        "sender_id": uid,
        "sender_role": role,
        "target_role": target_role,
        "title": title,
        "content": content,
    }
    if school_id:
        payload["school_id"] = school_id
    if class_id:
        payload["class_id"] = class_id
    if recipient_ids and isinstance(recipient_ids, list) and len(recipient_ids) > 0:
        try:
            payload["specific_recipients"] = recipient_ids
        except Exception:
            pass
    if attachment_url:
        payload["attachment_url"] = attachment_url
    expires_at = data.get("expires_at")
    if expires_at:
        payload["expires_at"] = expires_at

    try:
        res = supabase.table("pengumuman").insert(payload).execute()
        return jsonify(res.data[0]), 201
    except Exception as e:
        # No fallback. The previous retry wrote `school_id = 1` when the INT column
        # rejected a school UUID, which put every school's broadcast on one value
        # and made the read filter match all of them. A failure here is a real
        # failure and must be visible.
        return jsonify({"error": f"Gagal: {str(e)[:100]}"}), 500


@api_bp.route("/pengumuman", methods=["GET"])
@login_required
def api_list_pengumuman():
    """List pengumuman for current user."""
    supabase = get_supabase()
    role = g.get("user_role")
    uid = g.user_id
    limit = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    unread_only = request.args.get("unread_only", "false") == "true"

    try:
        school_id = g.get("user_school_id")

        # Super admin sees ALL pengumuman; others see targeted at their role OR sent by them
        if role == "super_admin":
            query = supabase.table("pengumuman").select("*").is_("is_archived", "false")
        else:
            query = supabase.table("pengumuman").select("*").is_("is_archived", "false").or_("target_role.eq." + role + ",sender_id.eq." + uid)

        # School scope filter (skip for super_admin).
        #
        # This used to probe the column and fall back to `.eq("school_id", 1)`,
        # the single value a legacy INT column accepted. Two ways that leaked
        # across tenants: the fallback matched *every* school's announcement, and
        # it also misfired on a correct empty result (`if not probe` is True when
        # a school simply has no announcements yet). Migration 024 retyped the
        # column to uuid, so the scope is applied directly and unconditionally.
        if school_id and role != "super_admin":
            query = query.eq("school_id", school_id)

        data = query.order("created_at", desc=True).limit(limit).offset(offset).execute().data or []
    except Exception as e:
        # Swallowing this silently made a transient database error look exactly
        # like "this school has no announcements" -- and it hid a real one while
        # this very fix was being verified. Still fails soft (announcements are
        # not worth a 500), but no longer invisibly.
        # Read the school from `g`, not the local: the exception may have been
        # raised before `school_id` was assigned, and this handler must not be
        # the thing that turns a soft failure into a 500.
        current_app.logger.warning("pengumuman list failed for school=%s: %s",
                                   g.get("user_school_id"), e)
        data = []

    read_ids = set()
    try:
        pr = supabase.table("pengumuman_read").select("pengumuman_id").eq("reader_id", uid).execute().data or []
        read_ids = set(r["pengumuman_id"] for r in pr)
    except Exception:
        pass

    # Filter by specific_recipients if column exists & populated
    try:
        data = [p for p in data if p.get("specific_recipients") is None or uid in p["specific_recipients"]]
    except Exception:
        pass

    result = []
    for p in data:
        if unread_only and p["id"] in read_ids:
            continue
        try:
            rc = supabase.table("pengumuman_read").select("id", count="exact").eq("pengumuman_id", p["id"]).execute()
            read_by_count = rc.count or 0
        except Exception:
            read_by_count = 0
        result.append({
            **p,
            "is_read": p["id"] in read_ids,
            "read_by_count": read_by_count,
        })

    return jsonify(result)


@api_bp.route("/pengumuman/classes", methods=["GET"])
@login_required
def api_pengumuman_classes():
    """Get classes for current user's school (for broadcast targeting)."""
    supabase = get_supabase()
    school_id = g.get("user_school_id")
    if not school_id:
        return jsonify({"classes": []})
    try:
        classes = supabase.table("classes").select("id, name").eq("school_id", school_id).order("name").execute().data or []
        return jsonify({"classes": classes})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "classes": []}), 500


@api_bp.route("/pengumuman/recipients-by-role", methods=["GET"])
@login_required
def api_pengumuman_recipients():
    """Get users by role for specific recipient selection (scoped to school/NPSN)."""
    supabase = get_supabase()
    role = request.args.get("role")
    class_id = request.args.get("class_id")
    if not role:
        return jsonify({"error": "role required"}), 400
    allowed = _get_allowed_recipient_roles(g.get("user_role"))
    if role not in allowed and g.get("user_role") != "super_admin":
        return jsonify({"error": "Forbidden"}), 403
    try:
        query = supabase.table("profiles").select("id, full_name").eq("role", role)
        school_id = g.get("user_school_id")
        if school_id:
            query = query.eq("school_id", school_id)
        if class_id:
            query = query.eq("class_id", class_id)
        users = query.order("full_name").execute().data or []
        return jsonify({"users": users})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "users": []}), 500


@api_bp.route("/pengumuman/<uuid:pengumuman_id>", methods=["GET"])
@login_required
def api_get_pengumuman(pengumuman_id):
    """Get single pengumuman with metadata."""
    supabase = get_supabase()
    pengumuman_id = str(pengumuman_id)
    try:
        p = supabase.table("pengumuman").select("*").eq("id", pengumuman_id).single().execute().data
        if not p:
            return jsonify({"error": "Pengumuman tidak ditemukan"}), 404
        # Check access
        role = g.get("user_role")
        if p["target_role"] != role and p["sender_id"] != g.user_id and role != "super_admin":
            return jsonify({"error": "Tidak berhak mengakses"}), 403
        readers = supabase.table("pengumuman_read").select("reader_id, read_at").eq("pengumuman_id", pengumuman_id).execute().data or []
        reader_ids = [r["reader_id"] for r in readers]
        enriched = []
        if reader_ids:
            profs = supabase.table("profiles").select("id, full_name").in_("id", reader_ids).execute().data or []
            prof_map = {pr["id"]: pr["full_name"] for pr in profs}
            for r in readers:
                enriched.append({"id": r["reader_id"], "full_name": prof_map.get(r["reader_id"], r["reader_id"][:8]), "read_at": r["read_at"]})
        is_read = any(r["reader_id"] == g.user_id for r in readers)
        return jsonify({**p, "is_read": is_read, "read_by_count": len(enriched), "readers": enriched})
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/pengumuman/<uuid:pengumuman_id>/read-status", methods=["GET"])
@login_required
def api_pengumuman_read_status(pengumuman_id):
    """Get read/unread breakdown for a pengumuman (sender only)."""
    supabase = get_supabase()
    pengumuman_id = str(pengumuman_id)
    try:
        p = supabase.table("pengumuman").select("*").eq("id", pengumuman_id).single().execute().data
        if not p:
            return jsonify({"error": "Not found"}), 404
        if p["sender_id"] != g.user_id and g.get("user_role") != "super_admin":
            return jsonify({"error": "Forbidden"}), 403

        # Get all readers
        reads = supabase.table("pengumuman_read").select("reader_id, read_at").eq("pengumuman_id", pengumuman_id).execute().data or []
        read_ids = set(r["reader_id"] for r in reads)

        # Get target recipients: either all profiles matching target_role (within school)
        # or specific_recipients if set
        target_role = p.get("target_role")
        school_id = p.get("school_id")
        specific = p.get("specific_recipients")
        if specific and isinstance(specific, list) and len(specific) > 0:
            target_query = supabase.table("profiles").select("id, full_name").in_("id", specific)
        else:
            target_query = supabase.table("profiles").select("id, full_name").eq("role", target_role)
            if school_id:
                # Was a try/except falling back to the legacy `school_id = 1`, which
                # would list every school's users as this announcement's audience.
                # The column is uuid since migration 024, so scope it directly.
                target_query = target_query.eq("school_id", school_id)
        all_targets = target_query.execute().data or []

        seen = set()
        enriched_targets = []
        for t in all_targets:
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            enriched_targets.append(t)

        read_users = []
        unread_users = []
        read_map = {r["reader_id"]: r["read_at"] for r in reads}

        for t in enriched_targets:
            if t["id"] in read_map:
                read_users.append({"id": t["id"], "full_name": t["full_name"], "read_at": read_map[t["id"]]})
            else:
                unread_users.append({"id": t["id"], "full_name": t["full_name"]})

        total = len(enriched_targets)
        read_count = len(read_users)
        unread_count = len(unread_users)

        return jsonify({
            "total_target": total,
            "total_read": read_count,
            "total_unread": unread_count,
            "read_percentage": round((read_count / total * 100), 1) if total > 0 else 0,
            "unread_percentage": round((unread_count / total * 100), 1) if total > 0 else 0,
            "read_users": read_users,
            "unread_users": unread_users,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@api_bp.route("/pengumuman/<uuid:pengumuman_id>", methods=["PUT"])
@login_required
def api_update_pengumuman(pengumuman_id):
    """Edit pengumuman (title, content, expires_at)."""
    supabase = get_supabase()
    pengumuman_id = str(pengumuman_id)
    role = g.get("user_role")
    try:
        p = supabase.table("pengumuman").select("id, sender_id").eq("id", pengumuman_id).single().execute().data
        if not p:
            return jsonify({"error": "Pengumuman tidak ditemukan"}), 404
        if p["sender_id"] != g.user_id and role != "super_admin":
            return jsonify({"error": "Tidak berhak mengedit"}), 403
        data = {k: request.json[k] for k in ("title", "content", "expires_at") if k in request.json}
        if not data:
            return jsonify({"error": "Tidak ada data yang diubah"}), 400
        supabase.table("pengumuman").update(data).eq("id", pengumuman_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/pengumuman/<uuid:pengumuman_id>", methods=["DELETE"])
@login_required
def api_delete_pengumuman(pengumuman_id):
    """Soft-delete pengumuman."""
    supabase = get_supabase()
    pengumuman_id = str(pengumuman_id)
    role = g.get("user_role")
    try:
        p = supabase.table("pengumuman").select("id, sender_id").eq("id", pengumuman_id).single().execute().data
        if not p:
            return jsonify({"error": "Pengumuman tidak ditemukan"}), 404
        if p["sender_id"] != g.user_id and role != "super_admin":
            return jsonify({"error": "Tidak berhak menghapus"}), 403
        supabase.table("pengumuman").update({"is_archived": True}).eq("id", pengumuman_id).execute()
        supabase.table("pengumuman_read").delete().eq("pengumuman_id", pengumuman_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/pengumuman/<uuid:pengumuman_id>/mark-read", methods=["POST"])
@login_required
def api_mark_read_pengumuman(pengumuman_id):
    """Mark pengumuman as read for current user."""
    supabase = get_supabase()
    pengumuman_id = str(pengumuman_id)
    try:
        supabase.table("pengumuman_read").insert({
            "pengumuman_id": pengumuman_id,
            "reader_id": g.user_id,
        }, on_conflict=["pengumuman_id", "reader_id"], ignore_duplicates=True).execute()
        return jsonify({"success": True})
    except Exception:
        try:
            existing = supabase.table("pengumuman_read").select("id").eq("pengumuman_id", pengumuman_id).eq("reader_id", g.user_id).execute().data
            if not existing:
                supabase.table("pengumuman_read").insert({
                    "pengumuman_id": pengumuman_id, "reader_id": g.user_id
                }).execute()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)[:100]}), 500


# ═══════════════════════════════════════════════════════════
# PERCAKAPAN (1-on-1 Chat)
# ═══════════════════════════════════════════════════════════

def _normalize_pair(uid_a, uid_b):
    """Return (smaller, larger) UUID string pair."""
    a, b = str(uid_a), str(uid_b)
    return (a, b) if a < b else (b, a)


def _get_allowed_recipient_roles(user_role):
    """Return list of roles the user can message (used for chat + broadcast)."""
    if user_role == "super_admin":
        return ["guru", "murid", "admin_sekolah", "super_admin"]
    if user_role == "admin_sekolah":
        return ["super_admin", "guru", "murid"]
    if user_role == "guru":
        return ["admin_sekolah", "guru", "murid"]
    if user_role == "murid":
        return ["guru", "admin_sekolah"]
    return []


@api_bp.route("/percakapan", methods=["GET"])
@login_required
def api_list_percakapan():
    """List conversations for current user."""
    supabase = get_supabase()
    uid = g.user_id
    limit = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    archived = request.args.get("archived", "false") == "true"

    try:
        query = supabase.table("percakapan").select("*").or_("user_a_id.eq." + uid + ",user_b_id.eq." + uid)
        if not archived:
            query = query.eq("is_archived", False)
        data = query.order("last_message_at", desc=True).limit(limit).offset(offset).execute().data or []
        # Filter to same-school conversations only (for non-super_admin)
        school_id = g.get("user_school_id")
        role = g.get("user_role", "")
        if school_id and role != "super_admin":
            filtered = []
            for c in data:
                other_id = c["user_b_id"] if uid == c["user_a_id"] else c["user_a_id"]
                try:
                    p = supabase.table("profiles").select("school_id").eq("id", other_id).single().execute().data
                    if p and p.get("school_id") and str(p["school_id"]) == str(school_id):
                        filtered.append(c)
                    elif not p or not p.get("school_id"):
                        filtered.append(c)
                except Exception:
                    filtered.append(c)
            data = filtered
    except Exception as e:
        return jsonify({"error": str(e)[:100], "percakapan": []}), 500

    result = []
    for c in data:
        other_id = c["user_b_id"] if uid == c["user_a_id"] else c["user_a_id"]
        other_name = other_id[:8]
        other_role = ""
        try:
            p = supabase.table("profiles").select("full_name, role").eq("id", other_id).single().execute().data
            if p:
                other_name = p.get("full_name", other_id[:8])
                other_role = p.get("role", "")
        except Exception:
            pass
        last_msg = None
        try:
            msgs = supabase.table("pesan").select("content, created_at, is_deleted").eq("percakapan_id", c["id"]).order("created_at", desc=True).limit(1).execute().data or []
            if msgs:
                m = msgs[0]
                if m.get("is_deleted"):
                    last_msg = "[Pesan dihapus]"
                else:
                    last_msg = m.get("content", "")[:80]
        except Exception:
            pass
        unread = 0
        try:
            uc = supabase.table("pesan").select("id", count="exact").eq("percakapan_id", c["id"]).neq("sender_id", uid).eq("is_read", False).eq("is_deleted", False).execute()
            unread = uc.count or 0
        except Exception:
            pass
        result.append({
            "id": c["id"],
            "other_id": other_id,
            "other_name": other_name,
            "other_role": other_role,
            "subject": c.get("subject") or "",
            "last_message": last_msg or "",
            "last_message_at": c.get("last_message_at"),
            "is_archived": c.get("is_archived", False),
            "unread_count": unread,
        })

    return jsonify({"percakapan": result})


@api_bp.route("/percakapan", methods=["POST"])
@login_required
def api_create_percakapan():
    """Create new 1-on-1 conversation."""
    data = request.get_json() or {}
    recipient_id = data.get("recipient_id")
    if not recipient_id:
        return jsonify({"error": "recipient_id wajib diisi"}), 400

    supabase = get_supabase()
    uid = g.user_id
    role = g.get("user_role")

    if recipient_id == uid:
        return jsonify({"error": "Tidak bisa chat diri sendiri"}), 400

    # Validate recipient exists and role is allowed
    try:
        recipient = supabase.table("profiles").select("id, role, school_id").eq("id", recipient_id).single().execute().data
        if not recipient:
            return jsonify({"error": "Penerima tidak ditemukan"}), 404
        allowed = _get_allowed_recipient_roles(role)
        if recipient["role"] not in allowed:
            return jsonify({"error": f"Tidak boleh chat dengan {recipient['role']}"}), 403
        # Also check reverse: recipient must be allowed to chat with sender
        recip_allowed = _get_allowed_recipient_roles(recipient["role"])
        if role not in recip_allowed:
            return jsonify({"error": "Penerima tidak boleh chat dengan anda"}), 403
        # School scoping: must be same school (except super_admin)
        if role != "super_admin":
            my_school = g.get("user_school_id")
            if my_school and recipient.get("school_id") and str(my_school) != str(recipient["school_id"]):
                return jsonify({"error": "Tidak bisa chat dengan pengguna di luar sekolah"}), 403
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500

    # Normalize pair
    ua, ub = _normalize_pair(uid, recipient_id)

    try:
        existing = supabase.table("percakapan").select("id").eq("user_a_id", ua).eq("user_b_id", ub).limit(1).execute().data
        if existing:
            return jsonify({"percakapan": existing[0]}), 200
        now = datetime.now(timezone.utc).isoformat()
        res = supabase.table("percakapan").insert({
            "user_a_id": ua, "user_b_id": ub, "last_message_at": now,
        }).execute()
        return jsonify({"percakapan": res.data[0]}), 201
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/percakapan/<uuid:percakapan_id>", methods=["PUT"])
@login_required
def api_update_percakapan(percakapan_id):
    """Archive/unarchive conversation."""
    supabase = get_supabase()
    percakapan_id = str(percakapan_id)
    uid = g.user_id
    is_archived = request.json.get("is_archived", True)
    try:
        c = supabase.table("percakapan").select("id, user_a_id, user_b_id").eq("id", percakapan_id).single().execute().data
        if not c:
            return jsonify({"error": "Percakapan tidak ditemukan"}), 404
        if uid != c["user_a_id"] and uid != c["user_b_id"]:
            return jsonify({"error": "Bukan peserta percakapan"}), 403
        supabase.table("percakapan").update({"is_archived": is_archived}).eq("id", percakapan_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/percakapan/<uuid:percakapan_id>", methods=["DELETE"])
@login_required
def api_delete_percakapan(percakapan_id):
    """Delete conversation (super_admin only)."""
    supabase = get_supabase()
    percakapan_id = str(percakapan_id)
    role = g.get("user_role")
    if role != "super_admin":
        return jsonify({"error": "Hanya super admin"}), 403
    try:
        supabase.table("pesan").delete().eq("percakapan_id", percakapan_id).execute()
        supabase.table("percakapan").delete().eq("id", percakapan_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/percakapan/<uuid:percakapan_id>/pesan", methods=["POST"])
@login_required
def api_send_pesan(percakapan_id):
    """Send message in conversation."""
    data = request.get_json() or {}
    content = str(data.get("content", "")).strip()
    if not content:
        return jsonify({"error": "Pesan wajib diisi"}), 400

    supabase = get_supabase()
    uid = g.user_id
    percakapan_id = str(percakapan_id)

    try:
        c = supabase.table("percakapan").select("id, user_a_id, user_b_id").eq("id", percakapan_id).single().execute().data
        if not c:
            return jsonify({"error": "Percakapan tidak ditemukan"}), 404
        if uid != c["user_a_id"] and uid != c["user_b_id"]:
            return jsonify({"error": "Bukan peserta percakapan"}), 403
        role = g.get("user_role")
        other_id = c["user_b_id"] if uid == c["user_a_id"] else c["user_a_id"]
        # Verify role pair still valid
        other = supabase.table("profiles").select("role").eq("id", other_id).single().execute().data
        if other:
            allowed = _get_allowed_recipient_roles(role)
            if other["role"] not in allowed:
                return jsonify({"error": "Tidak boleh kirim pesan ke pengguna ini"}), 403

        media_urls = data.get("media_urls", [])
        res = supabase.table("pesan").insert({
            "percakapan_id": percakapan_id,
            "sender_id": uid,
            "content": content,
            "media_urls": media_urls or [],
        }).execute()
        return jsonify(res.data[0]), 201
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/percakapan/<uuid:percakapan_id>/pesan", methods=["GET"])
@login_required
def api_list_pesan(percakapan_id):
    """List messages in conversation."""
    supabase = get_supabase()
    uid = g.user_id
    percakapan_id = str(percakapan_id)
    limit = min(int(request.args.get("limit", 30)), 100)
    offset = int(request.args.get("offset", 0))

    try:
        c = supabase.table("percakapan").select("id, user_a_id, user_b_id").eq("id", percakapan_id).single().execute().data
        if not c:
            return jsonify({"error": "Percakapan tidak ditemukan"}), 404
        if uid != c["user_a_id"] and uid != c["user_b_id"]:
            return jsonify({"error": "Bukan peserta percakapan"}), 403

        # Auto-mark messages as read
        try:
            supabase.rpc("mark_pesan_read", {"percakapan_id": percakapan_id, "user_id": uid}).execute()
        except Exception:
            supabase.table("pesan").update({"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()}).eq("percakapan_id", percakapan_id).neq("sender_id", uid).eq("is_read", False).execute()

        msgs = supabase.table("pesan").select("*").eq("percakapan_id", percakapan_id).order("created_at").limit(limit).offset(offset).execute().data or []

        # Enrich sender names
        sender_ids = set(m["sender_id"] for m in msgs)
        profs = {}
        if sender_ids:
            pd = supabase.table("profiles").select("id, full_name").in_("id", list(sender_ids)).execute().data or []
            profs = {p["id"]: p["full_name"] for p in pd}
        for m in msgs:
            m["sender_name"] = profs.get(m["sender_id"], m["sender_id"][:8])
        return jsonify({"pesan": msgs})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "pesan": []}), 500


@api_bp.route("/percakapan/<uuid:percakapan_id>/unread-count", methods=["GET"])
@login_required
def api_percakapan_unread_count(percakapan_id):
    """Get unread message count for a conversation."""
    supabase = get_supabase()
    uid = g.user_id
    percakapan_id = str(percakapan_id)
    try:
        uc = supabase.table("pesan").select("id", count="exact").eq("percakapan_id", percakapan_id).neq("sender_id", uid).eq("is_read", False).eq("is_deleted", False).execute()
        return jsonify({"unread_count": uc.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "unread_count": 0}), 500


# ═══════════════════════════════════════════════════════════
# PESAN (Individual Message Operations)
# ═══════════════════════════════════════════════════════════

@api_bp.route("/pesan/<uuid:pesan_id>", methods=["PUT"])
@login_required
def api_update_pesan(pesan_id):
    """Edit message content."""
    supabase = get_supabase()
    pesan_id = str(pesan_id)
    uid = g.user_id
    content = str(request.json.get("content", "")).strip()
    if not content:
        return jsonify({"error": "Pesan wajib diisi"}), 400
    try:
        m = supabase.table("pesan").select("id, sender_id, created_at").eq("id", pesan_id).single().execute().data
        if not m:
            return jsonify({"error": "Pesan tidak ditemukan"}), 404
        if m["sender_id"] != uid:
            return jsonify({"error": "Tidak berhak mengedit"}), 403
        supabase.table("pesan").update({
            "content": content, "is_edited": True, "edited_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", pesan_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


@api_bp.route("/pesan/<uuid:pesan_id>", methods=["DELETE"])
@login_required
def api_delete_pesan(pesan_id):
    """Soft-delete message."""
    supabase = get_supabase()
    pesan_id = str(pesan_id)
    uid = g.user_id
    role = g.get("user_role")
    try:
        m = supabase.table("pesan").select("id, sender_id").eq("id", pesan_id).single().execute().data
        if not m:
            return jsonify({"error": "Pesan tidak ditemukan"}), 404
        if m["sender_id"] != uid and role != "super_admin":
            return jsonify({"error": "Tidak berhak menghapus"}), 403
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("pesan").update({"is_deleted": True, "deleted_at": now}).eq("id", pesan_id).execute()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)[:100]}), 500


# ═══════════════════════════════════════════════════════════
# RECIPIENT SEARCH (for creating new chat)
# ═══════════════════════════════════════════════════════════

@api_bp.route("/percakapan/recipients", methods=["GET"])
@login_required
def api_search_recipients():
    """Search users that current user can start a chat with."""
    supabase = get_supabase()
    role = g.get("user_role")
    q = request.args.get("q", "").strip()
    allowed = _get_allowed_recipient_roles(role)
    try:
        query = supabase.table("profiles").select("id, full_name, role").in_("role", allowed)
        school_id = g.get("user_school_id")
        if school_id and role != "super_admin":
            query = query.eq("school_id", school_id)
        if q:
            query = query.ilike("full_name", f"%{q}%")
        users = query.limit(20).execute().data or []
        return jsonify({"users": users})
    except Exception:
        return jsonify({"users": []})

@api_bp.route("/percakapan/contacts", methods=["GET"])
@login_required
def api_contacts():
    """Get all contacts the user can chat with, merged with conversation info."""
    supabase = get_supabase()
    role = g.get("user_role")
    uid = g.user_id
    allowed = _get_allowed_recipient_roles(role)
    school_id = g.get("user_school_id")
    try:
        query = supabase.table("profiles").select("id, full_name, role").in_("role", allowed)
        if school_id and role != "super_admin":
            query = query.eq("school_id", school_id)
        profiles = query.limit(200).execute().data or []

        convs = supabase.table("percakapan").select("*").or_("user_a_id.eq." + uid + ",user_b_id.eq." + uid).order("last_message_at", desc=True).limit(200).execute().data or []
        conv_map = {}
        for c in convs:
            other = c["user_b_id"] if uid == c["user_a_id"] else c["user_a_id"]
            conv_map[other] = c

        contacts = []
        for p in profiles:
            if p["id"] == uid: continue
            conv = conv_map.get(p["id"])
            conv_id = conv["id"] if conv else None
            last_msg = ""
            unread = 0
            if conv:
                try:
                    ms = supabase.table("pesan").select("content, created_at, is_deleted").eq("percakapan_id", conv["id"]).order("created_at", desc=True).limit(1).execute().data or []
                    if ms:
                        m = ms[0]
                        last_msg = "[Pesan dihapus]" if m.get("is_deleted") else (m.get("content","")[:80])
                    uc = supabase.table("pesan").select("id", count="exact").eq("percakapan_id", conv["id"]).neq("sender_id", uid).eq("is_read", False).execute()
                    unread = uc.count or 0
                except Exception:
                    pass
            contacts.append({
                "id": p["id"],
                "full_name": p["full_name"],
                "role": p["role"],
                "initial": (p["full_name"] or "?")[0].upper(),
                "conversation_id": conv_id,
                "last_message": last_msg,
                "unread_count": unread,
                "last_message_at": conv["last_message_at"] if conv else None,
            })
        contacts.sort(key=lambda x: (0 if x["last_message_at"] else 1, x.get("last_message_at") or ""), reverse=True)
        return jsonify({"contacts": contacts})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "contacts": []}), 500

@api_bp.route("/account/export-data", methods=["GET"])
@login_required
def api_export_data():
    """Export all personal data (UU PDP right to data portability)."""
    from app.services.data_retention_service import export_user_data
    data = export_user_data(g.user_id)
    return jsonify(data)


@api_bp.route("/account/delete-request", methods=["POST"])
@login_required
def api_delete_request():
    """Submit account deletion request (UU PDP right to erasure)."""
    from app.services.data_retention_service import request_deletion
    data = request.get_json() or {}
    reason = str(data.get("reason", "")).strip()
    result, status = request_deletion(g.user_id, reason)
    return jsonify(result), status


@api_bp.route("/account/delete-request/cancel", methods=["POST"])
@login_required
def api_cancel_delete_request():
    """Cancel a pending deletion request."""
    from app.services.data_retention_service import cancel_deletion_request
    result, status = cancel_deletion_request(g.user_id)
    return jsonify(result), status


@api_bp.route("/account/deletion-status", methods=["GET"])
@login_required
def api_deletion_status():
    """Check if user has a pending deletion request."""
    supabase = get_supabase()
    try:
        res = supabase.table("deletion_requests").select("id, status, reason, requested_at").eq("user_id", g.user_id).order("requested_at", desc=True).limit(1).execute().data
        return jsonify({"request": res[0] if res else None})
    except Exception:
        return jsonify({"request": None})


@api_bp.route("/admin/deletion-requests", methods=["GET"])
@login_required
def api_admin_deletion_requests():
    """List all deletion requests (admin only)."""
    role = g.get("user_role")
    if role not in ("super_admin", "admin_sekolah"):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    try:
        res = supabase.table("deletion_requests").select("id, user_id, reason, status, requested_at, notes").order("requested_at", desc=True).limit(100).execute().data or []
        enriched = []
        for r in res:
            uname = r["user_id"][:8]
            try:
                p = supabase.table("profiles").select("full_name").eq("id", r["user_id"]).single().execute().data
                if p:
                    uname = p.get("full_name", r["user_id"][:8])
            except Exception:
                pass
            enriched.append({**r, "user_name": uname})
        return jsonify({"requests": enriched})
    except Exception as e:
        return jsonify({"error": str(e)[:100], "requests": []}), 500


@api_bp.route("/admin/deletion-requests/process", methods=["POST"])
@login_required
def api_admin_process_deletion():
    """Approve or reject a deletion request (admin only)."""
    role = g.get("user_role")
    if role not in ("super_admin", "admin_sekolah"):
        return jsonify({"error": "Forbidden"}), 403
    data = request.get_json() or {}
    request_id = data.get("id")
    action = data.get("action")
    notes = str(data.get("notes", "")).strip()
    if not request_id or action not in ("approve", "reject"):
        return jsonify({"error": "Data tidak lengkap"}), 400
    from app.services.data_retention_service import process_deletion_request
    result, status = process_deletion_request(request_id, g.user_id, action, notes)
    return jsonify(result), status


@api_bp.route("/public/privacy-info")
def api_public_privacy_info():
    """Public endpoint: returns DPO contact and PSE registration number."""
    from app.utils.auth import get_supabase
    supabase = get_supabase()
    result = {"dpo_contact": "", "pse_reg_number": "", "data_controller_name": "", "data_controller_email": ""}
    try:
        rows = supabase.table("system_settings").select("key, value").in_("key", ["dpo_contact", "pse_reg_number", "data_controller_name", "data_controller_email"]).execute().data or []
        for r in rows:
            result[r["key"]] = r["value"]
    except Exception:
        pass
    return jsonify(result)
