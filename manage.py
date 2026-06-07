"""ScanGrade Demo/Production Environment Management.

Usage:
    python manage.py seed          # Seed demo data
    python manage.py reset         # Reset all demo data (clean + seed)
    python manage.py seed --exam   # Also create sample exams + submissions
    python manage.py list          # List all demo users
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env.demo if --demo flag, else default .env
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

DEMO_SCHOOL_ID = None  # Set during seeding

DEMO_USERS = {
    "super_admin": {
        "email": "superadmin@scan-grade.app", "password": "superadmin123",
        "full_name": "Super Admin ScanGrade", "role": "super_admin",
        "phone": "081111111111",
    },
}

DEMO_SCHOOLS = [
    {
        "name": "SMP Negeri 1 ScanGrade",
        "npsn": "20623248", "address": "Jl. Pendidikan No. 1, Jakarta",
        "city": "Jakarta", "province": "DKI Jakarta", "level": "SMP",
        "admin": {"email": "admin_smp@scan-grade.app", "password": "demo123", "full_name": "Admin SMP ScanGrade", "role": "admin_sekolah", "phone": "081222222221"},
        "classes": ["VII-A", "VII-B", "VIII-A", "VIII-B", "IX-A"],
        "subjects": ["Matematika", "IPA", "Bahasa Indonesia", "Bahasa Inggris", "IPS"],
        "teachers": [
            {"email": "guru_mtk_smp@scan-grade.app", "password": "demo123", "full_name": "Budi Matematika", "phone": "081333333331"},
            {"email": "guru_ipa_smp@scan-grade.app", "password": "demo123", "full_name": "Siti IPA", "phone": "081333333332"},
            {"email": "guru_bing_smp@scan-grade.app", "password": "demo123", "full_name": "Agus Inggris", "phone": "081333333336"},
        ],
        "students": [
            {"email": "siswa1_smp@scan-grade.app", "password": "demo123", "full_name": "Ahmad SMP", "nisn": "1234567801", "phone": "081444444441"},
            {"email": "siswa2_smp@scan-grade.app", "password": "demo123", "full_name": "Bella SMP", "nisn": "1234567802", "phone": "081444444442"},
            {"email": "siswa3_smp@scan-grade.app", "password": "demo123", "full_name": "Candra SMP", "nisn": "1234567803", "phone": "081444444445"},
        ],
    },
    {
        "name": "SMA Negeri 1 ScanGrade",
        "npsn": "69893227", "address": "Jl. Merdeka No. 10, Jakarta",
        "city": "Jakarta", "province": "DKI Jakarta", "level": "SMA",
        "admin": {"email": "admin_sma@scan-grade.app", "password": "demo123", "full_name": "Admin SMA ScanGrade", "role": "admin_sekolah", "phone": "081222222222"},
        "classes": ["X-A", "X-B", "XI-A", "XI-B", "XII-A"],
        "subjects": ["Matematika", "Fisika", "Kimia", "Biologi", "Bahasa Indonesia", "Sejarah"],
        "teachers": [
            {"email": "guru_mtk_sma@scan-grade.app", "password": "demo123", "full_name": "Dewi Matematika", "phone": "081333333333"},
            {"email": "guru_fisika_sma@scan-grade.app", "password": "demo123", "full_name": "Eko Fisika", "phone": "081333333334"},
            {"email": "guru_kimia_sma@scan-grade.app", "password": "demo123", "full_name": "Fitri Kimia", "phone": "081333333335"},
        ],
        "students": [
            {"email": "siswa1_sma@scan-grade.app", "password": "demo123", "full_name": "Citra SMA", "nisn": "2234567801", "phone": "081444444443"},
            {"email": "siswa2_sma@scan-grade.app", "password": "demo123", "full_name": "Doni SMA", "nisn": "2234567802", "phone": "081444444444"},
            {"email": "siswa3_sma@scan-grade.app", "password": "demo123", "full_name": "Eka SMA", "nisn": "2234567803", "phone": "081444444446"},
            {"email": "siswa4_sma@scan-grade.app", "password": "demo123", "full_name": "Fani SMA", "nisn": "2234567804", "phone": "081444444447"},
        ],
    },
    {
        "name": "SMK Teknologi ScanGrade",
        "npsn": "34567890", "address": "Jl. Industri No. 5, Bandung",
        "city": "Bandung", "province": "Jawa Barat", "level": "SMK",
        "admin": {"email": "admin_smk@scan-grade.app", "password": "demo123", "full_name": "Admin SMK ScanGrade", "role": "admin_sekolah", "phone": "081222222223"},
        "classes": ["X-RPL", "XI-RPL", "XII-RPL", "X-TKJ", "XI-TKJ"],
        "subjects": ["Pemrograman Dasar", "Komputer Jaringan", "Basis Data", "Matematika", "Bahasa Inggris"],
        "teachers": [
            {"email": "guru_prog_smk@scan-grade.app", "password": "demo123", "full_name": "Gunawan Programming", "phone": "081333333337"},
            {"email": "guru_jaring_smk@scan-grade.app", "password": "demo123", "full_name": "Hani Jaringan", "phone": "081333333338"},
        ],
        "students": [
            {"email": "siswa1_smk@scan-grade.app", "password": "demo123", "full_name": "Galih SMK", "nisn": "3234567801", "phone": "081444444448"},
            {"email": "siswa2_smk@scan-grade.app", "password": "demo123", "full_name": "Hesti SMK", "nisn": "3234567802", "phone": "081444444449"},
        ],
    },
]

SAMPLE_EXAMS = [
    {
        "title": "Ulangan Harian Matematika - Persamaan Linear",
        "subject": "Matematika",
        "duration_minutes": 60,
        "total_questions": 5,
        "passing_score": 70,
        "status": "active", "is_published": True,
        "question_types": {"0": "mcq", "1": "mcq", "2": "mcq", "3": "mcq", "4": "essay_text"},
        "answer_key": {"0": "B", "1": "A", "2": "D", "3": "C", "4": "essay"},
        "question_weights": {"0": 20, "1": 20, "2": 20, "3": 20, "4": 20},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
        "class_ids": [], "max_attempts": 1, "publish_mode": "auto",
    },
    {
        "title": "Tryout Fisika - Mekanika Dasar",
        "subject": "Fisika",
        "duration_minutes": 90,
        "total_questions": 5,
        "passing_score": 65,
        "status": "active", "is_published": True,
        "question_types": {"0": "mcq", "1": "mcq", "2": "mcq", "3": "essay_canvas", "4": "essay_text"},
        "answer_key": {"0": "C", "1": "B", "2": "A", "3": "essay", "4": "essay"},
        "question_weights": {"0": 20, "1": 20, "2": 20, "3": 20, "4": 20},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
        "class_ids": [], "max_attempts": 1, "publish_mode": "manual",
    },
    {
        "title": "Pemrograman Dasar - Logika Algoritma",
        "subject": "Pemrograman Dasar",
        "duration_minutes": 120,
        "total_questions": 4,
        "passing_score": 70,
        "status": "active", "is_published": True,
        "question_types": {"0": "mcq", "1": "essay_text", "2": "mcq", "3": "essay_text"},
        "answer_key": {"0": "D", "1": "essay", "2": "A", "3": "essay"},
        "question_weights": {"0": 25, "1": 25, "2": 25, "3": 25},
        "anti_cheat_enabled": True, "penalty_per_violation": 5,
        "class_ids": [], "max_attempts": 2, "publish_mode": "auto",
    },
]


# ─── HELPERS ─────────────────────────────────────────

def _create_user(supabase, data, school_id=None, class_id=None):
    """Create auth user + profile. Return uid or None if exists."""
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
    if school_id: profile["school_id"] = school_id
    if class_id: profile["class_id"] = class_id
    supabase.table("profiles").upsert(profile).execute()

    # Create extension records
    if data["role"] == "guru" and school_id:
        try: supabase.table("teachers").upsert({"id": uid, "school_id": school_id}).execute()
        except: pass
    elif data["role"] == "murid" and school_id:
        try: supabase.table("students").upsert({"id": uid, "school_id": school_id, "nisn": data.get("nisn","")}).execute()
        except: pass
    return uid


def _seed_school(supabase, school_conf):
    """Seed one school with admin, teachers, students, classes, subjects."""
    global DEMO_SCHOOL_ID
    print(f"\n📚 {school_conf['name']}")

    # Create school
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

    # Classes
    class_ids = {}
    for cls_name in school_conf["classes"]:
        try:
            ex = supabase.table("classes").select("id").eq("school_id", sid).eq("name", cls_name).execute()
            if ex.data:
                class_ids[cls_name] = ex.data[0]["id"]
            else:
                r = supabase.table("classes").insert({"name": cls_name, "school_id": sid,
                    "grade_level": cls_name.split("-")[0]}).execute()
                class_ids[cls_name] = r.data[0]["id"]
        except: pass

    # Subjects
    for subj in school_conf.get("subjects", []):
        try:
            supabase.table("subjects").upsert({"name": subj, "school_id": sid,
                "code": subj[:3].upper()}).execute()
        except: pass

    # Admin
    admin = school_conf["admin"]
    uid = _create_user(supabase, {**admin, "school_id": sid})
    print(f"   👤 Admin: {admin['email']} / {admin['password']}")

    # Teachers
    teacher_ids = []
    for t in school_conf.get("teachers", []):
        uid = _create_user(supabase, {**t, "role": "guru", "school_id": sid})
        if uid: teacher_ids.append(uid)
        print(f"   👨‍🏫 Guru: {t['email']} / {t['password']}")

    # Students with class assignment
    for i, s in enumerate(school_conf.get("students", [])):
        class_list = list(class_ids.values())
        cid = class_list[i % len(class_list)] if class_list else None
        uid = _create_user(supabase, {**s, "role": "murid", "school_id": sid, "class_id": cid})
        print(f"   🧑‍🎓 Murid: {s['email']} / {s['password']}")

    # Create school relations (assignments, year, subscription)
    _seed_school_relations(supabase, sid, school_conf, class_ids, teacher_ids)

    return sid


def _seed_school_relations(supabase, sid, school_conf, class_ids, teacher_ids):
    """Create teacher assignments, school year, and subscription for a school."""
    now = datetime.now(timezone.utc)

    # School year
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

    # Trial subscription
    try:
        existing = supabase.table("school_subscriptions").select("id").eq("school_id", sid).execute()
        if not existing.data:
            trial_days = 14
            supabase.table("school_subscriptions").insert({
                "school_id": sid, "status": "trial",
                "trial_days": trial_days,
                "trial_start": now.isoformat(),
                "trial_end": (now + timedelta(days=trial_days)).isoformat(),
            }).execute()
    except: pass

    # Teacher assignments: map teachers to classes & subjects
    subjects = []
    try:
        subjects = supabase.table("subjects").select("id, name").eq("school_id", sid).execute().data or []
    except: pass
    subject_map = {s["name"]: s["id"] for s in subjects}
    class_list = list(class_ids.values())

    for i, tid in enumerate(teacher_ids):
        cid = class_list[i % len(class_list)] if class_list else None
        subj_name = school_conf.get("subjects", [])[i % len(school_conf.get("subjects", [1]))] if school_conf.get("subjects") else None
        sid_val = subject_map.get(subj_name) if subj_name else None
        if cid and sid_val:
            try:
                supabase.table("teacher_assignments").upsert({
                    "teacher_id": tid, "class_id": cid,
                    "subject_id": sid_val, "school_id": sid,
                }).execute()
            except: pass


def _seed_exams(supabase, school_id, school_conf):
    """Create sample exams for a school's teachers."""
    from uuid import uuid4
    teachers = []
    try:
        for t in school_conf.get("teachers", []):
            prof = supabase.table("profiles").select("id").eq("email", t["email"]).execute()
            if prof.data: teachers.append(prof.data[0]["id"])
    except: pass
    if not teachers: return

    for i, exam_spec in enumerate(SAMPLE_EXAMS):
        teacher_id = teachers[i % len(teachers)]
        subject = exam_spec["subject"]
        # Only create exam if it matches school's subjects
        if subject not in school_conf.get("subjects", []):
            continue
        try:
            exam_data = {
                "teacher_id": teacher_id, "school_id": school_id,
                "title": exam_spec["title"], "subject": subject,
                "duration_minutes": exam_spec["duration_minutes"],
                "total_questions": exam_spec["total_questions"],
                "passing_score": exam_spec["passing_score"],
                "status": "active", "is_published": True,
                "question_types": json.dumps(exam_spec["question_types"]),
                "answer_key": json.dumps(exam_spec["answer_key"]),
                "question_weights": json.dumps(exam_spec["question_weights"]),
                "anti_cheat_enabled": exam_spec["anti_cheat_enabled"],
                "penalty_per_violation": exam_spec["penalty_per_violation"],
                "class_ids": json.dumps(exam_spec.get("class_ids", [])),
                "max_attempts": exam_spec.get("max_attempts", 1),
                "publish_mode": exam_spec.get("publish_mode", "manual"),
            }
            supabase.table("exams").insert(exam_data).execute()
            print(f"   📝 Exam created: {exam_spec['title']}")
        except Exception as e:
            if "already" not in str(e).lower():
                print(f"   ⚠️  Exam: {str(e)[:60]}")


