"""ScanGrade Demo/Production Environment Management.

Usage:
    python manage.py seed          # Seed demo data
    python manage.py reset         # Reset all demo data (clean + seed)
    python manage.py seed --exam   # Also create sample exams + submissions
    python manage.py list          # List all demo users
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if '--demo' in sys.argv:
    from dotenv import load_dotenv
    demo_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.demo')
    if os.path.exists(demo_env):
        load_dotenv(demo_env, override=True)
        print(f"📁 Using demo environment: {demo_env}")
    else:
        print("⚠️  .env.demo not found. Copy .env.production.example to .env.demo")
        sys.exit(1)

from datetime import datetime, timezone, timedelta
from app import create_app
from app.utils.auth import get_supabase

app = create_app("app.config.DevConfig")

# ─── DEMO DATA CONFIG ────────────────────────────────

DEMO_SCHOOL_ID = None

DEMO_USERS = {
    "super_admin": {
        "email": "superadmin@scan-grade.app", "password": "superadmin123",
        "full_name": "Super Admin ScanGrade", "role": "super_admin", "phone": "081111111111",
    },
}

DEMO_SCHOOLS = [
    {
        "name": "SMP Negeri 1 ScanGrade",
        "npsn": "99887711", "address": "Jl. Pendidikan No. 1, Jakarta",
        "city": "Jakarta", "province": "DKI Jakarta", "level": "SMP",
        "admin": {"email": "admin_smp@scan-grade.app", "password": "demo123", "full_name": "Admin SMP", "role": "admin_sekolah", "phone": "081222222221"},
        "classes": [
            {"name": "VII-A", "grade_level": "7"}, {"name": "VII-B", "grade_level": "7"},
            {"name": "VIII-A", "grade_level": "8"}, {"name": "VIII-B", "grade_level": "8"}, {"name": "IX-A", "grade_level": "9"},
        ],
        "subjects": [{"name": "Matematika", "code": "MTK"}, {"name": "IPA", "code": "IPA"}, {"name": "Bahasa Indonesia", "code": "BIN"}, {"name": "Bahasa Inggris", "code": "BIG"}, {"name": "IPS", "code": "IPS"}],
        "teachers": [
            {"email": "guru_mtk_smp@scan-grade.app", "password": "demo123", "full_name": "Budi Matematika", "phone": "081333333331", "subject": "Matematika"},
            {"email": "guru_ipa_smp@scan-grade.app", "password": "demo123", "full_name": "Siti IPA", "phone": "081333333332", "subject": "IPA"},
            {"email": "guru_bing_smp@scan-grade.app", "password": "demo123", "full_name": "Agus Inggris", "phone": "081333333336", "subject": "Bahasa Inggris"},
            {"email": "guru_bindo_smp@scan-grade.app", "password": "demo123", "full_name": "Dewi Indonesia", "phone": "081333333339", "subject": "Bahasa Indonesia"},
        ],
        "students": [
            {"email": "siswa1_smp@scan-grade.app", "password": "demo123", "full_name": "Ahmad Pratama", "nisn": "1000000001", "phone": "081444444441"},
            {"email": "siswa2_smp@scan-grade.app", "password": "demo123", "full_name": "Bella Safira", "nisn": "1000000002", "phone": "081444444442"},
        ],
    },
    {
        "name": "SMA Negeri 1 ScanGrade",
        "npsn": "99887722", "address": "Jl. Merdeka No. 10, Jakarta",
        "city": "Jakarta", "province": "DKI Jakarta", "level": "SMA",
        "admin": {"email": "admin_sma@scan-grade.app", "password": "demo123", "full_name": "Admin SMA", "role": "admin_sekolah", "phone": "081222222222"},
        "classes": [
            {"name": "X-A", "grade_level": "10"}, {"name": "X-B", "grade_level": "10"},
            {"name": "XI-A", "grade_level": "11"}, {"name": "XI-B", "grade_level": "11"}, {"name": "XII-A", "grade_level": "12"},
        ],
        "subjects": [{"name": "Matematika", "code": "MTK"}, {"name": "Fisika", "code": "FIS"}, {"name": "Kimia", "code": "KIM"}, {"name": "Biologi", "code": "BIO"}, {"name": "Bahasa Indonesia", "code": "BIN"}, {"name": "Sejarah", "code": "SJH"}],
        "teachers": [
            {"email": "guru_mtk_sma@scan-grade.app", "password": "demo123", "full_name": "Dewi Matematika", "phone": "081333333333", "subject": "Matematika"},
            {"email": "guru_fisika_sma@scan-grade.app", "password": "demo123", "full_name": "Eko Fisika", "phone": "081333333334", "subject": "Fisika"},
            {"email": "guru_kimia_sma@scan-grade.app", "password": "demo123", "full_name": "Fitri Kimia", "phone": "081333333335", "subject": "Kimia"},
            {"email": "guru_bio_sma@scan-grade.app", "password": "demo123", "full_name": "Galih Biologi", "phone": "081333333340", "subject": "Biologi"},
        ],
        "students": [
            {"email": "siswa1_sma@scan-grade.app", "password": "demo123", "full_name": "Citra Amalia", "nisn": "2000000001", "phone": "081444444447"},
            {"email": "siswa2_sma@scan-grade.app", "password": "demo123", "full_name": "Doni Prasetyo", "nisn": "2000000002", "phone": "081444444448"},
        ],
    },
    {
        "name": "SMK Teknologi ScanGrade",
        "npsn": "99887733", "address": "Jl. Industri No. 5, Bandung",
        "city": "Bandung", "province": "Jawa Barat", "level": "SMK",
        "admin": {"email": "admin_smk@scan-grade.app", "password": "demo123", "full_name": "Admin SMK", "role": "admin_sekolah", "phone": "081222222223"},
        "classes": [
            {"name": "X-RPL", "grade_level": "10"}, {"name": "XI-RPL", "grade_level": "11"}, {"name": "XII-RPL", "grade_level": "12"},
            {"name": "X-TKJ", "grade_level": "10"}, {"name": "XI-TKJ", "grade_level": "11"},
        ],
        "subjects": [{"name": "Pemrograman Dasar", "code": "PRO"}, {"name": "Komputer Jaringan", "code": "KOM"}, {"name": "Basis Data", "code": "BAS"}, {"name": "Matematika", "code": "MTK"}, {"name": "Bahasa Inggris", "code": "BIG"}],
        "teachers": [
            {"email": "guru_prog_smk@scan-grade.app", "password": "demo123", "full_name": "Hendra Putra", "phone": "081333333337", "subject": "Pemrograman Dasar"},
            {"email": "guru_jaring_smk@scan-grade.app", "password": "demo123", "full_name": "Indah Sari", "phone": "081333333338", "subject": "Komputer Jaringan"},
            {"email": "guru_basis_smk@scan-grade.app", "password": "demo123", "full_name": "Joko Santoso", "phone": "081333333341", "subject": "Basis Data"},
        ],
        "students": [
            {"email": "siswa1_smk@scan-grade.app", "password": "demo123", "full_name": "Galih Saputra", "nisn": "3000000001", "phone": "081444444452"},
            {"email": "siswa2_smk@scan-grade.app", "password": "demo123", "full_name": "Hesti Purnama", "nisn": "3000000002", "phone": "081444444453"},
        ],
    },
]

SAMPLE_EXAMS = [
    {
        "title": "Ulangan Harian - Persamaan Linear", "subject": "Matematika",
        "duration_minutes": 60, "total_questions": 5, "passing_score": 70,
        "question_types": {"0": "mcq", "1": "mcq", "2": "mcq", "3": "mcq", "4": "essay_text"},
        "answer_key": {"0": "B", "1": "A", "2": "D", "3": "C", "4": "essay"},
        "question_weights": {"0": 20, "1": 20, "2": 20, "3": 20, "4": 20},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
    },
    {
        "title": "Tryout Fisika - Mekanika", "subject": "Fisika",
        "duration_minutes": 90, "total_questions": 5, "passing_score": 65,
        "question_types": {"0": "mcq", "1": "mcq", "2": "mcq", "3": "essay_canvas", "4": "essay_text"},
        "answer_key": {"0": "C", "1": "B", "2": "A", "3": "essay", "4": "essay"},
        "question_weights": {"0": 20, "1": 20, "2": 20, "3": 20, "4": 20},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
    },
    {
        "title": "Pemrograman Dasar - Logika Algoritma", "subject": "Pemrograman Dasar",
        "duration_minutes": 120, "total_questions": 4, "passing_score": 70,
        "question_types": {"0": "mcq", "1": "essay_text", "2": "mcq", "3": "essay_text"},
        "answer_key": {"0": "D", "1": "essay", "2": "A", "3": "essay"},
        "question_weights": {"0": 25, "1": 25, "2": 25, "3": 25},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
    },
]


# ─── HELPERS ─────────────────────────────────────────

def _create_user(supabase, data, school_id=None, class_id=None):
    try:
        res = supabase.auth.admin.create_user({
            "email": data["email"], "password": data["password"],
            "user_metadata": {"role": data["role"], "full_name": data["full_name"]},
            "email_confirm": True,
        })
        uid = res.user.id
    except Exception as e:
        if "already" in str(e).lower():
            try:
                for u in supabase.auth.admin.list_users():
                    if u.email == data["email"]:
                        uid = u.id; break
                else:
                    return None
            except:
                return None
        else:
            print(f"  ⚠️  {data['email']}: {str(e)[:80]}")
            return None

    profile = {"id": uid, "full_name": data["full_name"], "phone": data.get("phone", ""),
               "role": data["role"], "status": "active"}
    # Support both parameter and data dict for school_id (backward compat)
    sid = school_id or data.get("school_id")
    cid = class_id or data.get("class_id")
    if sid: profile["school_id"] = sid
    if cid: profile["class_id"] = cid
    supabase.table("profiles").upsert(profile).execute()

    if data["role"] == "guru" and sid:
        try: supabase.table("teachers").upsert({"id": uid, "school_id": sid}).execute()
        except: pass
    elif data["role"] == "murid" and sid:
        try: supabase.table("students").upsert({"id": uid, "school_id": sid, "nisn": data.get("nisn","")}).execute()
        except: pass
    return uid


def _seed_school(supabase, school_conf):
    global DEMO_SCHOOL_ID
    print(f"\n📚 {school_conf['name']}")

    sid = None
    try:
        existing = supabase.table("schools").select("id").eq("npsn", school_conf["npsn"]).execute()
        if existing.data:
            sid = existing.data[0]["id"]
            print(f"   School exists: {sid[:8]}...")
        else:
            res = supabase.table("schools").insert({
                "name": school_conf["name"], "npsn": school_conf["npsn"],
                "address": school_conf["address"], "city": school_conf["city"],
                "province": school_conf["province"], "status": "active",
            }).execute()
            sid = res.data[0]["id"]
            print(f"   School created: {sid[:8]}...")
    except Exception as e:
        print(f"   ⚠️  School error: {str(e)[:60]}")
        return None

    DEMO_SCHOOL_ID = sid

    class_ids = {}
    for cls in school_conf["classes"]:
        try:
            ex = supabase.table("classes").select("id").eq("school_id", sid).eq("name", cls["name"]).execute()
            if ex.data:
                class_ids[cls["name"]] = ex.data[0]["id"]
            else:
                r = supabase.table("classes").insert({"name": cls["name"], "school_id": sid, "grade_level": cls["grade_level"]}).execute()
                class_ids[cls["name"]] = r.data[0]["id"]
        except Exception as e:
            print(f"   ⚠️  Class error: {e}")

    subj_map = {}
    for subj in school_conf.get("subjects", []):
        try:
            ex = supabase.table("subjects").select("id").eq("school_id", sid).eq("name", subj["name"]).execute()
            if ex.data:
                subj_map[subj["name"]] = ex.data[0]["id"]
            else:
                r = supabase.table("subjects").insert({"name": subj["name"], "school_id": sid, "code": subj["code"]}).execute()
                subj_map[subj["name"]] = r.data[0]["id"]
        except Exception as e:
            print(f"   ⚠️  Subject error: {e}")

    uid = _create_user(supabase, {**school_conf["admin"], "school_id": sid})
    print(f"   👤 Admin: {school_conf['admin']['email']} / {school_conf['admin']['password']}")

    teacher_ids = []
    for t in school_conf.get("teachers", []):
        uid = _create_user(supabase, {**t, "role": "guru", "school_id": sid})
        if uid: teacher_ids.append(uid)
        print(f"   👨‍🏫 Guru: {t['email']} / {t['password']}")

    class_list = list(class_ids.values())
    for i, s in enumerate(school_conf.get("students", [])):
        cid = class_list[i % len(class_list)] if class_list else None
        uid = _create_user(supabase, {**s, "role": "murid", "school_id": sid, "class_id": cid})
        print(f"   🧑‍🎓 Murid: {s['email']} / {s['password']}")

    _seed_school_relations(supabase, sid, school_conf, class_ids, subj_map, teacher_ids)
    return sid


def _seed_school_relations(supabase, sid, school_conf, class_ids, subj_map, teacher_ids):
    now = datetime.now(timezone.utc)

    try:
        year_name = f"{now.year}/{now.year+1}"
        existing = supabase.table("school_years").select("id").eq("school_id", sid).eq("name", year_name).execute()
        if not existing.data:
            supabase.table("school_years").insert({
                "school_id": sid, "name": year_name,
                "start_date": now.isoformat(),
                "end_date": (now + timedelta(days=365)).isoformat(),
                "is_active": True,
            }).execute()
    except: pass

    try:
        existing = supabase.table("school_subscriptions").select("id").eq("school_id", sid).execute()
        if not existing.data:
            supabase.table("school_subscriptions").insert({
                "school_id": sid, "status": "trial",
                "trial_days": 14,
                "trial_start": now.isoformat(),
                "trial_end": (now + timedelta(days=14)).isoformat(),
            }).execute()
    except: pass

    class_list = list(class_ids.values())
    for i, tid in enumerate(teacher_ids):
        cid = class_list[i % len(class_list)] if class_list else None
        subj_config = school_conf.get("teachers", [])[i] if i < len(school_conf.get("teachers", [])) else None
        subj_name = subj_config.get("subject", "") if subj_config else ""
        sid_val = subj_map.get(subj_name)
        if cid and sid_val:
            try:
                supabase.table("teacher_assignments").upsert({
                    "teacher_id": tid, "class_id": cid,
                    "subject_id": sid_val, "school_id": sid,
                }).execute()
            except: pass


def _seed_exams(supabase, school_id, school_conf):
    teachers = []
    try:
        for t in school_conf.get("teachers", []):
            prof = supabase.table("profiles").select("id").eq("email", t["email"]).execute()
            if prof.data: teachers.append(prof.data[0]["id"])
    except: pass
    if not teachers: return

    class_ids = []
    class_ids_all = []
    try:
        cls = supabase.table("classes").select("id").eq("school_id", school_id).order("name").execute().data or []
        class_ids_all = [c["id"] for c in cls]
        class_ids = [class_ids_all[0]] if class_ids_all else []
    except: pass

    for i, exam_spec in enumerate(SAMPLE_EXAMS):
        teacher_id = teachers[i % len(teachers)]
        subject = exam_spec["subject"]
        if subject not in [s["name"] for s in school_conf.get("subjects", [])]:
            continue
        try:
            exam_data = {
                "teacher_id": teacher_id, "school_id": school_id,
                "title": exam_spec["title"], "subject": subject,
                "duration_minutes": exam_spec["duration_minutes"],
                "total_questions": exam_spec["total_questions"],
                "passing_score": exam_spec["passing_score"],
                "status": "active", "is_published": True,
                "question_types": exam_spec["question_types"],
                "answer_key": exam_spec["answer_key"],
                "question_weights": exam_spec["question_weights"],
                "anti_cheat_enabled": exam_spec["anti_cheat_enabled"],
                "penalty_per_violation": exam_spec["penalty_per_violation"],
                "class_ids": class_ids,
                "max_attempts": 1, "publish_mode": "auto",
            }
            supabase.table("exams").insert(exam_data).execute()
        except Exception as e:
            if "already" not in str(e).lower():
                print(f"   ⚠️  Exam: {str(e)[:60]}")


def _seed_invoices(supabase, school_id, school_conf):
    """Create sample invoices for demo purposes."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    plans = supabase.table("subscription_plans").select("*").order("sort_order").limit(3).execute().data or []
    if not plans:
        return
    for i, p in enumerate(plans):
        inv_num = f"INV-DEMO-{now.year}-{i+1:03d}"
        # Check if already exists
        existing = supabase.table("invoices").select("id").eq("invoice_number", inv_num).limit(1).execute()
        if existing.data:
            continue
        start = now - timedelta(days=i * 60)
        end = now + timedelta(days=p.get("duration_days", 30) - i * 60) if p.get("duration_days", 0) > 0 else None
        try:
            supabase.table("invoices").insert({
                "invoice_number": inv_num,
                "school_id": school_id,
                "order_id": f"DEMO-ORDER-{i+1}",
                "plan_id": p["id"],
                "amount": p["price"],
                "status": "paid",
                "payment_method": "midtrans" if i > 0 else "cash",
                "period_start": start.isoformat(),
                "period_end": end.isoformat() if end else None,
                "paid_at": now.isoformat(),
                "due_at": (now + timedelta(days=7)).isoformat(),
                "notes": f"Langganan {p['name']}",
                "activation_code": f"SG-{i+1:04d}-{i+2:04d}-{i+3:04d}",
            }).execute()
            print(f"   📄 Invoice: {inv_num} - {p['name']}")
        except Exception as e:
            if "already" not in str(e).lower():
                print(f"   ⚠️  Invoice: {str(e)[:60]}")


