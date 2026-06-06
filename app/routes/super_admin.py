"""Super Admin — full access to all schools, teachers, exams, submissions."""
import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, g, request, jsonify, redirect, flash
from app.utils.auth import login_required, get_supabase
from app.services.audit_service import log_activity

super_bp = Blueprint("super_admin", __name__, url_prefix="/super-admin")


def _sa_required(f):
    """Super admin only — can access ALL data across all schools."""
    from functools import wraps
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if g.get("user_role") != "super_admin":
            return redirect("/auth/login")
        return f(*args, **kwargs)
    return wrapper


def _safe_count(supabase, table, column="id", **filters):
    """Execute count query with safe error handling."""
    try:
        q = supabase.table(table).select(column, count="exact")
        for k, v in filters.items():
            if v is not None:
                q = q.eq(k, v)
        return q.execute().count or 0
    except Exception:
        return 0


def _safe_select(supabase, table, columns="*", limit=20, order_col="created_at", desc=True, **filters):
    """Execute select query with safe error handling."""
    try:
        q = supabase.table(table).select(columns)
        for k, v in filters.items():
            if v is not None:
                q = q.eq(k, v)
        if desc:
            q = q.order(order_col, desc=True)
        else:
            q = q.order(order_col)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []
    except Exception:
        return []


@super_bp.route("/dashboard")
@_sa_required
def dashboard():
    supabase = get_supabase()

    total_schools = _safe_count(supabase, "schools")
    total_users = _safe_count(supabase, "profiles")
    total_teachers = _safe_count(supabase, "profiles", role="guru")
    total_students = _safe_count(supabase, "profiles", role="murid")
    total_exams = _safe_count(supabase, "exams")
    total_subs = _safe_count(supabase, "submissions")
    pending_requests = _safe_count(supabase, "school_registration_requests", status="pending")

    schools = _safe_select(supabase, "schools", "id, name, status, created_at", limit=10)

    recent_logs = []
    try:
        recent_logs = supabase.table("audit_logs").select("*, profiles!inner(full_name)").order("created_at", desc=True).limit(20).execute().data or []
    except Exception:
        pass

    requests = _safe_select(supabase, "school_registration_requests", limit=20)

    return render_template("super_admin/dashboard.html",
        total_schools=total_schools, total_users=total_users,
        total_teachers=total_teachers, total_students=total_students,
        total_exams=total_exams, total_subs=total_subs,
        pending_requests=pending_requests,
        schools=schools, recent_logs=recent_logs, requests=requests,
    )


@super_bp.route("/schools")
@_sa_required
def schools():
    supabase = get_supabase()
    q = request.args.get("q", "")
    data = _safe_select(supabase, "schools", limit=200)
    if q:
        data = [s for s in data if q.lower() in (s.get("name", "") + s.get("npsn", "")).lower()]
    for s in data:
        s["teacher_count"] = _safe_count(supabase, "profiles", role="guru", school_id=s["id"])
        s["student_count"] = _safe_count(supabase, "profiles", role="murid", school_id=s["id"])
        s["exam_count"] = _safe_count(supabase, "exams", school_id=s["id"])
    return render_template("super_admin/schools.html", schools=data, q=q)


@super_bp.route("/users")
@_sa_required
def users():
    supabase = get_supabase()
    role = request.args.get("role", "")
    q = request.args.get("q", "")
    data = []
    try:
        query = supabase.table("profiles").select("*, schools!left(name)").order("created_at", desc=True)
        if role in ("super_admin", "admin_sekolah", "guru", "murid"):
            query = query.eq("role", role)
        data = query.execute().data or []
        if q:
            data = [u for u in data if q.lower() in (u.get("full_name", "") + ((u.get("schools") or {}) or {}).get("name", "")).lower()]
    except Exception:
        data = []
    return render_template("super_admin/users.html", users=data, role=role, q=q)


@super_bp.route("/exams")
@_sa_required
def exams():
    supabase = get_supabase()
    data = []
    try:
        data = supabase.table("exams").select("*, profiles!inner(full_name)").order("created_at", desc=True).limit(50).execute().data or []
    except Exception:
        pass
    for e in data:
        e["submission_count"] = _safe_count(supabase, "submissions", exam_id=e["id"])
        e["teacher_name"] = (e.pop("profiles", None) or {}).get("full_name", "-")
    return render_template("super_admin/exams.html", exams=data)