def _reset_demo(supabase):
    """Delete all demo users and data."""
    for email in [u["email"] for u in [DEMO_USERS["super_admin"]]] \
        + [s["admin"]["email"] for s in DEMO_SCHOOLS] \
        + [t["email"] for s in DEMO_SCHOOLS for t in s.get("teachers", [])] \
        + [st["email"] for s in DEMO_SCHOOLS for st in s.get("students", [])]:
        try:
            for u in supabase.auth.admin.list_users():
                if u.email == email:
                    supabase.auth.admin.delete_user(u.id)
                    break
        except:
            pass


def _reset_demo_data(supabase):
    """Delete all demo data EXCEPT user accounts (keep emails/passwords)."""
    print("\n🔄 Resetting demo data (keeping user accounts)...")
    for table in ["submissions", "violation_logs", "exam_access_codes", "analytics_cache",
                   "teacher_assignments", "exams", "payment_transactions",
                   "school_subscriptions", "ai_grading_logs"]:
        try:
            supabase.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
            print(f"   ✅ Cleared: {table}")
        except Exception as e:
            print(f"   ⚠️  {table}: {str(e)[:50]}")
    # Clear class/subject/school data (recreated by seed)
    for table in ["students", "teachers", "subjects", "classes", "school_years",
                   "teacher_ai_keys", "teacher_ai_settings"]:
        try:
            supabase.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        except:
            pass
    print(f"   ✅ Cleared: schools child data")


