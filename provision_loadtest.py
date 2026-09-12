"""Provision a dedicated load-test school.

Creates one school, one class, N students through the app's REAL bulk student
import (app.services.student_import) and M teachers, so a load test can use one
distinct account per simulated user.

Notes:
* `email_confirm=True` in the import means **no activation emails are sent**.
* Emails are deterministic so a roster never has to rely on admin.list_users()
  (which is paginated and would silently truncate once >50 users exist):
      student -> {nisn}@siswa.scan-grade.app
      teacher -> lt-guru-NN@loadtest.scan-grade.app   (NN from employee_id LTGNN)
* Everything is named "ZZ LOADTEST" so it is obvious and easy to clean up.

Usage:
    python provision_loadtest.py                 # 300 students, 30 teachers
    python provision_loadtest.py 5 2             # smoke test
    python provision_loadtest.py --cleanup       # remove it all again
"""
import csv
import io
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from supabase import create_client

load_dotenv(".env")

from app.config import get_config  # noqa: E402

NPSN = "99990001"
SCHOOL_NAME = "ZZ LOADTEST - JANGAN DIPAKAI"
CLASS_NAME = "LT-KELAS-1"
PASSWORD = "LoadTest123!"
STUDENT_NISN_BASE = 900000000
TEACHER_EMAIL = "lt-guru-{:02d}@loadtest.scan-grade.app"
ROSTER_PATH = Path(__file__).resolve().parent / ".freebuff" / "lt_roster.json"


def write_roster(svc):
    """Dump email/name/id per account so the load test can verify identity.

    Names alone are not proof: the harness must confirm that a session receives
    the account it signed in with, which needs the real user id (compared
    against /auth/me).
    """
    school_id = _find_school(svc)
    if not school_id:
        print("no load-test school — nothing to write")
        return

    teachers = {
        t["id"]: t.get("employee_id", "")
        for t in svc.table("teachers").select("id, employee_id").eq("school_id", school_id).execute().data
    }
    rows = svc.table("profiles").select("id, full_name, role, nisn").eq(
        "school_id", school_id
    ).execute().data

    out = []
    for r in rows:
        role = r.get("role")
        email = None
        if role == "murid" and r.get("nisn"):
            email = f"{r['nisn']}@siswa.scan-grade.app"
        elif role == "guru":
            emp = teachers.get(r["id"], "")
            if emp.upper().startswith("LTG") and emp[3:].isdigit():
                email = TEACHER_EMAIL.format(int(emp[3:]))
        if email:
            out.append({"email": email, "name": r.get("full_name", ""),
                        "id": r["id"], "role": role})

    ROSTER_PATH.parent.mkdir(exist_ok=True)
    ROSTER_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    murid = sum(1 for a in out if a["role"] == "murid")
    guru = sum(1 for a in out if a["role"] == "guru")
    print(f"roster written: {ROSTER_PATH} ({murid} murid, {guru} guru)")


def _minimal_app(cfg):
    """Just enough app context for get_supabase() — no schedulers, no Redis."""
    app = Flask(__name__)
    app.extensions["supabase"] = create_client(cfg.SUPABASE_URL, cfg.SUPABASE_SERVICE_KEY)
    return app


def _find_school(svc):
    rows = svc.table("schools").select("id").eq("npsn", NPSN).execute().data
    return rows[0]["id"] if rows else None


def cleanup(svc):
    school_id = _find_school(svc)
    if not school_id:
        print("no load-test school found; nothing to clean up")
        return
    profiles = svc.table("profiles").select("id").eq("school_id", school_id).execute().data
    ids = [p["id"] for p in profiles]
    print(f"deleting {len(ids)} profiles/auth users ...")
    for t in ("students", "teachers", "profiles"):
        try:
            svc.table(t).delete().eq("school_id", school_id).execute()
        except Exception as e:
            print(f"  {t}: {str(e)[:90]}")
    ok = 0
    for uid in ids:
        try:
            svc.auth.admin.delete_user(uid)
            ok += 1
        except Exception:
            pass
    try:
        svc.table("classes").delete().eq("school_id", school_id).execute()
        svc.table("schools").delete().eq("id", school_id).execute()
    except Exception as e:
        print("  school/class delete:", str(e)[:90])
    print(f"deleted {ok}/{len(ids)} auth users; school removed")


def provision(n_students, n_teachers):
    from app.services.student_import import import_students_from_csv

    cfg = get_config()
    app = _minimal_app(cfg)
    svc = app.extensions["supabase"]

    school_id = _find_school(svc)
    if school_id:
        print("reusing existing load-test school:", school_id)
    else:
        school_id = svc.table("schools").insert({
            "name": SCHOOL_NAME, "npsn": NPSN, "status": "active", "tz_offset": 7,
        }).execute().data[0]["id"]
        print("created school:", school_id)

    cls = svc.table("classes").select("id").eq("school_id", school_id).eq("name", CLASS_NAME).execute().data
    if cls:
        class_id = cls[0]["id"]
    else:
        class_id = svc.table("classes").insert({
            "name": CLASS_NAME, "school_id": school_id, "grade_level": "7",
        }).execute().data[0]["id"]
    print("class:", class_id)

    # ── students via the real bulk import ──
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["nama", "nisn", "kelas", "password"])
    for i in range(1, n_students + 1):
        w.writerow([f"LT Murid {i:03d}", str(STUDENT_NISN_BASE + i), CLASS_NAME, PASSWORD])

    with app.app_context():
        res = import_students_from_csv(io.BytesIO(buf.getvalue().encode()), school_id, class_id)
    print(f"students: {res['success']} created, {res['failed']} failed of {res['total']}")
    for e in res["errors"][:5]:
        print("   err:", e)

    # ── teachers ──
    made = 0
    for i in range(1, n_teachers + 1):
        email = TEACHER_EMAIL.format(i)
        try:
            uid = svc.auth.admin.create_user({
                "email": email, "password": PASSWORD, "email_confirm": True,
                "user_metadata": {"role": "guru", "full_name": f"LT Guru {i:02d}"},
            }).user.id
        except Exception:
            uid = None
        if not uid:
            continue
        svc.table("profiles").upsert({
            "id": uid, "full_name": f"LT Guru {i:02d}", "role": "guru",
            "school_id": school_id, "status": "active",
        }).execute()
        svc.table("teachers").upsert({
            "id": uid, "school_id": school_id, "employee_id": f"LTG{i:02d}",
        }).execute()
        made += 1
    print(f"teachers: {made} created")

    write_roster(svc)
    print("\nLOAD TEST ROSTER")
    print(f"  LT_SCHOOL_ID={school_id}")
    print(f"  LT_PASSWORD={PASSWORD}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--cleanup" in args:
        cfg = get_config()
        cleanup(_minimal_app(cfg).extensions["supabase"])
    elif "--roster" in args:
        cfg = get_config()
        write_roster(_minimal_app(cfg).extensions["supabase"])
    else:
        nums = [int(a) for a in args if a.isdigit()]
        provision(nums[0] if nums else 300, nums[1] if len(nums) > 1 else 30)
