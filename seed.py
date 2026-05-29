"""Seed script: Super Admin + 2 Schools (SMP & SMA) with admin/guru/murid."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.utils.auth import get_supabase

app = create_app("app.config.DevConfig")

SEEDS = {
    "super_admin": {
        "email": "superadmin@scan-grade.app",
        "password": "superadmin123",
        "full_name": "Super Admin",
        "role": "admin",
        "phone": "081111111111",
    },
    "schools": [
        {
            "name": "SMP Negeri 1",
            "npsn": "20623248",
            "address": "Jl. Pendidikan No. 1",
            "city": "Jakarta",
            "province": "DKI Jakarta",
            "level": "SMP",
            "admin": {
                "email": "admin_smp@scan-grade.app",
                "password": "smpadmin123",
                "full_name": "Admin SMP Negeri 1",
                "phone": "081222222221",
            },
            "classes": ["VII-A", "VIII-A", "IX-A"],
            "teachers": [
                {"email": "guru1_smp@scan-grade.app", "password": "guru123", "full_name": "Guru Matematika SMP", "phone": "081333333331"},
                {"email": "guru2_smp@scan-grade.app", "password": "guru123", "full_name": "Guru IPA SMP", "phone": "081333333332"},
            ],
            "students": [
                {"email": "murid1_smp@scan-grade.app", "password": "murid123", "full_name": "Murid 1 SMP", "nisn": "1234567801", "phone": "081444444441"},
                {"email": "murid2_smp@scan-grade.app", "password": "murid123", "full_name": "Murid 2 SMP", "nisn": "1234567802", "phone": "081444444442"},
            ],
        },
        {
            "name": "SMA Negeri 1",
            "npsn": "69893227",
            "address": "Jl. Merdeka No. 10",
            "city": "Jakarta",
            "province": "DKI Jakarta",
            "level": "SMA",
            "admin": {
                "email": "admin_sma@scan-grade.app",
                "password": "smaadmin123",
                "full_name": "Admin SMA Negeri 1",
                "phone": "081222222222",
            },
            "classes": ["X-A", "XI-A", "XII-A"],
            "teachers": [
                {"email": "guru1_sma@scan-grade.app", "password": "guru123", "full_name": "Guru Matematika SMA", "phone": "081333333333"},
                {"email": "guru2_sma@scan-grade.app", "password": "guru123", "full_name": "Guru Fisika SMA", "phone": "081333333334"},
            ],
            "students": [
                {"email": "murid1_sma@scan-grade.app", "password": "murid123", "full_name": "Murid 1 SMA", "nisn": "2234567801", "phone": "081444444443"},
                {"email": "murid2_sma@scan-grade.app", "password": "murid123", "full_name": "Murid 2 SMA", "nisn": "2234567802", "phone": "081444444444"},
            ],
        },
    ],
}


def create_user(supabase, data):
    """Create auth user + profile. Returns user ID."""
    email = data["email"]
    password = data["password"]
    full_name = data["full_name"]
    role = data["role"]
    phone = data.get("phone", "")
    school_id = data.get("school_id")
    nisn = data.get("nisn", "")
    class_id = data.get("class_id")

    print(f"  Creating user: {email} ({role})...", end=" ")

    try:
        res = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {
                "role": role,
                "full_name": full_name,
            },
            "email_confirm": True,
        })
        uid = res.user.id
    except Exception as e:
        msg = str(e)
        if "already" in msg.lower():
            print(f"auth user exists")
            # Look up existing user by listing auth users
            try:
                users = supabase.auth.admin.list_users()
                uid = None
                for u in users:
                    if u.email == email:
                        uid = u.id
                        break
                if not uid:
                    print(f"SKIP (cant find existing user)")
                    return None
            except Exception as e2:
                print(f"SKIP (cant list users: {str(e2)[:40]})")
                return None

            # Ensure profile exists
            prof = supabase.table("profiles").select("id").eq("id", uid).execute()
            if not prof.data:
                supabase.table("profiles").insert({
                    "id": uid,
                    "full_name": full_name,
                    "phone": phone,
                    "role": role,
                }).execute()
                print(f"  -> profile created")
            else:
                print(f"  -> profile ok")
        else:
            print(f"ERROR: {e}")
            return None

    profile = {
        "id": uid,
        "full_name": full_name,
        "phone": phone,
        "role": role,
    }

    supabase.table("profiles").upsert(profile).execute()

    # Try creating extension records (tables may not exist)
    if role in ("guru", "teacher") and school_id:
        try:
            supabase.table("teachers").upsert({"id": uid, "school_id": school_id}).execute()
        except Exception:
            pass
    elif role in ("murid", "student") and school_id:
        try:
            sd = {"id": uid, "school_id": school_id}
            if nisn:
                sd["nisn"] = nisn
            if class_id:
                sd["class_id"] = class_id
            supabase.table("students").upsert(sd).execute()
        except Exception:
            pass

    print(f"OK ({uid[:8]}...)")
    return uid


def run():
    with app.app_context():
        supabase = get_supabase()

        # ─── SUPER ADMIN ──────────────────────────
        print("\n=== Super Admin ===")
        sa = SEEDS["super_admin"]
        create_user(supabase, sa)

        # ─── SCHOOLS ──────────────────────────────
        for school_conf in SEEDS["schools"]:
            name = school_conf["name"]
            npsn = school_conf["npsn"]
            level = school_conf["level"]
            print(f"\n=== {name} (NPSN: {npsn}) ===")

            # Create school
            print(f"  Creating school...", end=" ")
            school_id = None
            try:
                existing = supabase.table("schools").select("id").eq("npsn", npsn).execute()
                if existing.data:
                    school_id = existing.data[0]["id"]
                    print(f"already exists ({school_id[:8]}...)")
                else:
                    res = supabase.table("schools").insert({
                        "name": name,
                        "npsn": npsn,
                        "address": school_conf["address"],
                        "city": school_conf["city"],
                        "province": school_conf["province"],
                        "status": "active",
                    }).execute()
                    school_id = res.data[0]["id"]
                    print(f"OK ({school_id[:8]}...)")
            except Exception as e:
                print(f"SKIP (schools table: {str(e)[:40]})")

            # Create classes
            class_ids = {}
            if school_id:
                print(f"  Creating classes...")
                for cls_name in school_conf["classes"]:
                    try:
                        existing = supabase.table("classes").select("id, name").eq("school_id", school_id).eq("name", cls_name).execute()
                        if existing.data:
                            class_ids[cls_name] = existing.data[0]["id"]
                            print(f"    {cls_name}: already exists")
                        else:
                            res = supabase.table("classes").insert({
                                "name": cls_name,
                                "school_id": school_id,
                                "grade_level": cls_name.split("-")[0],
                            }).execute()
                            class_ids[cls_name] = res.data[0]["id"]
                            print(f"    {cls_name}: OK")
                    except Exception as e:
                        print(f"    {cls_name}: SKIP ({str(e)[:30]})")

            # Admin sekolah
            print(f"  Admin sekolah...")
            admin_data = school_conf["admin"]
            admin_data["role"] = "admin"
            if school_id:
                admin_data["school_id"] = school_id
            create_user(supabase, admin_data)

            # Teachers
            print(f"  Teachers...")
            for t in school_conf["teachers"]:
                t["role"] = "teacher"
                if school_id:
                    t["school_id"] = school_id
                create_user(supabase, t)

            # Students
            print(f"  Students...")
            for s in school_conf["students"]:
                s["role"] = "student"
                if school_id:
                    s["school_id"] = school_id
                if class_ids.get(school_conf["classes"][0]):
                    s["class_id"] = class_ids.get(school_conf["classes"][0])
                create_user(supabase, s)

        print("\n=== SEED COMPLETE ===")
        print()
        print("Login credentials:")
        print("  Super Admin   : superadmin@scan-grade.app / superadmin123")
        print("  Admin SMP     : admin_smp@scan-grade.app / smpadmin123")
        print("  Admin SMA     : admin_sma@scan-grade.app / smaadmin123")
        print("  Guru SMP      : guru1_smp@scan-grade.app / guru123")
        print("  Guru SMA      : guru1_sma@scan-grade.app / guru123")
        print("  Murid SMP     : murid1_smp@scan-grade.app / murid123")
        print("  Murid SMA     : murid1_sma@scan-grade.app / murid123")


if __name__ == "__main__":
    run()
