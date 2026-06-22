import io
import json
import os
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, g, jsonify, current_app, make_response
from app.utils.auth import login_required, get_supabase
from app.services.audit_service import log_activity

student_bp = Blueprint("student", __name__)


@student_bp.route("/dashboard")
@login_required
def dashboard():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")

    available_exams = []
    submitted_ids = set()
    student_class_id = None
    student_school_id = None
    try:
        prof = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute()
        if prof.data:
            student_class_id = prof.data.get("class_id")
            student_school_id = prof.data.get("school_id")
    except Exception:
        pass
    try:
        query = supabase.table("exams").select("*").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        all_exams = query.execute().data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        # Filter by class_id if student has one, AND check scheduling
        for e in all_exams:
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            cids = e.get("class_ids") or []
            if isinstance(cids, str):
                try:
                    cids = json.loads(cids)
                except (json.JSONDecodeError, TypeError):
                    cids = []
            if student_class_id:
                if not cids or student_class_id in cids:
                    available_exams.append(e)
            else:
                if not cids:
                    available_exams.append(e)
        subs_ids = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published", "draft"]).execute().data or []
        submitted_ids = {s["exam_id"] for s in subs_ids}
        # Exclude retracted
        retracted = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).eq("status", "retracted").execute().data or []
        submitted_ids -= {s["exam_id"] for s in retracted}
    except Exception as e:
        current_app.logger.error(f"Dashboard query error: {e}")
    available_exams = [e for e in available_exams if e["id"] not in submitted_ids]

    subs = []
    try:
        subs = supabase.table("submissions").select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, submitted_at, graded_at, teacher_feedback, exams(id, title, answer_key, question_types, total_questions, pdf_page_urls)").eq("student_id", g.user_id).neq("status", "retracted").order("submitted_at", desc=True).execute().data or []
    except Exception as e:
        current_app.logger.error(f"Dashboard submissions query error: {e}")
    completed_exams = []
    all_scores = []
    for s in subs:
        # Only show submitted/graded/published in dashboard (hide drafts)
        if s.get("status") in ("draft",):
            continue
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        s.setdefault("is_hidden", False)
        completed_exams.append(s)
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if sc is not None:
            all_scores.append(float(sc))
    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else "-"
    user_name = g.user_name or g.user_email or ""

    # Get student's class info
    student_class = None
    subject_count = 0
    try:
        profile = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute().data or {}
        if profile.get("class_id"):
            cls = supabase.table("classes").select("name, grade_level").eq("id", profile["class_id"]).single().execute().data
            if cls:
                student_class = cls
        if profile.get("school_id"):
            cnt = supabase.table("teacher_assignments").select("id", count="exact") \
                .eq("school_id", profile["school_id"]) \
                .execute()
            subject_count = cnt.count or 0
            if student_class and student_class.get("name"):
                class_subj = supabase.table("teacher_assignments").select("id", count="exact") \
                    .eq("school_id", profile["school_id"]) \
                    .execute()
                subject_count = class_subj.count or 0
    except Exception:
        pass

    # School info
    school_info = {}
    try:
        profile = supabase.table("profiles").select("school_id").eq("id", g.user_id).single().execute().data or {}
        if profile.get("school_id"):
            school_info = supabase.table("schools").select("name, npsn, logo_url").eq("id", profile["school_id"]).single().execute().data or {}
    except Exception:
        pass
    # Active whiteboards for student's class (only if enabled by super admin)
    active_whiteboards = []
    if student_class_id and student_school_id:
        try:
            # Check school feature toggle
            feat = supabase.table("schools").select("features").eq("id", student_school_id).single().execute().data or {}
            f = feat.get("features") or {}
            if isinstance(f, str):
                f = json.loads(f)
            if not f.get("whiteboard_enabled", True):
                pass  # whiteboard disabled for this school
            else:
                wbs = supabase.table("whiteboards").select("id,title,status,created_at") \
                    .eq("class_id", student_class_id) \
                    .eq("school_id", student_school_id) \
                    .eq("status", "active") \
                    .order("created_at", desc=True) \
                    .limit(5).execute()
                active_whiteboards = wbs.data or []
        except Exception:
            pass

    return render_template("student/dashboard.html", available_exams=available_exams,
                           completed_exams=completed_exams[:5], avg_score=avg_score,
                           user_name=user_name, student_class=student_class,
                           subject_count=subject_count, school_info=school_info,
                           active_whiteboards=active_whiteboards)