@super_bp.route("/logs")
@_sa_required
def logs():
    supabase = get_supabase()
    days = request.args.get("days", 7, type=int)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    data = []
    try:
        data = supabase.table("audit_logs").select("*, profiles!left(full_name)").gte("created_at", since.isoformat()).order("created_at", desc=True).limit(100).execute().data or []
    except Exception:
        pass
    # Filter out None items
    data = [d for d in data if d is not None]
    return render_template("super_admin/logs.html", logs=data, days=days)


@super_bp.route("/reset-demo-passwords", methods=["POST"])
@_sa_required
def reset_demo_passwords():
    """Reset all demo users to their default passwords."""
    supabase = get_supabase()
    # Import demo config from manage.py
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
    try:
        from manage import DEMO_USERS, DEMO_SCHOOLS
    except ImportError:
        # Fallback: define inline
        DEMO_USERS = {"super_admin": {"email": "superadmin@scan-grade.app", "password": "superadmin123"}}
        DEMO_SCHOOLS = [
            {"admin": {"email": "admin_smp@scan-grade.app", "password": "demo123"}, "teachers": [
                {"email": "guru_mtk_smp@scan-grade.app", "password": "demo123"},
                {"email": "guru_ipa_smp@scan-grade.app", "password": "demo123"},
            ], "students": [
                {"email": "siswa1_smp@scan-grade.app", "password": "demo123"},
                {"email": "siswa2_smp@scan-grade.app", "password": "demo123"},
            ]},
            {"admin": {"email": "admin_sma@scan-grade.app", "password": "demo123"}, "teachers": [
                {"email": "guru_mtk_sma@scan-grade.app", "password": "demo123"},
                {"email": "guru_fisika_sma@scan-grade.app", "password": "demo123"},
            ], "students": [
                {"email": "siswa1_sma@scan-grade.app", "password": "demo123"},
                {"email": "siswa2_sma@scan-grade.app", "password": "demo123"},
            ]},
        ]

    results = []
    all_users = [DEMO_USERS["super_admin"]]
    for s in DEMO_SCHOOLS:
        all_users.append(s["admin"])
        all_users.extend(s.get("teachers", []))
        all_users.extend(s.get("students", []))

    for user in all_users:
        email = user["email"]
        password = user["password"]
        try:
            for u in supabase.auth.admin.list_users():
                if u.email == email:
                    supabase.auth.admin.update_user_by_id(u.id, {"password": password})
                    results.append({"email": email, "password": password, "success": True})
                    break
        except Exception as e:
            results.append({"email": email, "error": str(e)[:60]})

    return jsonify({"results": results, "total": len(results), "ok": sum(1 for r in results if r.get("success"))})


@super_bp.route("/reset-demo-data", methods=["POST"])
@_sa_required
def reset_demo_data():
    """Delete all demo data (submissions, exams, classes) but KEEP user accounts."""
    supabase = get_supabase()
    tables = ["submissions", "violation_logs", "exam_access_codes", "analytics_cache",
              "teacher_assignments", "exams", "students", "teachers", "classes", "subjects"]
    cleared = 0
    errors = []
    for table in tables:
        try:
            supabase.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            cleared += 1
        except Exception as e:
            errors.append(f"{table}: {str(e)[:40]}")
    return jsonify({"success": True, "message": f"{cleared}/{len(tables)} tabel dibersihkan", "errors": errors[:3]})


@super_bp.route("/demo-settings/data")
@_sa_required
def demo_settings_data():
    supabase = get_supabase()
    try:
        data = supabase.table("school_settings").select("demo_settings").eq("id", 1).single().execute().data or {}
        return jsonify(data.get("demo_settings") or {})
    except Exception:
        return jsonify({})


