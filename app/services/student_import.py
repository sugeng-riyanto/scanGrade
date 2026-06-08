"""Student CSV import — validation and batch import logic.

Usage:
    from app.services.student_import import import_students_from_csv, validate_csv
"""

import csv
import io
from app.utils.auth import get_supabase
from app.utils.logger import get_logger
from app.errors import ValidationError

logger = get_logger("student_import")

REQUIRED_COLUMNS = ["nama", "nisn"]
OPTIONAL_COLUMNS = ["email", "kelas", "password"]


def validate_csv(file_stream):
    """Validate CSV structure and data. Returns (errors_list, headers).
    Each error: {"row": int, "field": str, "message": str}
    """
    errors = []
    content = file_stream.read().decode("utf-8-sig")
    file_stream.seek(0)
    reader = csv.DictReader(io.StringIO(content))

    if not reader.fieldnames:
        return [{"row": 0, "field": "file", "message": "CSV tidak memiliki header"}], []

    headers = [h.strip().lower() for h in reader.fieldnames]

    for col in REQUIRED_COLUMNS:
        if col not in headers:
            errors.append({"row": 0, "field": col, "message": f"Kolom wajib '{col}' tidak ditemukan"})

    if errors:
        return errors, headers

    seen_nisn = {}
    for idx, row in enumerate(reader, 2):
        nama = row.get("nama", "").strip()
        nisn = row.get("nisn", "").strip()

        if not nama:
            errors.append({"row": idx, "field": "nama", "message": "Nama tidak boleh kosong"})
        if not nisn:
            errors.append({"row": idx, "field": "nisn", "message": "NISN tidak boleh kosong"})
        elif not nisn.isdigit() or len(nisn) < 8:
            errors.append({"row": idx, "field": "nisn", "message": "NISN harus 8-12 digit angka"})
        elif nisn in seen_nisn:
            errors.append({"row": idx, "field": "nisn", "message": f"NISN duplikat (baris {seen_nisn[nisn]})"})
        else:
            seen_nisn[nisn] = idx

        email = row.get("email", "").strip()
        if email and "@" not in email:
            errors.append({"row": idx, "field": "email", "message": "Format email tidak valid"})

    return errors, headers


def import_students_from_csv(file_stream, school_id, class_id=None, batch_size=50):
    """Import students from CSV. Returns {success, failed, errors, total}.
    Raises ValidationError if CSV structure is invalid.
    """
    content = file_stream.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]

    for col in REQUIRED_COLUMNS:
        if col not in headers:
            raise ValidationError("csv", f"Kolom '{col}' tidak ditemukan di CSV")

    supabase = get_supabase()
    results = {"success": 0, "failed": 0, "errors": [], "total": 0}
    rows = list(reader)
    results["total"] = len(rows)

    for idx, row in enumerate(rows, 2):
        try:
            nama = row.get("nama", "").strip()
            nisn = row.get("nisn", "").strip()
            if not nama or not nisn:
                results["failed"] += 1
                continue

            email = row.get("email", "").strip() or None
            kelas = row.get("kelas", "").strip()
            password = row.get("password", "").strip() or "siswa123"

            # Duplicate NISN check
            existing = supabase.table("students").select("id").eq("nisn", nisn).eq("school_id", school_id).maybe_single().execute()
            if existing.data:
                results["failed"] += 1
                results["errors"].append({"row": idx, "nisn": nisn, "message": "NISN sudah terdaftar"})
                continue

            # Resolve class_id from name if class_id not provided
            resolved_class_id = class_id
            if not resolved_class_id and kelas:
                c = supabase.table("classes").select("id").eq("school_id", school_id).eq("name", kelas).maybe_single().execute()
                if c.data:
                    resolved_class_id = c.data["id"]

            # Create auth user
            user_email = email or f"{nisn}@siswa.scan-grade.app"
            supabase.auth.admin.create_user({
                "email": user_email,
                "password": password,
                "user_metadata": {"role": "murid", "full_name": nama},
                "email_confirm": True,
            })

            # Fetch the created user to get ID
            # (create_user returns the user, but we use a direct approach)
            created = supabase.auth.admin.create_user({
                "email": user_email,
                "password": password,
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
            results["errors"].append({"row": idx, "nisn": row.get("nisn", "").strip(), "message": str(e)[:100]})

    logger.info("CSV import: %d success, %d failed of %d", results["success"], results["failed"], results["total"])
    return results