@student_bp.route("/reset-password", methods=["POST"])
@login_required
def student_reset_password():
    if g.get("user_role") not in ("murid",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    pw = request.form.get("password", "").strip()
    if len(pw) < 6:
        return jsonify({"error": "Password minimal 6 karakter"}), 400
    try:
        supabase.auth.admin.update_user_by_id(g.user_id, {"password": pw})
        log_activity("reset_password", "user", g.user_id, user_id=g.user_id)
        return jsonify({"success": True, "message": "Password berhasil diubah"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@student_bp.route("/profile/update", methods=["POST"])
@login_required
def student_update_profile():
    if g.get("user_role") not in ("murid",):
        return jsonify({"error": "Forbidden"}), 403
    supabase = get_supabase()
    data = {}
    for key in ("phone",):
        val = request.form.get(key)
        if val is not None:
            data[key] = val.strip()
    if not data:
        return jsonify({"error": "Tidak ada data yang diubah"}), 400
    try:
        supabase.table("profiles").update(data).eq("id", g.user_id).execute()
        log_activity("update", "profile", g.user_id, new_data=data, user_id=g.user_id)
        return jsonify({"success": True, "message": "Profil berhasil diperbarui"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@student_bp.route("/exams")
@login_required
def exam_list():
    supabase = get_supabase()
    if g.get("user_role") != "murid":
        return redirect("/teacher/dashboard")

    # Get student's class_id and school_id
    student_class_id = None
    student_school_id = None
    try:
        prof = supabase.table("profiles").select("class_id, school_id").eq("id", g.user_id).single().execute()
        if prof.data:
            student_class_id = prof.data.get("class_id")
            student_school_id = prof.data.get("school_id")
    except Exception:
        pass

    exams = []
    try:
        query = supabase.table("exams").select("*").eq("is_published", True).eq("status", "active")
        if student_school_id:
            query = query.eq("school_id", student_school_id)
        res = query.order("created_at", desc=True).execute()
        all_exams = res.data or []
        now_iso = datetime.now(timezone.utc).isoformat()
        # Filter by class_id if student has one, AND check scheduling
        for e in all_exams:
            # Skip exams with future start_at
            start_at = e.get("start_at")
            if start_at and str(start_at) > now_iso[:19]:
                continue
            cids = e.get("class_ids") or []
            if isinstance(cids, str):
                try:
                    cids = json.loads(cids)
                except (json.JSONDecodeError, TypeError):
                    cids = []
            if student_class_id:
                if not cids or student_class_id in cids:
                    exams.append(e)
            else:
                if not cids:
                    exams.append(e)
    except Exception as e:
        current_app.logger.error(f"Exam list query error: {e}")

    submitted_ids = set()
    try:
        subs = supabase.table("submissions").select("exam_id").eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published", "draft"]).neq("status", "retracted").execute().data or []
        submitted_ids = {s["exam_id"] for s in subs}
    except Exception as e:
        current_app.logger.error(f"Submission query error: {e}")
    exams = [e for e in exams if e["id"] not in submitted_ids]
    resp = make_response(render_template("student/exam_list.html", exams=exams))
    resp.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"
    return resp


@student_bp.route("/exams/<exam_id>")
@login_required
def take_exam(exam_id):
    supabase = get_supabase()
    try:
        res = supabase.table("exams").select("*").eq("id", exam_id).single().execute()
        exam = res.data
    except Exception as e:
        current_app.logger.error(f"Take exam query error: {e}")
        flash("Ujian tidak ditemukan", "error")
        return redirect("/student/exams")
    if not exam:
        flash("Ujian tidak ditemukan", "error")
        return redirect("/student/exams")

    current_app.logger.info("Student exam %s: pdf_page_urls=%s, pdf_url=%s",
                            exam_id, exam.get("pdf_page_urls"), exam.get("pdf_url"))
    # Check if exam is scheduled for the future
    start_at = exam.get("start_at")
    if start_at:
        try:
            if isinstance(start_at, str):
                start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            else:
                start_dt = start_at
            if start_dt > datetime.now(timezone.utc):
                flash("Ujian ini belum tersedia. Silakan cek kembali jadwal.", "error")
                return redirect("/student/exams")
        except Exception:
            pass

    # Check if student already reached max attempts (exclude draft + retracted)
    max_attempts = exam.get("max_attempts", 1)
    try:
        existing = supabase.table("submissions").select("id", count="exact").eq("exam_id", exam_id).eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published"]).execute()
        attempt_count = existing.count or 0
        if attempt_count >= max_attempts:
            flash(f"Anda sudah mencapai batas maksimal {max_attempts}x mengerjakan ujian ini", "error")
            return redirect("/student/exams")
    except Exception:
        pass
    # Ensure anti-cheat defaults (handle missing columns or NULL values)
    ac_defaults = {
        "anti_cheat_enabled": True,
        "penalty_per_violation": 5,
        "max_violations": 5,
        "auto_submit_on_max": True,
        "fullscreen_required": True,
        "block_copy_paste": True,
        "block_right_click": True,
        "watermark_name": True,
        "allow_calculator": False,
    }
    for k, v in ac_defaults.items():
        if k not in exam or exam[k] is None:
            exam[k] = v
    # Parse JSON fields that may come as strings from Supabase
    for _field in ("question_types", "answer_key", "question_weights", "question_pages", "pdf_page_urls"):
        _val = exam.get(_field)
        if isinstance(_val, str):
            try:
                exam[_field] = json.loads(_val)
            except (json.JSONDecodeError, TypeError):
                exam[_field] = {}
    anti_cheat_config = json.dumps({k: exam.get(k, v) for k, v in ac_defaults.items()})
    # Cek existing draft submission untuk timer persist across devices
    exam_started_at = None
    try:
        draft = supabase.table("submissions").select("started_at").eq("exam_id", exam_id).eq("student_id", g.user_id).eq("status", "draft").limit(1).execute()
        if draft.data and draft.data[0].get("started_at"):
            exam_started_at = draft.data[0]["started_at"]
    except Exception:
        pass
    resp = make_response(render_template("student/take_exam.html", exam=exam, anti_cheat_config=anti_cheat_config, exam_started_at=exam_started_at))
    # Allow short browser caching for exam page (exam data is static once started)
    resp.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=60"
    return resp


@student_bp.route("/exams/<exam_id>/submit", methods=["POST"])
@login_required
def submit_exam(exam_id):
    # Check subscription
    from app.utils.auth import check_subscription_write
    allowed, msg = check_subscription_write()
    if not allowed:
        return jsonify({"error": msg}), 403

    supabase = get_supabase()

    # Verify exam exists, is published and active
    exam = supabase.table("exams").select("*").eq("id", exam_id).single().execute().data
    if not exam:
        return jsonify({"error": "Exam not found"}), 404
    if not exam.get("is_published") or exam.get("status") != "active":
        return jsonify({"error": "Exam is not available for submission"}), 403

    # Verify student's class matches exam's class_ids
    exam_class_ids = exam.get("class_ids") or []
    if exam_class_ids:
        try:
            prof = supabase.table("profiles").select("class_id").eq("id", g.user_id).single().execute()
            student_class_id = prof.data.get("class_id") if prof.data else None
            if student_class_id and student_class_id not in exam_class_ids:
                return jsonify({"error": "Ujian ini tidak tersedia untuk kelas Anda"}), 403
        except Exception:
            pass

    # Check attempts (exclude draft + retracted)
    max_attempts = exam.get("max_attempts", 1)
    existing = supabase.table("submissions").select("id", count="exact").eq("exam_id", exam_id).eq("student_id", g.user_id).in_("status", ["submitted", "graded", "published"]).execute()
    attempt_count = existing.count or 0
    if attempt_count >= max_attempts:
        return jsonify({"error": f"Anda sudah mencapai batas maksimal {max_attempts}x mengerjakan ujian ini"}), 409

    answers = {}
    if request.is_json:
        data = request.get_json()
        answers = data.get("answers_json", data.get("answers", {}))
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except json.JSONDecodeError:
                pass
    else:
        answers_json = request.form.get("answers_json", "{}")
        try:
            answers = json.loads(answers_json)
        except json.JSONDecodeError:
            pass

    # Parse JSON fields that may be strings from Supabase
    for _fld in ("answer_key", "question_types", "question_weights", "question_pages"):
        _v = exam.get(_fld)
        if isinstance(_v, str):
            try:
                exam[_fld] = json.loads(_v)
            except (json.JSONDecodeError, TypeError):
                exam[_fld] = {}

    mcq_count = sum(1 for v in (exam.get("answer_key") or {}).values() if v not in ("essay", "essay_text", "essay_canvas", None))
    question_weights = exam.get("question_weights") or {}
    question_types = exam.get("question_types") or {}
    total_q = exam["total_questions"]
    if not question_weights and mcq_count > 0:
        each = round(100 / mcq_count, 2)
        for i in range(total_q):
            if question_types.get(str(i), "mcq") == "mcq":
                question_weights[str(i)] = each
    earned = 0.0
    for i in range(exam["total_questions"]):
        qtype = question_types.get(str(i), "mcq")
        key = exam.get("answer_key", {}).get(str(i))
        w = float(question_weights.get(str(i), 0))
        if qtype == "mcq" and key and w > 0:
            ans = answers.get(str(i))
            if isinstance(ans, dict):
                ans = ans.get('answer', '')
            if key == "bonus":
                if ans and str(ans).strip():
                    earned += w
            elif isinstance(key, list):
                if ans in key:
                    earned += w
            elif ans == key:
                earned += w

    score = round(min(earned, 100), 2)

    from app.services.anti_cheat_service import calculate_graduated_penalty
    try:
        violation_count = supabase.table("violation_logs").select("id", count="exact").eq("user_id", g.user_id).eq("exam_id", exam_id).execute().count or 0
    except Exception:
        violation_count = 0
    penalty_info = calculate_graduated_penalty(violation_count, exam)
    penalty = penalty_info["penalty"]

    final_score = max(0.0, round(score - penalty, 2))
    submission = {
        "exam_id": exam_id,
        "student_id": g.user_id,
        "answers": {k: v for k, v in answers.items() if v is not None},
        "score": score,
        "max_score": 100.0,
        "violations": violation_count,
        "penalty": round(penalty, 2),
        "final_score": final_score,
        "status": "submitted",
        "is_published": exam.get("publish_mode") == "auto",
    }
    try:
        supabase.table("submissions").insert(submission).execute()
        log_activity("submit", "submission", None, new_data={"exam_id": exam_id, "score": score}, user_id=g.user_id)
    except Exception as e:
        import traceback
        current_app.logger.error("Submit error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")


@student_bp.route("/results")
@login_required
def results():
    supabase = get_supabase()
    submissions = []
    try:
        res = supabase.table("submissions") \
            .select("id, status, score, final_score, penalty, submitted_at, answers, exams(id, title, subject)") \
            .eq("student_id", g.user_id) \
            .order("submitted_at", desc=True) \
            .execute()
        submissions = res.data or []
    except Exception as e:
        current_app.logger.error(f"Results query error: {e}")
        submissions = []
    for s in submissions:
        if s.get("exams"):
            s["exam"] = s.pop("exams")
        ans = s.get("answers")
        if isinstance(ans, str):
            try:
                s["answers"] = json.loads(ans)
            except (json.JSONDecodeError, TypeError):
                s["answers"] = {}
        if not isinstance(s.get("answers"), dict):
            s["answers"] = {}
    # Hanya tampilkan 1 submission terbaru per exam (draft boleh standalone)
    seen = {}
    for s in submissions:
        eid = s.get("exam", {}).get("id")
        if not eid:
            continue
        if eid in seen:
            existing = seen[eid]
            # Draft diganti sama non-draft (final)
            if existing["status"] == "draft" and s["status"] != "draft":
                seen[eid] = s
            # Non-draft tidak diganti draft
            elif existing["status"] != "draft" and s["status"] == "draft":
                continue
        else:
            seen[eid] = s
    seen_no_key = [s for s in submissions if not s.get("exam", {}).get("id")]
    submissions = seen_no_key + list(seen.values())
    # Group by subject for total scores
    subjects = {}
    for s in submissions:
        subj = (s.get("exam") or {}).get("subject", "Lainnya")
        sc = s.get("final_score") if s.get("final_score") is not None else s.get("score")
        if subj not in subjects:
            subjects[subj] = {"scores": [], "count": 0}
        if sc is not None:
            subjects[subj]["scores"].append(float(sc))
        subjects[subj]["count"] += 1
    subject_totals = []
    for subj, data in subjects.items():
        scores = data["scores"]
        avg = round(sum(scores) / len(scores), 1) if scores else 0
        subject_totals.append({
            "name": subj,
            "avg": avg,
            "count": data["count"],
            "exam_count": len(scores),
        })
    subject_totals.sort(key=lambda x: x["name"])
    return render_template("student/results.html", submissions=submissions, subject_totals=subject_totals)


@student_bp.route("/results/<submission_id>")
@login_required
def result_detail(submission_id):
    supabase = get_supabase()
    try:
        res = supabase.table("submissions") \
            .select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, started_at, submitted_at, graded_at, teacher_feedback, exams(id, title, subject, answer_key, question_types, total_questions, pdf_page_urls)") \
            .eq("id", submission_id) \
            .eq("student_id", g.user_id) \
            .single() \
            .execute()
        submission = res.data
    except Exception:
        return redirect("/student/results")
    if not submission:
        return redirect("/student/results")
    if submission.get("exams"):
        submission["exam"] = submission.pop("exams")
    submission.setdefault("is_hidden", False)
    # Parse JSON strings
    for field in ("teacher_feedback", "answers"):
        val = submission.get(field)
        if isinstance(val, str):
            try:
                submission[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                submission[field] = {} if field != "answers" else {}
        if not isinstance(submission.get(field), dict):
            submission[field] = {} if field != "answers" else {}
    # Parse exam JSON fields
    for _field in ("question_types", "answer_key", "question_weights", "question_pages", "pdf_page_urls"):
        _val = submission.get("exam", {}).get(_field)
        if isinstance(_val, str):
            try:
                submission["exam"][_field] = json.loads(_val)
            except (json.JSONDecodeError, TypeError):
                submission["exam"][_field] = {}
    student_name = g.user_name or g.user_email or ""
    return render_template("student/result_detail.html", submission=submission, student_name=student_name)


@student_bp.route("/results/<submission_id>/download-pdf")
@login_required
def download_result_pdf(submission_id):
    import base64
    import re
    import os
    from xhtml2pdf import pisa
    from PIL import Image, ImageDraw, ImageFont

    supabase = get_supabase()
    try:
        res = supabase.table("submissions") \
            .select("id, exam_id, student_id, answers, score, max_score, violations, penalty, final_score, status, is_published, started_at, submitted_at, graded_at, teacher_feedback, exams(id, title, subject, answer_key, question_types, total_questions, pdf_page_urls)") \
            .eq("id", submission_id) \
            .eq("student_id", g.user_id) \
            .single() \
            .execute()
        submission = res.data
    except Exception:
        return redirect("/student/results")
    if not submission:
        return redirect("/student/results")
    if submission.get("exams"):
        submission["exam"] = submission.pop("exams")
    submission.setdefault("is_hidden", False)
    student_name = g.user_name or g.user_email or ""

    # Fetch teacher & school info
    teacher_name = ""
    school_info = {"name": "", "address": "", "logo_url": ""}
    try:
        exam_id = submission.get("exam_id", "")
        ex = supabase.table("exams").select("teacher_id").eq("id", exam_id).single().execute().data
        if ex:
            t = supabase.table("profiles").select("full_name").eq("id", ex["teacher_id"]).single().execute().data
            if t: teacher_name = t.get("full_name", "")
        prof = supabase.table("profiles").select("school_id").eq("id", g.user_id).single().execute().data
        if prof and prof.get("school_id"):
            sch = supabase.table("schools").select("name, address, logo_url").eq("id", prof["school_id"]).single().execute().data
            if sch: school_info = sch
    except:
        pass

    exam = submission.get("exam") or {}
    fb = submission.get("teacher_feedback") or {}
    fb_overlay = fb.get("overlay_pages", {})
    fb_scores = fb.get("scores", {})
    fb_comments = fb.get("comments", {})
    answer_key = exam.get("answer_key", {})
    question_types = exam.get("question_types", {})
    question_weights = exam.get("question_weights", {})
    total_q = exam.get("total_questions", 0)
    pdf_page_urls = exam.get("pdf_page_urls") or []
    answers = submission.get("answers") or {}
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}

    def data_url_to_pil(data_url):
        try:
            if not data_url or "," not in data_url:
                return None
            _, b64 = data_url.split(",", 1)
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        except Exception:
            return None

    def local_path_to_pil(path):
        try:
            if not path:
                return None
            if path.startswith("/static/"):
                full = os.path.join(current_app.static_folder, path.replace("/static/", "", 1))
            else:
                full = path
            if not os.path.exists(full):
                return None
            return Image.open(full).convert("RGBA")
        except Exception:
            return None

    def remove_black_pixels(img):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = data[x, y]
                if r < 20 and g < 20 and b < 20:
                    data[x, y] = (r, g, b, 0)
                elif r < 60 and g < 60 and b < 60:
                    alpha = int((r + g + b) / 3 * 255 / 60)
                    data[x, y] = (r, g, b, min(a, alpha))
        return img

    def draw_text_boxes(img, text_boxes, border_color, bg_color):
        if not text_boxes:
            return img
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        w, h = img.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for box in text_boxes:
            text = box.get("text", "").strip()
            if not text:
                continue
            x_pct = box.get("x_pct", 0)
            y_pct = box.get("y_pct", 0)
            bx = int(x_pct * w)
            by = int(y_pct * h)
            try:
                bbox = draw.textbbox((bx + 4, by + 4), text, font=font)
                tw = bbox[2] - bbox[0] + 10
                th = bbox[3] - bbox[1] + 8
            except Exception:
                tw, th = max(80, len(text) * 8), 22
            draw.rectangle([bx, by, bx + tw, by + th], fill=bg_color, outline=border_color, width=2)
            draw.text((bx + 5, by + 4), text, fill=(30, 41, 59, 255), font=font)
        return Image.alpha_composite(img, overlay)

    def pil_to_data_url(img, fmt="PNG"):
        buf2 = io.BytesIO()
        rgb = Image.new("RGB", img.size, (255, 255, 255))
        rgb.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
        rgb.save(buf2, format=fmt, quality=85)
        b64 = base64.b64encode(buf2.getvalue()).decode("ascii")
        return f"data:image/{fmt.lower()};base64,{b64}"

    merged_pages = {}
    for i in range(total_q):
        qtype = (question_types or {}).get(str(i), "mcq")
        student_ans = answers.get(str(i), "")
        student_ans_data = student_ans if isinstance(student_ans, dict) else {}
        teacher_ov = fb_overlay.get(str(i), {})

        s_pages = student_ans_data.get("pages", {}) if (student_ans_data and student_ans_data.get("pages")) else {}
        page_imgs = {}

        for p_idx_str, p_data in s_pages.items():
            p_idx = int(p_idx_str)
            pdf_idx = p_idx if ("0" in s_pages) else (p_idx - 1)
            pdf_url = pdf_page_urls[pdf_idx] if (0 <= pdf_idx < len(pdf_page_urls)) else ""

            bg = local_path_to_pil(pdf_url)
            if bg is None and p_data.get("canvas"):
                bg = data_url_to_pil(p_data.get("canvas", ""))
            if bg is None:
                continue

            canvas_raw = p_data.get("canvas", "")
            is_png = canvas_raw.startswith("data:image/png")
            has_canvas = canvas_raw and len(canvas_raw) > (800 if is_png else 3000)
            student_tbs = p_data.get("textBoxes") or []

            if has_canvas and pdf_url:
                overlay = data_url_to_pil(canvas_raw)
                if overlay:
                    is_jpeg = canvas_raw.startswith("data:image/jpeg") or canvas_raw.startswith("data:image/jpg")
                    if is_jpeg:
                        overlay = remove_black_pixels(overlay)
                    overlay = overlay.resize(bg.size, Image.LANCZOS)
                    bg = Image.alpha_composite(bg, overlay)

            if student_tbs:
                bg = draw_text_boxes(bg, student_tbs, (249, 115, 22, 255), (255, 255, 255, 235))

            page_imgs[pdf_idx] = bg

        for ov_p_str, ov_data in teacher_ov.items():
            ov_p_idx = int(ov_p_str)
            if ov_p_idx in page_imgs:
                bg = page_imgs[ov_p_idx]
            else:
                pdf_url = pdf_page_urls[ov_p_idx] if (0 <= ov_p_idx < len(pdf_page_urls)) else ""
                bg = local_path_to_pil(pdf_url)
                if bg is None:
                    continue

            ov_canvas = ov_data.get("canvas", "")
            is_ov_png = ov_canvas.startswith("data:image/png")
            has_ov_canvas = ov_canvas and len(ov_canvas) > (800 if is_ov_png else 1000)
            ov_tbs = ov_data.get("textBoxes") or []

            if has_ov_canvas:
                ov = data_url_to_pil(ov_canvas)
                if ov:
                    is_jpeg = ov_canvas.startswith("data:image/jpeg") or ov_canvas.startswith("data:image/jpg")
                    if is_jpeg:
                        ov = remove_black_pixels(ov)
                    ov = ov.resize(bg.size, Image.LANCZOS)
                    bg = Image.alpha_composite(bg, ov)

            if ov_tbs:
                bg = draw_text_boxes(bg, ov_tbs, (5, 150, 105, 255), (236, 253, 245, 235))

            page_imgs[ov_p_idx] = bg

        sorted_imgs = [page_imgs[k] for k in sorted(page_imgs.keys())]
        if sorted_imgs:
            merged_pages[f"{i}"] = [pil_to_data_url(img) for img in sorted_imgs]

    html_string = render_template(
        "student/result_detail_pdf.html",
        submission=submission,
        student_name=student_name,
        teacher_name=teacher_name,
        school_info=school_info,
        merged_pages=merged_pages,
    )
    buf = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buf)
    if pisa_status.err:
        current_app.logger.error("PDF generation error: %s", pisa_status.err)
    buf.seek(0)
    safe_title = re.sub(r'[^\w\s-]', '', exam.get('title', 'ujian')).replace(' ', '_')
    safe_name = re.sub(r'[^\w\s-]', '', student_name).replace(' ', '_')
    filename = f"hasil_{safe_title}_{safe_name}.pdf"
    resp = make_response(buf.read())
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@student_bp.route("/submissions/<submission_id>/retract", methods=["POST"])
@login_required
def retract_submission(submission_id):
    supabase = get_supabase()
    sub = supabase.table("submissions").select("answers").eq("id", submission_id).eq("student_id", g.user_id).single().execute().data
    if not sub:
        if request.is_json:
            return jsonify({"error": "Not found"}), 404
        return redirect("/student/results")
    answers = sub.get("answers")
    if isinstance(answers, str):
        try:
            answers = json.loads(answers)
        except (json.JSONDecodeError, TypeError):
            answers = {}
    if not isinstance(answers, dict):
        answers = {}
    answers["_retract_request"] = {"status": "pending", "requested_at": datetime.now(timezone.utc).isoformat()}
    supabase.table("submissions").update({"answers": json.dumps(answers)}).eq("id", submission_id).execute()
    log_activity("retract_request", "submission", submission_id, user_id=g.user_id)
    if request.is_json:
        return jsonify({"success": True})
    return redirect("/student/results")


@student_bp.route("/submissions/<submission_id>/toggle-visibility", methods=["POST"])
@login_required
def toggle_submission_visibility(submission_id):
    return jsonify({"error": "Fitur belum tersedia (migrasi DB belum dijalankan)"}), 501


@student_bp.route("/submissions/<submission_id>/delete", methods=["POST"])
@login_required
def delete_submission(submission_id):
    if g.get("user_role") == "murid":
        return jsonify({"error": "Siswa tidak bisa menghapus submission"}), 403
    supabase = get_supabase()
    sub = supabase.table("submissions").select("id, status, student_id").eq("id", submission_id).single().execute().data
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if g.get("user_role") not in ("super_admin", "admin_sekolah") and sub.get("student_id") != g.user_id:
        return jsonify({"error": "Unauthorized"}), 403
    if sub.get("status") not in ("submitted", "draft"):
        return jsonify({"error": "Cannot delete this submission"}), 403
    supabase.table("submissions").delete().eq("id", submission_id).execute()
    return jsonify({"success": True})


@student_bp.route("/notifications")
@login_required
def student_notifications():
    return render_template("student/notifications.html")


@student_bp.route("/settings")
@login_required
def student_settings():
    """Student settings page (password, data export, deletion request)."""
    return render_template("student/settings.html")