@super_bp.route("/demo-settings", methods=["GET", "POST"])
@_sa_required
def demo_settings():
    supabase = get_supabase()
    if request.method == "POST":
        settings = {
            "demo_enabled": request.form.get("demo_enabled", "false") == "true",
            "demo_super_admin": request.form.get("demo_super_admin", "false") == "true",
            "demo_admin_sekolah": request.form.get("demo_admin_sekolah", "false") == "true",
            "demo_guru": request.form.get("demo_guru", "false") == "true",
            "demo_murid": request.form.get("demo_murid", "false") == "true",
            "demo_tutorial": request.form.get("demo_tutorial", "false") == "true",
        }
        try:
            supabase.table("school_settings").upsert({"id": 1, "demo_settings": settings}).execute()
        except Exception:
            try:
                existing = supabase.table("school_settings").select("id").eq("id", 1).execute()
                if existing.data:
                    supabase.table("school_settings").update({"demo_settings": settings}).eq("id", 1).execute()
                else:
                    supabase.table("school_settings").insert({"id": 1, "demo_settings": settings}).execute()
            except Exception:
                pass
        return jsonify({"success": True})

    current = {}
    try:
        data = supabase.table("school_settings").select("demo_settings").eq("id", 1).single().execute().data or {}
        current = data.get("demo_settings") or {}
    except Exception:
        pass
    return render_template("super_admin/demo_settings.html", settings=current)


# ─── Midtrans Settings ────────────────────────────────────────────────

@super_bp.route("/midtrans", methods=["GET", "POST"])
@_sa_required
def midtrans_settings():
    supabase = get_supabase()
    if request.method == "POST":
        data = {
            "merchant_id": request.form.get("merchant_id", "").strip(),
            "client_key": request.form.get("client_key", "").strip(),
            "server_key": request.form.get("server_key", "").strip(),
            "is_production": request.form.get("is_production", "false") == "true",
            "updated_by": g.user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            existing = supabase.table("midtrans_settings").select("id").limit(1).execute()
            if existing.data:
                supabase.table("midtrans_settings").update(data).eq("id", existing.data[0]["id"]).execute()
            else:
                supabase.table("midtrans_settings").insert(data).execute()
            log_activity("update", "midtrans_settings", "1", new_data={"merchant_id": data["merchant_id"][:6] + "***"}, user_id=g.user_id)
            flash("Pengaturan Midtrans berhasil disimpan", "success")
        except Exception as e:
            err = str(e)
            current_app.logger.error(f"Midtrans save error: {err}")
            if "relation" in err and "does not exist" in err:
                flash("Tabel midtrans_settings belum ada. Jalankan SQL migration (supabase/_COMPLETE_SETUP.sql) di Supabase SQL Editor.", "error")
            else:
                flash(f"Gagal menyimpan: {err[:100]}", "error")
        return redirect("/super-admin/midtrans")

    settings = {}
    try:
        res = supabase.table("midtrans_settings").select("*").limit(1).execute()
        if res.data:
            settings = res.data[0]
            # Mask keys for display
            if settings.get("server_key"):
                settings["server_key_display"] = settings["server_key"][:8] + "***" + settings["server_key"][-4:]
            if settings.get("client_key"):
                settings["client_key_display"] = settings["client_key"][:8] + "***" + settings["client_key"][-4:]
    except Exception:
        pass
    return render_template("super_admin/midtrans_settings.html", settings=settings)


# ─── Subscription Plans ───────────────────────────────────────────────

@super_bp.route("/plans")
@_sa_required
def subscription_plans():
    supabase = get_supabase()
    plans = []
    try:
        plans = supabase.table("subscription_plans").select("*").order("sort_order").execute().data or []
    except Exception:
        pass
    return render_template("super_admin/subscription_plans.html", plans=plans)


@super_bp.route("/plans/new", methods=["GET", "POST"])
@_sa_required
def plan_new():
    supabase = get_supabase()
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "duration_label": request.form.get("duration_label", "").strip(),
            "duration_days": int(request.form.get("duration_days", 0)),
            "price": float(request.form.get("price", 0)),
            "is_active": request.form.get("is_active", "true") == "true",
            "sort_order": int(request.form.get("sort_order", 0)),
        }
        try:
            supabase.table("subscription_plans").insert(data).execute()
            log_activity("create", "subscription_plan", data["name"], new_data=data, user_id=g.user_id)
            flash("Plan berhasil ditambahkan", "success")
        except Exception as e:
            flash(f"Gagal: {str(e)[:60]}", "error")
        return redirect("/super-admin/plans")
    return render_template("super_admin/subscription_plan_form.html", plan=None)