def _reset_demo_data(supabase):
    print("\n🔄 Resetting demo data (keeping user accounts)...")
    # Delete in FK-safe order: children first, parents last
    tables_in_order = [
        "submissions", "violation_logs", "exam_access_codes", "analytics_cache",
        "teacher_assignments", "exams", "payment_transactions",
        "school_subscriptions", "ai_grading_logs", "invoices",
        "usage_tracking", "teacher_ai_keys", "teacher_ai_settings",
        "students", "teachers", "subjects", "classes", "school_years",
    ]
    for table in tables_in_order:
        try:
            supabase.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"   ✅ Cleared: {table}")
        except Exception as e:
            print(f"   ⚠️  {table}: {str(e)[:50]}")


def _reset_demo(supabase):
    """Full reset: delete all demo data AND users, then re-seed."""
    # First clean data
    _reset_demo_data(supabase)
    # Then delete demo users
    demo_emails = (
        [DEMO_USERS["super_admin"]["email"]]
        + [s["admin"]["email"] for s in DEMO_SCHOOLS]
        + [t["email"] for s in DEMO_SCHOOLS for t in s.get("teachers", [])]
        + [st["email"] for s in DEMO_SCHOOLS for st in s.get("students", [])]
    )
    for email in demo_emails:
        try:
            users = supabase.auth.admin.list_users()
            for u in users:
                if u.email == email:
                    supabase.auth.admin.delete_user(u.id)
                    break
        except Exception:
            pass


