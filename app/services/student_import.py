"""Student CSV import — validation and batch import logic.

Usage:
    from app.services.student_import import import_students_from_csv, validate_csv
"""

import csv
import io
from app.services import account_creation
from app.utils.auth import get_supabase
from app.utils.helpers import row_or_none
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


def find_student_by_nisn(supabase, nisn):
    """Look up a NISN across the **whole** database, not one school.

    ``students.nisn`` is ``TEXT UNIQUE`` globally (migration 007), so a check
    scoped with ``.eq("school_id", ...)`` answers a different question than the
    constraint asks: a NISN already used at another school passes the scoped
    check and is only rejected later, by the index -- after the account has
    already been created.

    Returns the existing row, or ``None``.

    ``.limit(1)`` rather than ``.maybe_single()``: ``maybe_single`` raises when a
    query matches more than one row, and this runs before every student insert.
    """
    rows = (
        supabase.table("students").select("id, school_id")
        .eq("nisn", str(nisn).strip()).limit(1).execute().data or []
    )
    return rows[0] if rows else None


def create_student_account(supabase, *, school_id, nisn, full_name, email,
                           password, class_id=None, phone="", status="active"):
    """Create one student account: auth user -> profiles -> students.

    The order is forced by foreign keys (``students.id -> profiles.id ->
    auth.users.id``), and the last step is the one that can be rejected -- by the
    global ``students.nisn`` index. So the NISN is checked up front across the
    whole database, and if anything still fails the auth user is deleted, which
    cascades to its profile and student row.

    Without that rollback a failed row left an auth user plus a profile with no
    ``students`` row: an account that can sign in but appears in no class list.

    Raises ``ValidationError`` when the NISN is already taken, or when the posted
    class is not this school's (`owned_class_id`) — before anything is created.
    """
    nisn = str(nisn).strip()
    existing = find_student_by_nisn(supabase, nisn)
    if existing:
        where = "sekolah ini" if str(existing.get("school_id")) == str(school_id) \
            else "sekolah lain"
        raise ValidationError("nisn", f"NISN {nisn} sudah terdaftar di {where}")

    # The class is a foreign key that arrives in a form body, and it is the one
    # column that decides which school's rosters this pupil will appear on. It is
    # checked here, at the single writer, rather than at four doors: every caller
    # that mints a pupil account passes through this function, so a door added
    # later cannot forget the rule. The doors ask `owned_class_id` first anyway,
    # because a 422 naming the class is a better answer than an exception, but
    # what makes the row impossible is this line.
    class_id, class_error = owned_class_id(supabase, school_id, class_id)
    if class_error:
        raise ValidationError("class_id", class_error)

    profile_fields = {"nisn": nisn}
    if class_id:
        profile_fields["class_id"] = class_id

    return account_creation.create_account(
        supabase,
        school_id=school_id, role="murid", full_name=full_name,
        email=email, password=password, status=status, phone=phone,
        profile_fields=profile_fields,
        role_table="students",
        role_fields={"nisn": nisn, "class_id": class_id, "status": status},
        identifier=nisn,
    )


#: The refusals of the class check below, as sentences. Constants rather than
#: literals at the four doors, so the door that pre-checks and the writer that
#: guards cannot answer the same request two different ways.
CLASS_NOT_OURS = "Kelas yang dipilih bukan milik sekolah ini"
CLASS_UNVERIFIABLE = "Kelas tidak dapat diverifikasi untuk sekolah ini"


def owned_class_id(supabase, school_id, class_id):
    """``(class_id, error)`` — the posted class, if this school owns it.

    `class_id` arrives in a form body and is written straight onto both
    `profiles.class_id` and `students.class_id`, and every reader of a class roster
    filters by school **and** class (`teacher_assignments.students_in_classes`, the
    exam roster, the weighted grade table). A class id from another NPSN therefore
    produces a pupil who belongs to this school and to a class it does not own —
    precisely the row `cross_school_repair` reports as `class_pupil_mismatch` for an
    operator to untangle by hand.

    The actor with a reason to *have* another school's class ids is the teacher who
    works at two of them, which is the door this check stands on: `/students/import`
    is the guru's page, and none of the four doors that write this column — the guru's
    import, the JSON import, the admin's add and the admin's edit — offered the class
    it posted any ownership question at all.

    A refusal, never a silent drop: an import that answers "6 created" while filing
    six pupils with no class is the failure mode this file already refuses elsewhere
    (a row that exists and is wrong looks exactly like success from the page).

    An unreadable `classes` read refuses too. "Could not ask" is not "ours", and the
    write this guards is a row a school's rosters will then be built from.
    """
    wanted = str(class_id or "").strip()
    if not wanted:
        return None, None                       # no class asked for: nothing to check
    if not school_id:
        return None, CLASS_UNVERIFIABLE
    try:
        rows = (supabase.table("classes").select("id")
                .eq("id", wanted).eq("school_id", school_id)
                .limit(1).execute().data or [])
    except Exception:
        logger.debug("class ownership read failed; refusing the import", exc_info=True)
        return None, CLASS_UNVERIFIABLE
    if not rows:
        return None, CLASS_NOT_OURS
    return wanted, None


def import_students_from_csv(file_stream, school_id, class_id=None, batch_size=50):
    """Import students from CSV. Returns {success, failed, errors, total}.

    Raises ValidationError if CSV structure is invalid. A posted `class_id` that is
    not this school's is refused by `create_student_account`, so the rows carry the
    refusal instead of being written with a foreign class; the route asks
    `owned_class_id` before calling this, and answers 422 once for the file.
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

            # Resolve class_id from name if class_id not provided
            resolved_class_id = class_id
            if not resolved_class_id and kelas:
                c = row_or_none(
                    supabase.table("classes").select("id").eq("school_id", school_id)
                    .eq("name", kelas).maybe_single().execute()
                )
                if c:
                    resolved_class_id = c["id"]

            # The helper checks the NISN globally and rolls the account back if
            # any of its three writes fails, so a rejected row leaves nothing.
            create_student_account(
                supabase, school_id=school_id, nisn=nisn, full_name=nama,
                email=email or f"{nisn}@siswa.scan-grade.app",
                password=password, class_id=resolved_class_id,
            )
            results["success"] += 1

        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "row": idx,
                "nisn": row.get("nisn", "").strip(),
                "message": getattr(e, "user_message", str(e))[:120],
            })

    logger.info("CSV import: %d success, %d failed of %d", results["success"], results["failed"], results["total"])
    return results
