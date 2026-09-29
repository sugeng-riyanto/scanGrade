"""Login cards — the only way a school can obtain the credentials it created.

Why issuing a card means *setting* a password
---------------------------------------------
Nothing in this app can read a password back. ``profiles``, ``students`` and
``teachers`` carry no password column; the credential lives in Supabase Auth as a
hash. And every path that creates an account generates one and drops it:

* ``_import_students`` / ``_import_teachers`` call ``_gen_password()`` per row and
  keep only ``results["students"] += 1`` — measured on the running box, importing a
  class of pupils creates accounts whose passwords are never shown to anyone;
* ``bulk_reset_students_password`` / ``bulk_reset_teachers_password`` **do** return
  the new password for each row, and the pages print ``N passwords direset`` and
  throw the rest away.

So a school could create two hundred accounts and not be able to hand a single one
to a pupil. A card therefore has to be *issued*: the password is generated, written
to the account, and carried into the file in the same request. If it is not written
here it does not exist anywhere.

What one card carries
---------------------
The **login identity** is not the email for either role — the pupil/teacher door
(``LOGIN_URL_USER`` in :mod:`app.utils.auth`, which the sheet also prints) finds a
pupil by ``students.nisn`` and a teacher by ``teachers.employee_id`` (the page calls
it "Nomor Pegawai"). A card that printed only the email would look right and not
work, so the identity is read from the same column the login route matches on.

Scoping, and why a bad id is refused rather than skipped
--------------------------------------------------------
These ids arrive in a request body, so each one is checked against
``profiles.school_id`` — the same per-id guard the bulk resets already make, because
a bulk route has no id in its path for ``@require_school_access`` to read. An id
belonging to another school is **refused** (404) instead of quietly dropped: a sheet
that is missing three pupils, with nothing saying why, is a sheet a school hands out
and then answers for.
"""

from __future__ import annotations

import csv
import io
import secrets
import string
from datetime import datetime, timezone

# The door, not a copy of it: `tests/unit/test_login_door.py` keeps the pupil/teacher
# login URL spelled in exactly one place, and a sheet that printed its own string
# would be a second copy to keep in sync.
from app.utils.auth import LOGIN_URL_USER
from app.services.school_officials import OFFICIAL_ROLES

#: ``kind -> everything that differs between a pupil's card and a teacher's``.
KINDS = {
    "student": {
        "table": "students",
        "identity_column": "nisn",
        "identity_label": ("NISN", "NISN"),
        "group_embed": "classes(name)",
        "group_label": ("Kelas", "Class"),
        "title": ("Kartu Login Murid", "Student Login Cards"),
        "filename": "kartu-login-murid",
        "role": "murid",
    },
    "teacher": {
        "table": "teachers",
        "identity_column": "employee_id",
        "identity_label": ("Nomor Pegawai", "Employee No."),
        "group_embed": "subjects(name)",
        "group_label": ("Mapel", "Subject"),
        "title": ("Kartu Login Guru", "Teacher Login Cards"),
        "filename": "kartu-login-guru",
        "role": "guru",
    },
    # The officials have no table of their own, and that was a decision (see
    # app/services/school_officials.py): the role lives in `profiles.role`. So this is
    # the one kind whose rows are read from `profiles` directly, and the one whose
    # identity is the **email** — `/auth/login-user` has no NISN or NIP to match for
    # these two roles, so the address typed by the school is what they sign in with.
    # A card that printed an "identity" they cannot use would look right and not work.
    "official": {
        "table": None,
        "identity_column": None,
        "identity_label": ("Email login", "Login email"),
        "group_embed": None,
        "group_label": ("Peran", "Role"),
        "title": ("Kartu Login Pejabat Sekolah", "School Official Login Cards"),
        "filename": "kartu-login-pejabat",
    },
}


#: ``kind -> the log/audit label``: what the export is recorded as.
def audit_label(kind: str) -> str:
    return "official" if kind == "official" else kind


#: The words the sheet uses for the two official roles, in both languages.
OFFICIAL_ROLE_LABELS = {
    "principal": ("Kepala Sekolah", "Principal"),
    "vice_principal": ("Wakil Kepala Sekolah", "Vice Principal"),
}

