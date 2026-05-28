"""Seed data generator for ScanGrade development.
Run: python scripts/seed_data.py
Creates dummy teachers, students, exams, and submissions.
"""
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client, Client

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY"),
)

DATA = {
    "teachers": [
        {"email": "budi@guru.com", "password": "guru123", "full_name": "Budi Santoso", "phone": "081234567890"},
        {"email": "siti@guru.com", "password": "guru123", "full_name": "Siti Rahmawati", "phone": "081234567891"},
        {"email": "agus@guru.com", "password": "guru123", "full_name": "Agus Wijaya", "phone": "081234567892"},
    ],
    "students": [
        {"email": "siswa1@mail.com", "password": "siswa123", "full_name": "Ahmad Fauzi"},
        {"email": "siswa2@mail.com", "password": "siswa123", "full_name": "Dewi Sartika"},
        {"email": "siswa3@mail.com", "password": "siswa123", "full_name": "Rudi Hartono"},
        {"email": "siswa4@mail.com", "password": "siswa123", "full_name": "Nita Permata"},
        {"email": "siswa5@mail.com", "password": "siswa123", "full_name": "Doni Prasetyo"},
        {"email": "siswa6@mail.com", "password": "siswa123", "full_name": "Rina Marlina"},
        {"email": "siswa7@mail.com", "password": "siswa123", "full_name": "Eko Susanto"},
        {"email": "siswa8@mail.com", "password": "siswa123", "full_name": "Sari Dewi"},
        {"email": "siswa9@mail.com", "password": "siswa123", "full_name": "Adi Nugroho"},
        {"email": "siswa10@mail.com", "password": "siswa123", "full_name": "Maya Anggraini"},
    ],
    "exams": [
        {
            "title": "UTS Matematika Kelas X",
            "subject": "Matematika",
            "description": "Ujian Tengah Semester Matematika - Bilangan dan Aljabar",
            "duration_minutes": 120,
            "total_questions": 10,
            "passing_score": 70,
            "answer_key": {str(i): chr(65 + (i % 5)) for i in range(10)},
        },
        {
            "title": "UH Fisika - Gerak Lurus",
            "subject": "Fisika",
            "description": "Ulangan Harian Fisika bab Gerak Lurus",
            "duration_minutes": 60,
            "total_questions": 10,
            "passing_score": 65,
            "answer_key": {str(i): chr(65 + ((i * 2) % 5)) for i in range(10)},
        },
        {
            "title": "Tryout Bahasa Inggris",
            "subject": "Bahasa Inggris",
            "description": "Tryout persiapan ujian nasional Bahasa Inggris",
            "duration_minutes": 90,
            "total_questions": 10,
            "passing_score": 60,
            "answer_key": {str(i): chr(65 + ((i + 3) % 5)) for i in range(10)},
        },
    ],
}


def create_user(email, password, full_name, role, phone=None):
    try:
        res = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "user_metadata": {"role": role, "full_name": full_name},
            "email_confirm": True,
        })
        user_id = res.user.id
    except Exception as e:
        print(f"  SKIP create {email}: {e}")
        # User already exists — look up their ID
        try:
            users = supabase.auth.admin.list_users()
            for u in users:
                if u.email == email:
                    user_id = u.id
                    break
            else:
                print(f"  ERROR: {email} not found in auth.users")
                return None
        except Exception as e2:
            print(f"  ERROR looking up {email}: {e2}")
            return None

    try:
        supabase.table("profiles").upsert({
            "id": user_id,
            "full_name": full_name,
            "phone": phone or "",
            "role": role,
        }).execute()
        print(f"  Synced {role}: {email} ({full_name}) -> {user_id[:8]}...")
        return user_id
    except Exception as e:
        print(f"  ERROR profile {email}: {e}")
        return None


def create_exam(teacher_id, data):
    exam_data = {
        **data,
        "teacher_id": teacher_id,
        "status": "active",
    }
    res = supabase.table("exams").insert(exam_data).execute()
    exam_id = res.data[0]["id"]
    print(f"  Created exam: {data['title']} -> {exam_id[:8]}...")
    return exam_id


def create_submission(exam, exam_id, student_id, wrong_count):
    answers = {}
    for i in range(exam["total_questions"]):
        correct = exam["answer_key"][str(i)]
        if i < wrong_count:
            wrong_opts = [o for o in ["A","B","C","D","E"] if o != correct]
            answers[str(i)] = wrong_opts[i % len(wrong_opts)]
        else:
            answers[str(i)] = correct

    correct_count = exam["total_questions"] - wrong_count
    score = round((correct_count / exam["total_questions"]) * 100, 2)
    penalty = wrong_count * 2 if wrong_count > 2 else 0
    final_score = max(0, score - penalty)

    submission_data = {
        "exam_id": exam_id,
        "student_id": student_id,
        "answers": answers,
        "score": score,
        "max_score": 100,
        "violations": wrong_count // 2,
        "penalty": penalty,
        "final_score": final_score,
        "status": "graded",
        "is_published": True,
    }
    supabase.table("submissions").insert(submission_data).execute()
    print(f"    Submission: {student_id[:8]}... -> score={score}, penalty={penalty}")


def main():
    print("=== ScanGrade Seed Data ===\n")

    # Create teachers
    teacher_ids = []
    for t in DATA["teachers"]:
        uid = create_user(t["email"], t["password"], t["full_name"], "teacher", t.get("phone"))
        if uid:
            teacher_ids.append(uid)

    # Create students
    student_ids = []
    for s in DATA["students"]:
        uid = create_user(s["email"], s["password"], s["full_name"], "student")
        if uid:
            student_ids.append(uid)

    if not teacher_ids or not student_ids:
        print("\nNo users created — aborting.")
        return

    print("\nCreating exams...")
    exam_ids = []
    for i, e in enumerate(DATA["exams"]):
        tid = teacher_ids[i % len(teacher_ids)]
        eid = create_exam(tid, e)
        if eid:
            exam_ids.append((e, eid))

    print("\nCreating submissions...")
    for exam, exam_id in exam_ids:
        print(f"  Exam: {exam['title']}")
        for j, sid in enumerate(student_ids):
            wrong_count = (j * 2) % (exam["total_questions"] + 1)
            create_submission(exam, exam_id, sid, wrong_count)

    print("\n=== Done! ===")
    print(f"Teachers: {len(teacher_ids)}, Students: {len(student_ids)}")
    print(f"Exams: {len(exam_ids)}, Submissions: {len(student_ids) * len(exam_ids)}")
    print("\nLogin credentials:")
    print("  Teacher: budi@guru.com / guru123")
    print("  Student: siswa1@mail.com / siswa123")


if __name__ == "__main__":
    main()