@super_bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@_sa_required
def plan_edit(plan_id):
    supabase = get_supabase()
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "duration_label": request.form.get("duration_label", "").strip(),
            "duration_days": int(request.form.get("duration_days", 0)),
            "price": float(request.form.get("price", 0)),
            "is_active": request.form.get("is_active", "true") == "true",
            "sort_order": int(request.form.get("sort_order", 0)),
        }
        try:
            supabase.table("subscription_plans").update(data).eq("id", plan_id).execute()
            log_activity("update", "subscription_plan", str(plan_id), new_data=data, user_id=g.user_id)
            flash("Plan berhasil diperbarui", "success")
        except Exception as e:
            flash(f"Gagal: {str(e)[:60]}", "error")
        return redirect("/super-admin/plans")

    plan = None
    try:
        res = supabase.table("subscription_plans").select("*").eq("id", plan_id).single().execute()
        plan = res.data
    except Exception:
        pass
    if not plan:
        flash("Plan tidak ditemukan", "error")
        return redirect("/super-admin/plans")
    return render_template("super_admin/subscription_plan_form.html", plan=plan)


@super_bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
@_sa_required
def plan_delete(plan_id):
    supabase = get_supabase()
    try:
        supabase.table("subscription_plans").delete().eq("id", plan_id).execute()
        log_activity("delete", "subscription_plan", str(plan_id), user_id=g.user_id)
        flash("Plan berhasil dihapus", "success")
    except Exception as e:
        flash(f"Gagal: {str(e)[:60]}", "error")
    return redirect("/super-admin/plans")


# ─── Trial Settings ───────────────────────────────────────────────────

@super_bp.route("/trial-settings", methods=["GET", "POST"])
@_sa_required
def trial_settings():
    supabase = get_supabase()
    if request.method == "POST":
        days = int(request.form.get("trial_days", 14))
        try:
            existing = supabase.table("trial_settings").select("id").limit(1).execute()
            data = {"trial_days": days, "updated_by": g.user_id, "updated_at": datetime.now(timezone.utc).isoformat()}
            if existing.data:
                supabase.table("trial_settings").update(data).eq("id", existing.data[0]["id"]).execute()
            else:
                supabase.table("trial_settings").insert(data).execute()
            log_activity("update", "trial_settings", "1", new_data={"trial_days": days}, user_id=g.user_id)
            flash(f"Trial duration diubah ke {days} hari", "success")
        except Exception as e:
            flash(f"Gagal: {str(e)[:60]}", "error")
        return redirect("/super-admin/trial-settings")

    trial = {"trial_days": 14}
    try:
        res = supabase.table("trial_settings").limit(1).execute()
        if res.data:
            trial = res.data[0]
    except Exception:
        pass
    return render_template("super_admin/trial_settings.html", trial=trial)


# ─── Payment Fee Settings ───────────────────────────────────────────────

@super_bp.route("/payment-fee-settings", methods=["GET", "POST"])
@_sa_required
def payment_fee_settings():
    supabase = get_supabase()
    if request.method == "POST":
        cfg = {
            "fee_flat": int(request.form.get("fee_flat", 0)),
            "fee_percent": float(request.form.get("fee_percent", 0)),
            "fee_note": request.form.get("fee_note", "").strip(),
        }
        try:
            existing = supabase.table("school_settings").select("id").eq("id", 1).execute()
            if existing.data:
                supabase.table("school_settings").update({"payment_fee_config": cfg}).eq("id", 1).execute()
            else:
                supabase.table("school_settings").insert({"id": 1, "payment_fee_config": cfg}).execute()
            log_activity("update", "payment_fee_settings", "1", new_data=cfg, user_id=g.user_id)
            flash("Pengaturan biaya admin berhasil disimpan", "success")
        except Exception as e:
            flash(f"Gagal: {str(e)[:60]}", "error")
        return redirect("/super-admin/payment-fee-settings")

    fee_config = {"fee_flat": 4000, "fee_percent": 0, "fee_note": "Biaya admin Rp 4.000 (transfer bank)"}
    try:
        res = supabase.table("school_settings").select("payment_fee_config").eq("id", 1).single().execute()
        if res.data and res.data.get("payment_fee_config"):
            fee_config = res.data["payment_fee_config"]
    except Exception:
        pass
    return render_template("super_admin/payment_fee_settings.html", fee_config=fee_config)


# ─── Pricing Settings ───────────────────────────────────────────────────