#: The columns of the sheet, in order. Named once so the CSV and the XLSX cannot
#: disagree about what a column is.
COLUMNS = (
    ("no", ("No", "No")),
    ("name", ("Nama", "Name")),
    ("identity", ("Identitas", "Identity")),
    ("group", ("Kelas / Mapel", "Class / Subject")),
    ("email", ("Email", "Email")),
    ("password", ("Password", "Password")),
    ("error", ("Catatan", "Note")),
)


def new_password(length: int = 12) -> str:
    """The password shape this app already hands out (letters, digits, symbols)."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


def _group_name(row: dict, kind: str) -> str:
    if kind == "official":
        return OFFICIAL_ROLE_LABELS.get(row.get("role"), ("", ""))[0]
    embedded = None
    if kind == "student":
        embedded = row.get("classes") or {}
    else:
        embedded = row.get("subjects") or {}
    return (embedded or {}).get("name", "") or ""


def _official_rows(supabase, school_id, ids) -> list[dict]:
    """This school's official accounts, in the shape the other kinds return.

    Scoped and role-checked in the query, not after it: the sheet is the one artefact
    that leaves the building, and an id belonging to a pupil (or to another school's
    head teacher) must not be able to turn into a card. ``email`` comes from the
    mirror column migration 040 adds, which is exactly the read it exists for —
    ``auth.admin.list_users()`` is paged at 50 accounts, so reading the address per
    row from Auth would be dozens of round-trips for one school.
    """
    rows = (supabase.table("profiles")
            .select("id, full_name, role, school_id, email")
            .in_("id", ids)
            .eq("school_id", school_id)
            .in_("role", list(OFFICIAL_ROLES))
            .execute().data) or []
    out = []
    for row in rows:
        address = (row.get("email") or "").strip()
        out.append({
            "id": str(row.get("id")),
            "name": row.get("full_name") or "-",
            "identity": address,
            "group": _group_name(row, "official"),
            "email": address,
            "password": "",
            "error": "",
        })
    return out


def collect(supabase, school_id, user_ids, kind, emails=None) -> dict:
    """Read the accounts named by ``user_ids``, scoped to ``school_id``.

    Returns ``{"rows": [...], "missing": [...]}`` — ``missing`` are the ids that are
    not this school's, which the caller turns into a refusal rather than a shorter
    sheet. One query for the rows; the passwords are set one account at a time
    afterwards because that is the only shape the auth API offers.
    """
    conf = KINDS[kind]
    ids = [str(u) for u in (user_ids or []) if u]
    if not ids:
        return {"rows": [], "missing": []}

    if conf.get("table") is None:
        out = _official_rows(supabase, school_id, ids)
        found = {r["id"] for r in out}
        return {"rows": out, "missing": [i for i in ids if i not in found]}

    rows = (
        supabase.table(conf["table"])
        .select(f"id, {conf['identity_column']}, profiles!inner(id, full_name, school_id), "
                f"{conf['group_embed']}")
        .in_("id", ids)
        .eq("school_id", school_id)
        .execute()
        .data or []
    )
    found = {str(r.get("id")) for r in rows}
    out = []
    for row in rows:
        profile = row.get("profiles") or {}
        out.append({
            "id": str(row.get("id")),
            "name": profile.get("full_name") or "-",
            "identity": str(row.get(conf["identity_column"]) or ""),
            "group": _group_name(row, kind),
            "email": (emails or {}).get(str(row.get("id")), ""),
            "password": "",
            "error": "",
        })
    return {"rows": out, "missing": [i for i in ids if i not in found]}


def set_passwords(supabase, rows, password_factory=None) -> list[dict]:
    """Write a fresh password to each row's account and record it on the row.

    Deliberately a **second step**, never folded into :func:`collect`. The order is
    the whole point: a request naming one account from another school must be refused
    *before* any password is written, because a password that is set and then not
    handed over does not exist anywhere — the sheet was never produced, and the
    account's old password is gone. (The first version of this module did both in one
    function, and the guard suite caught it: the foreign id was refused after the
    legitimate accounts had already been reset.)

    A reset that fails is carried in the row's ``error`` instead of raising: the
    operator asked for a sheet, and "these three could not be reset" belongs *in* the
    sheet rather than in a 500 that loses the other ninety-seven.
    """
    make = password_factory or new_password
    for row in rows:
        password = make()
        try:
            supabase.auth.admin.update_user_by_id(row["id"], {"password": password})
            row["password"] = password
        except Exception as exc:  # noqa: BLE001 — the note is the report
            row["error"] = f"password tidak dapat direset: {str(exc)[:60]}"
            continue
        # Issuing a card is the moment a password becomes a *one-time* one, so the
        # flag is set here rather than at the caller — there is exactly one place that
        # writes a password to an account, and this is the side of it that knows the
        # credential is now on a piece of paper in a school bag. Best-effort but
        # reported: the password is real and stays on the sheet either way, so a
        # failure here is a note in the row, never a reason to drop the password.
        try:
            supabase.table("profiles").update({"must_change_password": True}) \
                .eq("id", row["id"]).execute()
        except Exception as exc:  # noqa: BLE001
            row["error"] = (f"password terpasang, tetapi penanda ganti-password gagal "
                            f"disetel: {str(exc)[:60]}")
    return rows


def _cell_value(row: dict, key: str, index: int):
    if key == "no":
        return index
    return row.get(key, "")


def to_csv(cards: list[dict], kind: str, meta: dict | None = None) -> bytes:
    """The sheet as CSV, with a BOM so Excel reads an accented name as UTF-8."""
    conf = KINDS[kind]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([conf["title"][0]])
    for key, value in (meta or {}).items():
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow([label[0] for _key, label in COLUMNS])
    for index, row in enumerate(cards, 1):
        writer.writerow([_cell_value(row, key, index) for key, _label in COLUMNS])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def to_xlsx(cards: list[dict], kind: str, meta: dict | None = None) -> bytes:
    """The sheet as XLSX — the format the import templates already use here."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    conf = KINDS[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = conf["title"][0][:31]
    ws.append([conf["title"][0]])
    ws["A1"].font = Font(bold=True, size=13)
    for key, value in (meta or {}).items():
        ws.append([key, value])
    ws.append([])
    ws.append([label[0] for _key, label in COLUMNS])
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = Font(bold=True)
    for index, row in enumerate(cards, 1):
        ws.append([_cell_value(row, key, index) for key, _label in COLUMNS])
    widths = {"no": 6, "name": 28, "identity": 18, "group": 20, "email": 30,
              "password": 18, "error": 40}
    for position, (key, _label) in enumerate(COLUMNS, 1):
        ws.column_dimensions[ws.cell(row=header_row, column=position).column_letter].width = \
            widths.get(key, 18)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def meta_for(school_name: str, requested: int, issued: int, failed: int,
             lang: str = "id") -> dict:
    """The lines above the table: what this file is, and when it was made."""
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    if lang == "en":
        return {
            "School": school_name, "Made": stamp,
            "Accounts requested": str(requested), "Passwords issued": str(issued),
            "Could not be reset": str(failed),
            "Login page": LOGIN_URL_USER,            "Note": ("Passwords are shown only in this file — nothing on the site can "
                     "read them back. Each one was set on the account just now."),
            "First sign-in": ("This is a one-time password: the account is asked to "
                              "choose its own before any other page opens."),
    }
    return {
        "Sekolah": school_name, "Dibuat": stamp,
        "Akun diminta": str(requested), "Password diterbitkan": str(issued),
        "Gagal direset": str(failed),
        "Halaman login": LOGIN_URL_USER,
        "Catatan": ("Password hanya ada di berkas ini — tidak ada halaman yang bisa "
                    "membacanya kembali. Setiap password baru saja dipasang ke akunnya."),
        "Login pertama": ("Ini password sekali pakai: akunnya diminta membuat password "
                          "sendiri sebelum halaman lain terbuka."),
    }


def filename(kind: str, when=None) -> str:
    conf = KINDS[kind]
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")
    return f"{conf['filename']}-{stamp}"


MIMETYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def render(cards, kind, meta, fmt: str) -> tuple[bytes, str, str]:
    """``(payload, mimetype, filename)`` for the requested format.

    An unknown format is XLSX rather than an error: the format is chosen by a button
    on the page, and a 400 for a typo in a hidden field would cost the operator the
    passwords that were just set.
    """
    fmt = (fmt or "xlsx").strip().lower()
    if fmt not in ("csv", "xlsx"):
        fmt = "xlsx"
    payload = to_csv(cards, kind, meta) if fmt == "csv" else to_xlsx(cards, kind, meta)
    return payload, MIMETYPES[fmt], f"{filename(kind)}.{fmt}"