# ─── COMMANDS ─────────────────────────────────────────

def cmd_seed(args):
    with app.app_context():
        supabase = get_supabase()
        print("=" * 50)
        print("🌱 SEEDING DEMO DATA")
        print("=" * 50)
        print("\n👑 Super Admin")
        _create_user(supabase, DEMO_USERS["super_admin"])
        print(f"   {DEMO_USERS['super_admin']['email']} / {DEMO_USERS['super_admin']['password']}")

        all_school_ids = []
        for school in DEMO_SCHOOLS:
            sid = _seed_school(supabase, school)
            if sid: all_school_ids.append((sid, school))

        if args.exam:
            print("\n📝 Creating sample exams...")
            for sid, school in all_school_ids:
                _seed_exams(supabase, sid, school)

        print("\n📄 Creating sample invoices...")
        for sid, school in all_school_ids:
            _seed_invoices(supabase, sid, school)

        print("\n" + "=" * 50)
        print("✅ SEED COMPLETE")
        print("=" * 50)
        _print_credentials()


def cmd_reset_data(args):
    with app.app_context():
        supabase = get_supabase()
        _reset_demo_data(supabase)
        print("\n✅ Data reset complete. Run 'python manage.py seed' to recreate.")


def cmd_reset(args):
    with app.app_context():
        supabase = get_supabase()
        _reset_demo(supabase)
        print("\n✅ Reset complete. Run 'python manage.py seed' to recreate.")


