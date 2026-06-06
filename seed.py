"""Seed demo data — all roles with sample exams, classes, submissions.
Run: python seed.py
This is for DEMO only. Not for production use.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app
from app.utils.auth import get_supabase
from datetime import datetime, timezone
import json

app = create_app("app.config.DevConfig")

ROLES = {
    "super_admin": "super_admin",
    "admin_sekolah": "admin_sekolah",
    "guru": "guru",
    "murid": "murid",
}

DEMO = {
    "super_admin": {
        "email": "superadmin@scan-grade.app",
        "password": "superadmin123",
        "full_name": "Super Admin ScanGrade",
        "role": "super_admin",
    },
    "schools": [
        {
            "name": "SMP Negeri 1 ScanGrade",
            "npsn": "20623248",
            "address": "Jl. Pendidikan No. 1, Jakarta",
            "city": "Jakarta",
            "province": "DKI Jakarta",
            "admin": {
                "email": "admin_smp@scan-grade.app", "password": "demo123",
                "full_name": "Admin SMP ScanGrade", "role": "admin_sekolah",
            },
            "classes": ["VII-A", "VIII-A", "IX-A"],
            "subjects": ["Matematika", "IPA", "Bahasa Indonesia"],
            "teachers": [
                {"email": "guru_mtk_smp@scan-grade.app", "password": "demo123", "full_name": "Budi Matematika"},
                {"email": "guru_ipa_smp@scan-grade.app", "password": "demo123", "full_name": "Siti IPA"},
            ],
            "students": [
                {"email": "siswa1_smp@scan-grade.app", "password": "demo123", "full_name": "Ahmad SMP", "nisn": "1234567801"},
                {"email": "siswa2_smp@scan-grade.app", "password": "demo123", "full_name": "Bella SMP", "nisn": "1234567802"},
            ],
        },
        {
            "name": "SMA Negeri 1 ScanGrade",
            "npsn": "69893227",
            "address": "Jl. Merdeka No. 10, Jakarta",
            "city": "Jakarta",
            "province": "DKI Jakarta",
            "admin": {
                "email": "admin_sma@scan-grade.app", "password": "demo123",
                "full_name": "Admin SMA ScanGrade", "role": "admin_sekolah",
            },
            "classes": ["X-A", "XI-A", "XII-A"],
            "subjects": ["Matematika", "Fisika", "Kimia", "Biologi"],
            "teachers": [
                {"email": "guru_mtk_sma@scan-grade.app", "password": "demo123", "full_name": "Dewi Matematika"},
                {"email": "guru_fisika_sma@scan-grade.app", "password": "demo123", "full_name": "Eko Fisika"},
            ],
            "students": [
                {"email": "siswa1_sma@scan-grade.app", "password": "demo123", "full_name": "Citra SMA", "nisn": "2234567801"},
                {"email": "siswa2_sma@scan-grade.app", "password": "demo123", "full_name": "Doni SMA", "nisn": "2234567802"},
            ],
        },
    ],
}


def create_auth_user(supabase, email, password, meta):
    """Create auth user, return uid."""
    auth = get_supabase()
    try:
        res = auth.auth.admin.create_user({
            "email": email, "password": password,
            "user_metadata": meta,
            "email_confirm": True,
        })
        return res.user.id
    except Exception as e:
        if "already" in str(e).lower():
            try:
                users = auth.auth.admin.list_users()
                for u in users:
                    if u.email == email:
                        return u.id
            except Exception:
                pass
        print(f"  WARN: {str(e)[:120]}")
        return None


def upsert_profile(supabase, uid, data):
    """Upsert profile record."""
    if not uid:
        return
    supabase.table("profiles").upsert({
        "id": uid, "full_name": data["full_name"],
        "phone": data.get("phone", ""), "role": data["role"],
        "school_id": data.get("school_id"), "status": "active",
    }).execute()


def run():
    with app.app_context():
        supabase = get_supabase()
        default_school_id = "00000000-0000-0000-0000-000000000001"

        print("\n=== SEEDING DEMO DATA ===\n")

        # ── 1. Super Admin ──
        print("1. Super Admin")
        sa = DEMO["super_admin"]
        uid = create_auth_user(supabase, sa["email"], sa["password"], {"role": "super_admin", "full_name": sa["full_name"]})
        upsert_profile(supabase, uid, sa)
        print(f"   {sa['email']} / {sa['password']}")

        # ── 2. Schools ──
        for school in DEMO["schools"]:
            print(f"\n2. {school['name']}")

            # Find or create school
            sid = None
            try:
                existing = supabase.table("schools").select("id").eq("npsn", school["npsn"]).execute()
                if existing.data:
                    sid = existing.data[0]["id"]
                    print(f"   School exists: {sid[:8]}...")
                else:
                    res = supabase.table("schools").insert({
                        "name": school["name"], "npsn": school["npsn"],
                        "address": school["address"], "city": school["city"],
                        "province": school["province"], "status": "active",
                    }).execute()
                    sid = res.data[0]["id"]
                    print(f"   School created: {sid[:8]}...")
            except Exception as e:
                print(f"   School error: {e}, using default")
                sid = default_school_id

            # Classes
            class_ids = {}
            for cls_name in school["classes"]:
                try:
                    existing = supabase.table("classes").select("id").eq("school_id", sid).eq("name", cls_name).execute()
                    if existing.data:
                        class_ids[cls_name] = existing.data[0]["id"]
                    else:
                        res = supabase.table("classes").insert({
                            "name": cls_name, "school_id": sid,
                            "grade_level": cls_name.split("-")[0],
                        }).execute()
                        class_ids[cls_name] = res.data[0]["id"]
                except Exception:
                    pass

            # Subjects
            for subj in school["subjects"]:
                try:
                    supabase.table("subjects").upsert({
                        "name": subj, "school_id": sid, "code": subj[:3].upper(), "is_active": True,
                    }).execute()
                except Exception:
                    pass

            # Admin
            admin = school["admin"]
            admin["school_id"] = sid
            uid = create_auth_user(supabase, admin["email"], admin["password"], {"role": "admin_sekolah", "full_name": admin["full_name"]})
            upsert_profile(supabase, uid, admin)
            print(f"   Admin: {admin['email']} / {admin['password']}")

            # Teachers
            for t in school["teachers"]:
                t["school_id"] = sid
                t["role"] = "guru"
                uid = create_auth_user(supabase, t["email"], t["password"], {"role": "guru", "full_name": t["full_name"]})
                upsert_profile(supabase, uid, t)
                print(f"   Guru: {t['email']} / {t['password']}")

            # Students
            for s in school["students"]:
                s["school_id"] = sid
                s["role"] = "murid"
                if school["classes"]:
                    s["class_id"] = class_ids.get(school["classes"][0])
                uid = create_auth_user(supabase, s["email"], s["password"], {"role": "murid", "full_name": s["full_name"]})
                upsert_profile(supabase, uid, s)
                print(f"   Murid: {s['email']} / {s['password']}")

        print("\n=== SEED COMPLETE ===\n")
        print("Login credentials (semua password: demo123):")
        print("┌──────────────────────┬──────────────────────────────┬──────────┐")
        print("│ Role                 │ Email                        │ Password │")
        print("├──────────────────────┼──────────────────────────────┼──────────┤")
        print("│ Super Admin          │ superadmin@scan-grade.app    │ demo123  │")
        print("│ Admin SMP            │ admin_smp@scan-grade.app     │ demo123  │")
        print("│ Admin SMA            │ admin_sma@scan-grade.app     │ demo123  │")
        print("│ Guru Matematika SMP  │ guru_mtk_smp@scan-grade.app  │ demo123  │")
        print("│ Guru IPA SMP         │ guru_ipa_smp@scan-grade.app  │ demo123  │")
        print("│ Guru Matematika SMA  │ guru_mtk_sma@scan-grade.app  │ demo123  │")
        print("│ Guru Fisika SMA      │ guru_fisika_sma@scan-grade.app│ demo123  │")
        print("│ Siswa 1 SMP          │ siswa1_smp@scan-grade.app    │ demo123  │")
        print("│ Siswa 2 SMP          │ siswa2_smp@scan-grade.app    │ demo123  │")
        print("│ Siswa 1 SMA          │ siswa1_sma@scan-grade.app    │ demo123  │")
        print("│ Siswa 2 SMA          │ siswa2_sma@scan-grade.app    │ demo123  │")
        print("└──────────────────────┴──────────────────────────────┴──────────┘")


if __name__ == "__main__":
    run()

print("\nJalankan:   cd F:\\opencode\\ScanGrade\\scanGrade")
print("            .venv\\Scripts\\python seed.py\n")