@super_bp.route("/pricing-settings", methods=["GET", "POST"])
@_sa_required
def pricing_settings():
    supabase = get_supabase()
    if request.method == "POST":
        model = request.form.get("pricing_model", "flat")
        tiers_raw = request.form.get("tiers", "[]")
        try:
            tiers = json.loads(tiers_raw)
        except (json.JSONDecodeError, TypeError):
            tiers = []
        config = {"model": model, "tiers": tiers}
        try:
            existing = supabase.table("school_settings").select("id").eq("id", 1).execute()
            if existing.data:
                supabase.table("school_settings").update({"pricing_config": config}).eq("id", 1).execute()
            else:
                supabase.table("school_settings").insert({"id": 1, "pricing_config": config}).execute()
            log_activity("update", "pricing_settings", "1", new_data=config, user_id=g.user_id)
            flash("Pengaturan pricing berhasil disimpan", "success")
        except Exception as e:
            flash(f"Gagal: {str(e)[:60]}", "error")
        return redirect("/super-admin/pricing-settings")

    config = {"model": "flat", "tiers": []}
    try:
        res = supabase.table("school_settings").select("pricing_config").eq("id", 1).single().execute()
        if res.data and res.data.get("pricing_config"):
            config = res.data["pricing_config"]
    except Exception:
        pass
    # Get total schools count for reference
    schools_total = _safe_count(supabase, "schools")
    return render_template("super_admin/pricing_settings.html", config=config, schools_total=schools_total)


# ─── Activation Code Management ─────────────────────────────────────────

@super_bp.route("/activation-codes")
@_sa_required
def activation_codes():
    supabase = get_supabase()
    q = request.args.get("q", "")
    schools = []
    try:
        data = supabase.table("schools").select("id, name, npsn, status").order("name").execute().data or []
        for s in data:
            # Get latest subscription
            sub = supabase.table("school_subscriptions") \
                .select("status, activation_code, subscription_start, subscription_end, subscription_plans!left(name)") \
                .eq("school_id", s["id"]) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            s["subscription"] = sub.data[0] if sub.data else {}
            # Get latest payment transaction
            tx = supabase.table("payment_transactions") \
                .select("order_id, gross_amount, status, activation_code, created_at") \
                .eq("school_id", s["id"]) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            s["last_transaction"] = tx.data[0] if tx.data else {}
        if q:
            data = [s for s in data if q.lower() in (s.get("name", "") + s.get("npsn", "")).lower()]
        schools = data
    except Exception as e:
        current_app.logger.error(f"Activation codes error: {e}")
    return render_template("super_admin/activation_codes.html", schools=schools, q=q)


@super_bp.route("/activation-codes/<school_id>/regenerate", methods=["POST"])
@_sa_required
def regenerate_activation_code(school_id):
    supabase = get_supabase()
    try:
        from app.services.midtrans_service import generate_activation_code
        code = generate_activation_code()
        supabase.table("school_subscriptions").update({
            "activation_code": code,
        }).eq("school_id", school_id).eq("status", "active").execute()
        log_activity("regenerate_activation_code", "school", school_id, new_data={"code": code}, user_id=g.user_id)
        flash(f"Kode aktivasi baru: {code}", "success")
    except Exception as e:
        flash(f"Gagal: {str(e)[:60]}", "error")
    return redirect("/super-admin/activation-codes")


@super_bp.route("/activation-codes/<school_id>/send-code", methods=["POST"])
@_sa_required
def send_activation_code(school_id):
    """Trigger activation for a school that paid but hasn't activated yet."""
    supabase = get_supabase()
    try:
        # Find latest successful payment without activation
        tx = supabase.table("payment_transactions") \
            .select("*") \
            .eq("school_id", school_id) \
            .eq("status", "success") \
            .is_("activation_code", "null") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        if tx.data:
            tx_data = tx.data[0]
            from app.services.midtrans_service import _activate_subscription
            _activate_subscription(school_id, tx_data["plan_id"], tx_data["order_id"], supabase)
            # Refresh to get the code
            sub = supabase.table("school_subscriptions") \
                .select("activation_code") \
                .eq("school_id", school_id) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            code = sub.data[0]["activation_code"] if sub.data else "-"
            flash(f"Langganan diaktifkan! Kode: {code}", "success")
        else:
            # No pending activation - just generate code
            from app.services.midtrans_service import generate_activation_code
            code = generate_activation_code()
            flash(f"Kode aktivasi (cadangan): {code}", "info")
    except Exception as e:
        flash(f"Gagal: {str(e)[:60]}", "error")
    return redirect("/super-admin/activation-codes")