# ─── COMMANDS ─────────────────────────────────────────

def cmd_seed(args):
    with app.app_context():
        supabase = get_supabase()
        print("=" * 50)
        print("🌱 SEEDING DEMO DATA")
        print("=" * 50)

        # Super admin
        print("\n👑 Super Admin")
        _create_user(supabase, DEMO_USERS["super_admin"])
        print(f"   {DEMO_USERS['super_admin']['email']} / {DEMO_USERS['super_admin']['password']}")

        # Schools
        all_school_ids = []
        for school in DEMO_SCHOOLS:
            sid = _seed_school(supabase, school)
            if sid: all_school_ids.append((sid, school))

        # Sample exams (optional)
        if args.exam:
            print("\n📝 Creating sample exams...")
            for sid, school in all_school_ids:
                _seed_exams(supabase, sid, school)

        print("\n" + "=" * 50)
        print("✅ SEED COMPLETE")
        print("=" * 50)
        _print_credentials()


def cmd_reset_data(args):
    with app.app_context():
        supabase = get_supabase()
        _reset_demo_data(supabase)
        print("\n✅ Data reset complete. Run 'python manage.py seed' to recreate fresh data.")


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
                prof = supabase.table("profiles").select("id,status").eq("id" if False else "id", "none").execute()
                status = "❓"
                for u in supabase.auth.admin.list_users():
                    if u.email == email:
                        status = "✅"; break
                else:
                    status = "❌"
            except:
                status = "❓"
            print(f"{roles.get(email,'?'):<20} {email:<35} {passwords.get(email,'?'):<15} {status:<10}")
        print()


