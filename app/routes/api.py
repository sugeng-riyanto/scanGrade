import io
import os
import uuid
import time
import threading
from flask import Blueprint, request, jsonify, g, render_template, redirect, send_file, current_app
from app.utils.auth import login_required, get_supabase
from app.services.anti_cheat_service import validate_violation_log
from app.utils.logger import get_logger
from app.errors import ValidationError, NotFoundError, GradingError, AIProcessingError
from app.utils.rate_limiter import limiter

api_bp = Blueprint("api", __name__)

# ── Upload security constants ──────────────────────────
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB

_sync_locks = {}
_sync_lock_mutex = threading.Lock()
_sync_last = {}


def _get_sync_lock(user_id, exam_id):
    key = f"{user_id}:{exam_id}"
    with _sync_lock_mutex:
        if key not in _sync_locks:
            _sync_locks[key] = threading.Lock()
        return _sync_locks[key]


def _check_rate_limit(user_id, exam_id, min_interval=5):
    key = f"{user_id}:{exam_id}"
    now = time.time()
    last = _sync_last.get(key, 0)
    if now - last < min_interval:
        return False
    _sync_last[key] = now
    return True


@api_bp.route("/violation/log", methods=["POST"])
@login_required
def log_violation():
    data = request.get_json()
    logs = data if isinstance(data, list) else [data]

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
            supabase.table("violation_logs").insert({
                "exam_id": exam_id,
                "user_id": g.user_id,
                "violation_type": log.get("violation_type", "unknown"),
                "metadata": log.get("metadata", {}),
            }).execute()
            total_count = supabase.table("violation_logs").select("id", count="exact").eq("user_id", g.user_id).eq("exam_id", exam_id).execute().count or 0
            exam = supabase.table("exams").select("anti_cheat_enabled, penalty_per_violation, max_violations, auto_submit_on_max").eq("id", exam_id).single().execute().data or {}
            from app.services.anti_cheat_service import calculate_graduated_penalty
            penalty_info = calculate_graduated_penalty(total_count, exam)
            results.append({"logged": True, "violation_count": total_count, **penalty_info})
        else:
            results.append({"logged": False, "reason": valid.get("reason")})

    return jsonify({"violations": results})


@api_bp.route("/violation/count", methods=["GET"])
@login_required
def violation_count():
    exam_id = request.args.get("exam_id")
    supabase = get_supabase()
    res = supabase.table("violation_logs") \
        .select("count", count="exact") \
        .eq("user_id", g.user_id) \
        .eq("exam_id", exam_id) \
        .execute()
    return jsonify({"count": res.count})


