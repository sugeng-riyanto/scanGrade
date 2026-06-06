import io
import time
import threading
from flask import Blueprint, request, jsonify, g, render_template, redirect, send_file
from app.utils.auth import login_required, get_supabase
from app.services.anti_cheat_service import validate_violation_log

api_bp = Blueprint("api", __name__)

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
@login_required
def scan_process():
    """Process a scanned bubble sheet image and return detected answers."""
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    exam_id = request.form.get("exam_id", "")
    total_questions = int(request.form.get("total_questions", 50))

    image_file = request.files["image"]
    image_data = image_file.read()

    from app.services.omr_service import process_scan, draw_debug_image

    result = process_scan(image_data, total_questions=total_questions)

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