def cmd_migrate(args):
    """Run pending SQL migrations."""
    import glob as gb
    migration_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase", "migrations")
    sql_files = sorted(gb.glob(os.path.join(migration_dir, "*.sql")))
    if not sql_files:
        print("No migration files found.")
        return

    # Try direct DB connection via environment
    db_url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DATABASE_URL")
    if not db_url:
        print("⚠️  DATABASE_URL not found in .env")
        print("   Set DATABASE_URL=postgresql://postgres:password@db.xxxxx.supabase.co:5432/postgres")
        print("   Or run the SQL manually in Supabase SQL Editor.")
        return

    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    for fpath in sql_files:
        fname = os.path.basename(fpath)
        print(f"Running {fname}...")
        with open(fpath, "r", encoding="utf-8") as f:
            sql = f.read()
        try:
            cur.execute(sql)
            print(f"  ✅ {fname}")
        except Exception as e:
            print(f"  ⚠️  {fname}: {e}")

    cur.close()
    conn.close()
    print("\nMigration complete.")


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
    parser.add_argument("command", choices=["seed", "reset", "reset-data", "list", "migrate"],
                        help="seed: create demo data | reset: delete ALL (users+data) | reset-data: keep users, reset data | list: show demo users | migrate: run pending SQL migrations")
    parser.add_argument("--exam", action="store_true", help="Also create sample exams (with seed)")
    parser.add_argument("--demo", action="store_true", help="Use .env.demo instead of .env")
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
        cmd_migrate(args)