@api_bp.route("/scan/process", methods=["POST"])
@limiter.limit("20 per minute")
@login_required
def scan_process():
    """Process a scanned bubble sheet image and return detected answers.

    Security: validates extension, MIME type, image integrity, strips EXIF.
    """
    if "image" not in request.files:
        return jsonify({"error": "Tidak ada gambar yang dikirim"}), 400

    image_file = request.files["image"]

    # 1. Validate extension
    ext = os.path.splitext(image_file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Format file tidak didukung. Gunakan .jpg, .jpeg, atau .png"}), 422

    # 2. Validate MIME type via python-magic
    try:
        import magic
        header = image_file.read(2048)
        image_file.seek(0)
        mime_type = magic.from_buffer(header, mime=True)
        if mime_type not in ("image/jpeg", "image/png"):
            return jsonify({"error": f"Tipe file tidak valid ({mime_type}). Hanya jpg/png yang diizinkan."}), 422
    except ImportError:
        current_app.logger.warning("python-magic not installed; skipping MIME validation")

    # 3. Validate size
    image_file.seek(0, 2)
    file_size = image_file.tell()
    image_file.seek(0)
    if file_size > MAX_IMAGE_SIZE:
        return jsonify({"error": f"Gambar terlalu besar ({file_size/1024/1024:.1f}MB). Maksimal 20MB."}), 413
    if file_size == 0:
        return jsonify({"error": "File kosong"}), 422

    # 4. Verify image integrity + strip EXIF via Pillow
    try:
        from PIL import Image
        img_pil = Image.open(image_file)
        img_pil.verify()  # raises on corrupt
        image_file.seek(0)

        # Re-open after verify (verify consumes the file)
        img_pil = Image.open(image_file)
        # Strip EXIF by re-saving without EXIF data
        clean_buf = io.BytesIO()
        # Save without EXIF (only PNG/JPEG)
        save_format = "PNG" if mime_type == "image/png" else "JPEG"
        if "exif" in img_pil.info:
            img_pil.info.pop("exif")
        # Use raw data if PNG, convert to RGB for JPEG
        if save_format == "JPEG" and img_pil.mode != "RGB":
            img_pil = img_pil.convert("RGB")
        img_pil.save(clean_buf, format=save_format)
        image_data = clean_buf.getvalue()
        current_app.logger.info("EXIF stripped from scan image, cleaned size=%d", len(image_data))
    except Exception as e:
        return jsonify({"error": "Gambar tidak valid atau corrupt", "detail": str(e)[:100]}), 422

    exam_id = request.form.get("exam_id", "")
    total_questions = int(request.form.get("total_questions", 50))

    from app.services.omr_service import process_scan, draw_debug_image, preprocess_scan

    # 5. Preprocess + detect
    try:
        result = process_scan(image_data, total_questions=total_questions, preprocess=True)
    except Exception as e:
        current_app.logger.error("OMR processing crashed: %s", e, exc_info=True)
        return jsonify({
            "error": "Gagal memproses gambar. Pastikan foto jelas dan seluruh lembar jawaban terlihat.",
            "detail": str(e)[:200] if current_app.debug else None,
        }), 422

    # If no error, grade against answer key if exam_id provided
    if "error" not in result and exam_id:
        supabase = get_supabase()
        exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
        if exam and exam.get("answer_key"):
            key = exam["answer_key"]
            detected = result.get("answers", {})
            correct = 0
            for k, v in key.items():
                if k in detected and detected[k] == v and v not in ("essay", "essay_text", "essay_canvas"):
                    correct += 1
            mcq_count = sum(1 for v in key.values() if v not in ("essay", "essay_text", "essay_canvas"))
            result["score"] = round((correct / max(mcq_count, 1)) * 100, 2)
            result["correct"] = correct

    # Generate debug image
    if "error" not in result:
        from app.services.omr_service import load_image, find_registration_marks
        img = load_image(image_data)
        if img is not None:
            corners = find_registration_marks(img)
            debug_jpg = draw_debug_image(img, corners, result.get("answers"))
            import base64
            result["debug_image"] = base64.b64encode(debug_jpg).decode()

    return jsonify(result)


@api_bp.route("/student/auto-save", methods=["POST"])
@login_required
def student_auto_save():
    """Auto-save student's in-progress exam draft."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    return jsonify({"saved": True, "at": int(time.time())})


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
    lock = _get_sync_lock(g.user_id, exam_id)
    if not lock.acquire(blocking=False):
        return jsonify({"saved": True, "at": int(time.time()), "busy": True})
    try:
        from app.utils.auth import get_supabase
        supabase = get_supabase()
        existing = supabase.table("submissions").select("id,status,answers").eq("exam_id", exam_id).eq("student_id", g.user_id).execute().data
        if existing and existing[0].get("status") == "draft":
            if is_light and existing[0].get("answers"):
                merged = existing[0]["answers"]
                if isinstance(merged, dict):
                    merged.update(answers)
                    answers = merged
            supabase.table("submissions").update({"answers": answers}).eq("id", existing[0]["id"]).execute()
        elif not existing:
            supabase.table("submissions").insert({
                "exam_id": exam_id,
                "student_id": g.user_id,
                "answers": answers,
                "score": 0,
                "max_score": 100,
                "status": "draft",
            }).execute()
    except Exception:
        pass
    finally:
        lock.release()
    return jsonify({"saved": True, "at": int(time.time())})


@api_bp.route("/grade/auto-save/<submission_id>", methods=["POST"])
@login_required
def grade_auto_save(submission_id):
    """Auto-save teacher's in-progress grading."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    supabase = get_supabase()
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
    answer_key = exam.get("answer_key") or {}
    question_types = exam.get("question_types") or {}
    question_weights = exam.get("question_weights") or {}
    total_q = exam.get("total_questions", 0)
    if not question_weights and total_q > 0:
        mcq_count = sum(1 for i in range(total_q) if question_types.get(str(i), "mcq") == "mcq")
        if mcq_count > 0:
            each = round(100 / mcq_count, 2)
            for i in range(total_q):
                if question_types.get(str(i), "mcq") == "mcq":
                    question_weights[str(i)] = each
    subs = supabase.table("submissions").select("id,answers,penalty,teacher_feedback").eq("exam_id", exam_id).in_("status", ["submitted", "graded", "published"]).execute().data or []
    graded = 0
    for sub in subs:
        answers = sub.get("answers") or {}
        earned = 0.0
        for i in range(total_q):
            qtype = question_types.get(str(i), "mcq")
            key_val = answer_key.get(str(i))
            w = float(question_weights.get(str(i), 0))
            if w <= 0:
                continue
            if qtype == "mcq":
                ans = answers.get(str(i))
                if key_val == "bonus":
                    if ans and str(ans).strip():
                        earned += w
                elif isinstance(key_val, list):
                    if ans in key_val:
                        earned += w
                elif ans == key_val:
                    earned += w
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
        mcq_correct = 0
        mcq_count_q = sum(1 for i in range(total_q) if question_types.get(str(i), "mcq") == "mcq")
        for i in range(total_q):
            qtype = question_types.get(str(i), "mcq")
            key_val = answer_key.get(str(i))
            if qtype == "mcq" and key_val:
                ans = answers.get(str(i))
                if key_val == "bonus":
                    if ans and str(ans).strip():
                        mcq_correct += 1
                elif isinstance(key_val, list):
                    if ans in key_val:
                        mcq_correct += 1
                elif ans == key_val:
                    mcq_correct += 1
        mcq_score = round((mcq_correct / max(mcq_count_q, 1)) * 100, 2) if mcq_count_q > 0 else 0
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


@api_bp.route("/scan/save", methods=["POST"])
@login_required
def scan_save():
    """Save scanned answers as a submission."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    exam_id = data.get("exam_id")
    student_id = data.get("student_id")
    answers = data.get("answers")

    if not all([exam_id, student_id, answers]):
        return jsonify({"error": "exam_id, student_id, and answers are required"}), 400

    supabase = get_supabase()

    # Verify exam belongs to this teacher
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    if str(exam.get("teacher_id")) != g.user_id:
        return jsonify({"error": "Forbidden"}), 403

    # Grade MCQ answers
    key = exam.get("answer_key", {})
    detected = answers
    correct = 0
    for k, v in key.items():
        if k in detected and detected[k] == v and v not in ("essay", "essay_text", "essay_canvas"):
            correct += 1
    mcq_count = sum(1 for v in key.values() if v not in ("essay", "essay_text", "essay_canvas"))
    score = round((correct / max(mcq_count, 1)) * 100, 2) if mcq_count > 0 else 0

    # Check for existing submission and update or create
    existing = supabase.table("submissions") \
        .select("*") \
        .eq("exam_id", exam_id) \
        .eq("student_id", student_id) \
        .execute().data

    if existing:
        sub = supabase.table("submissions") \
            .update({
                "answers": answers,
                "score": score,
                "max_score": mcq_count,
                "status": "graded",
            }) \
            .eq("id", existing[0]["id"]) \
            .execute().data
    else:
        sub = supabase.table("submissions") \
            .insert({
                "exam_id": exam_id,
                "student_id": student_id,
                "answers": answers,
                "score": score,
                "max_score": mcq_count,
                "status": "graded",
            }) \
            .execute().data

    return jsonify({
        "success": True,
        "score": score,
        "correct": correct,
        "total": mcq_count,
        "submission": sub[0] if sub else None,
    })


@api_bp.route("/transaction/status", methods=["GET"])
@login_required
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
def ai_test_key():
    data = request.get_json()
    key_id = (data or {}).get("key_id", "")
    if not key_id:
        raise ValidationError("key_id", "Parameter key_id diperlukan")
    from app.services.ai_service import test_api_key
    result = test_api_key(g.user_id, key_id)
    if result.get("error"):
        app.logger.warning("AI key test failed: %s", result["error"], extra={"user_id": g.user_id, "key_id": key_id})
    return jsonify(result)


@api_bp.route("/grade/ai-suggest", methods=["POST"])
@login_required
def ai_suggest():
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
    import pandas as pd

    school_id = g.get("user_school_id")
    if not school_id:
        return jsonify({"error": "Akses ditolak: sekolah tidak terdaftar"}), 403

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

                # Check duplicate NISN
                existing = supabase.table("students") \
                    .select("id").eq("nisn", nisn) \
                    .eq("school_id", school_id).maybe_single().execute()
                if existing.data:
                    results["failed"] += 1
                    results["errors"].append({"row": row_num, "nisn": nisn, "message": "NISN sudah terdaftar"})
                    continue

                # Resolve class from name
                resolved_class_id = class_id
                if not resolved_class_id and kelas:
                    c = supabase.table("classes") \
                        .select("id").eq("school_id", school_id) \
                        .eq("name", kelas).maybe_single().execute()
                    if c.data:
                        resolved_class_id = c.data["id"]

                # Create auth user
                user_email = email or f"{nisn}@siswa.scan-grade.app"
                created = supabase.auth.admin.create_user({
                    "email": user_email, "password": password,
                    "user_metadata": {"role": "murid", "full_name": nama},
                    "email_confirm": True,
                })
                uid = created.user.id

                supabase.table("profiles").upsert({
                    "id": uid, "full_name": nama, "role": "murid",
                    "nisn": nisn, "school_id": school_id, "status": "active",
                }).execute()
                supabase.table("students").upsert({
                    "id": uid, "school_id": school_id, "nisn": nisn,
                    "class_id": resolved_class_id, "status": "active",
                }).execute()
                results["success"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append({
                    "row": row_num,
                    "nisn": str(row.get("nisn", "")),
                    "message": str(e)[:100],
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