def cmd_list(args):
    with app.app_context():
        supabase = get_supabase()
        print("\n📋 Demo Users:")
        print(f"{'Role':<20} {'Email':<35} {'Password':<15} {'Status':<10}")
        print("-" * 80)
        all_emails = ([DEMO_USERS["super_admin"]["email"]]
            + [s["admin"]["email"] for s in DEMO_SCHOOLS]
            + [t["email"] for s in DEMO_SCHOOLS for t in s.get("teachers", [])]
            + [st["email"] for s in DEMO_SCHOOLS for st in s.get("students", [])])
        passwords = {DEMO_USERS["super_admin"]["email"]: DEMO_USERS["super_admin"]["password"]}
        for s in DEMO_SCHOOLS:
            passwords[s["admin"]["email"]] = s["admin"]["password"]
            for t in s.get("teachers", []): passwords[t["email"]] = t["password"]
            for st in s.get("students", []): passwords[st["email"]] = st["password"]
        roles = {DEMO_USERS["super_admin"]["email"]: "super_admin"}
        for s in DEMO_SCHOOLS:
            roles[s["admin"]["email"]] = "admin_sekolah"
            for t in s.get("teachers", []): roles[t["email"]] = "guru"
            for st in s.get("students", []): roles[st["email"]] = "murid"
        for email in all_emails:
            try:
                status = "❌"
                for u in supabase.auth.admin.list_users():
                    if u.email == email:
                        status = "✅"; break
            except:
                status = "❓"
            print(f"{roles.get(email,'?'):<20} {email:<35} {passwords.get(email,'?'):<15} {status:<10}")
        print()