# ─── NPSN Data Reset (Super Admin) ──────────────────────────────────────

@super_bp.route("/reset-school-data", methods=["GET", "POST"])
@_sa_required
def reset_school_data():
    supabase = get_supabase()
    school = None
    if request.method == "POST":
        npsn = request.form.get("npsn", "").strip()
        confirm = request.form.get("confirm", "") == "YA"
        if not npsn:
            flash("Masukkan NPSN", "error")
            return redirect("/super-admin/reset-school-data")
        if not confirm:
            flash("Ketik 'YA' untuk konfirmasi", "error")
            return redirect("/super-admin/reset-school-data")

        try:
            sch = supabase.table("schools").select("id, name, npsn").eq("npsn", npsn).single().execute()
            school = sch.data
        except Exception:
            flash(f"Sekolah dengan NPSN {npsn} tidak ditemukan", "error")
            return redirect("/super-admin/reset-school-data")

        school_id = school["id"]
        deleted = {"submissions": 0, "exams": 0, "students": 0, "teachers": 0, "transactions": 0, "subscriptions": 0}

        try:
            # 1. Delete submissions for all exams in this school
            exam_ids = supabase.table("exams").select("id").eq("school_id", school_id).execute().data or []
            eids = [e["id"] for e in exam_ids]
            if eids:
                for eid in eids:
                    res = supabase.table("submissions").delete().eq("exam_id", eid).execute()
                    deleted["submissions"] += len(res.data or [])
                # 2. Delete exams
                for eid in eids:
                    for tbl in ["violation_logs", "exam_access_codes", "analytics_cache"]:
                        supabase.table(tbl).delete().eq("exam_id", eid).execute()
                    supabase.table("exams").delete().eq("id", eid).execute()
                    deleted["exams"] += 1

            # 3. Delete teacher_assignments
            supabase.table("teacher_assignments").delete().eq("school_id", school_id).execute()

            # 4. Get and delete teacher profiles
            teachers = supabase.table("profiles").select("id").eq("school_id", school_id).eq("role", "guru").execute().data or []
            for t in teachers:
                try:
                    supabase.auth.admin.delete_user(t["id"])
                except:
                    pass
                supabase.table("teachers").delete().eq("id", t["id"]).execute()
                supabase.table("profiles").delete().eq("id", t["id"]).execute()
                deleted["teachers"] += 1

            # 5. Get and delete student profiles
            students = supabase.table("profiles").select("id").eq("school_id", school_id).eq("role", "murid").execute().data or []
            for st in students:
                try:
                    supabase.auth.admin.delete_user(st["id"])
                except:
                    pass
                supabase.table("students").delete().eq("id", st["id"]).execute()
                supabase.table("profiles").delete().eq("id", st["id"]).execute()
                deleted["students"] += 1

            # 6. Delete admin sekolah profile
            admins = supabase.table("profiles").select("id").eq("school_id", school_id).eq("role", "admin_sekolah").execute().data or []
            for a in admins:
                try:
                    supabase.auth.admin.delete_user(a["id"])
                except:
                    pass
                supabase.table("profiles").delete().eq("id", a["id"]).execute()

            # 7. Delete payment transactions and subscriptions
            res = supabase.table("payment_transactions").delete().eq("school_id", school_id).execute()
            deleted["transactions"] = len(res.data or [])
            res = supabase.table("school_subscriptions").delete().eq("school_id", school_id).execute()
            deleted["subscriptions"] = len(res.data or [])

            # 8. Delete classes, subjects, school_years
            supabase.table("classes").delete().eq("school_id", school_id).execute()
            supabase.table("subjects").delete().eq("school_id", school_id).execute()
            supabase.table("school_years").delete().eq("school_id", school_id).execute()

            # 9. Delete school
            supabase.table("schools").delete().eq("id", school_id).execute()

            log_activity("reset_school_data", "school", school_id, new_data={"npsn": npsn, "deleted": deleted}, user_id=g.user_id)
            flash(f"✅ Data sekolah {school['name']} (NPSN: {npsn}) berhasil di-reset. {deleted}", "success")
            return redirect("/super-admin/reset-school-data")

        except Exception as e:
            current_app.logger.error(f"Reset school data error: {e}")
            flash(f"Gagal: {str(e)[:80]}", "error")
            return redirect("/super-admin/reset-school-data")

    return render_template("super_admin/reset_school_data.html", school=school)