def _print_credentials():
    print()
    print("🔑 Login Credentials:")
    print(f"   Super Admin  : {DEMO_USERS['super_admin']['email']} / {DEMO_USERS['super_admin']['password']}")
    for s in DEMO_SCHOOLS:
        print(f"   Admin {s['level']:<4}: {s['admin']['email']} / {s['admin']['password']}")
        for t in s.get("teachers", []):
            print(f"   Guru {s['level']:<5}: {t['email']} / {t['password']}")
        for st in s.get("students", []):
            print(f"   Murid {s['level']:<4}: {st['email']} / {st['password']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ScanGrade Data Management")
    parser.add_argument("command", choices=["seed", "reset", "reset-data", "list", "migrate", "generate-csv"])
    parser.add_argument("--exam", action="store_true", help="Also create sample exams (with seed)")
    parser.add_argument("--demo", action="store_true", help="Use .env.demo")
    args = parser.parse_args()

    if args.command == "seed":
        cmd_seed(args)
    elif args.command == "reset":
        cmd_reset(args)
    elif args.command == "reset-data":
        cmd_reset_data(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "migrate":
        print("Migrate not available without DATABASE_URL")
    elif args.command == "generate-csv":
        _cmd_generate_csv()


def _cmd_generate_csv():
    import csv, io
    from flask import send_file
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_students.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["nama", "nisn", "email", "kelas", "password"])
        for i in range(1, 101):
            w.writerow([f"Siswa {i}", f"{1234567890 + i}", f"siswa{i}@sekolah.id", f"X IPA {i % 5 + 1}", "siswa123"])
    print(f"✅ Generated sample CSV: {path}")
    print(f"   Import via: /students/import")
