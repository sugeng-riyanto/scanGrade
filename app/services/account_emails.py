"""Attach or repair the email on accounts that already exist — and never create one.

Why this is needed at all
-------------------------
Every account in this app is created with an email, and for a pupil or a teacher that
address is **generated from their name** (``_generate_email``: ``budi.santoso@…``)
unless the import sheet happened to carry one. A school that imported a class before it
had addresses for anyone therefore owns two hundred accounts whose email nobody reads —
and the email is not cosmetic: it is where ``/auth/forgot-password`` sends the reset
code, and it is one of the two things the teacher/student door accepts as a login.
There has
never been a way to fix that in bulk, so the only repair was per account, through the
Supabase dashboard, which is not a thing a school admin can be asked to do.

What one row is
---------------
``peran`` names the role, ``identitas`` names the account **the way that role is found**,
and ``email`` is what it becomes:

============  =====================  ===========================================
peran          identitas              why that is the identifier
============  =====================  ===========================================
murid          NISN                   ``students.nisn`` is what the login matches
guru           NIP                    ``teachers.employee_id`` likewise
kepala         nama lengkap           these two roles have no table and no number —
wakil kepala   nama lengkap           ``profiles.role`` is all there is
============  =====================  ===========================================

Naming an official by name is the one soft spot, and it is handled by refusing rather
than guessing: a name that matches two accounts (or none) is reported as such, because
quietly picking one of two head teachers would move a credential to the wrong person.

It never creates anything
-------------------------
``plan`` resolves each row against accounts that exist. A row naming an account that is
not found is an *error in the report*, never an invitation to make one: this file's whole
purpose is repairing addresses, and a typo in a NISN would otherwise mint an account with
a password nobody has ever seen.

The write is two writes, and the order matters
----------------------------------------------
Supabase Auth holds the credential; ``profiles.email`` is the derived mirror migration
040 adds so a school's own sheet can be built in one query instead of paging
``auth.admin.list_users()`` at 50 accounts a page. Auth is written **first**: if the
mirror then fails, the account still works and the mirror is stale (visible, and fixable
by the next upload); the other order would leave the sheet promising an address that the
account cannot sign in with.
"""

from __future__ import annotations

import csv
import io
import logging
import re

logger = logging.getLogger(__name__)

#: Every spelling a school may reasonably type for the four roles, mapped to the role
#: the database uses. Indonesian first, because that is what the sheet is written in.
ROLE_WORDS = {
    "murid": "murid", "siswa": "murid", "student": "murid", "murid/siswa": "murid",
    "guru": "guru", "teacher": "guru", "guru/mapel": "guru",
    "kepala": "principal", "kepala sekolah": "principal", "principal": "principal",
    "wakil": "vice_principal", "wakil kepala": "vice_principal",
    "wakil kepala sekolah": "vice_principal", "vice principal": "vice_principal",
    "vice_principal": "vice_principal",
}

#: What the sheet calls the account for each role, in both languages — printed in the
#: report so a refusal says *which* identifier it could not find.
IDENTITY_LABEL = {
    "murid": ("NISN", "NISN"),
    "guru": ("NIP", "NIP"),
    "principal": ("Nama lengkap", "Full name"),
    "vice_principal": ("Nama lengkap", "Full name"),
}

#: The column headers accepted, per language. Lowercased on comparison.
HEADER_WORDS = {
    "peran": "role", "role": "role",
    "identitas": "identity", "nisn": "identity", "nip": "identity",
    "nama": "identity", "nama lengkap": "identity", "name": "identity",
    "email": "email", "e-mail": "email", "alamat email": "email",
}

#: Deliberately structural, not RFC-complete: this app has to reject a typo, and the
#: only authority on whether an address exists is the mail server. Anything with a
#: local part, one ``@``, a dot in the domain and a plausible TLD passes.
EMAIL_SHAPE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")

COLUMNS = ("role", "identity", "email")


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _header_key(raw: str) -> str | None:
    """The canonical column a header cell names, or ``None``.

    A school writes ``Identitas (NISN/NIP/Nama)`` or ``Email (wajib)``: the
    parenthetical is a hint to the person filling the sheet, not a second column, so it
    is stripped before the lookup. Without this the sheet this module *generates* could
    not be read back by its own parser.
    """
    for candidate in (raw, raw.split("(")[0].strip()):
        key = HEADER_WORDS.get(candidate)
        if key:
            return key
    return None


def read_rows(file_stream, filename: str = "") -> list[dict]:
    """``[{row, role, identity, email}]`` from an uploaded .xlsx or .csv.

    The header row is mapped by name rather than by position, so a school may order the
    columns however it likes and a sheet exported from Excel with an extra column still
    reads. A file whose header has no recognisable ``email`` column is refused outright:
    silently reading such a file would report every row as "no email given".
    """
    name = (filename or "").lower()
    rows: list[list] = []
    if name.endswith(".csv"):
        text = file_stream.read().decode("utf-8-sig", errors="replace")
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
    else:
        from openpyxl import load_workbook
        wb = load_workbook(filename=io.BytesIO(file_stream.read()),
                           read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]

    if not rows:
        raise ValueError("empty sheet")

    header = {_text(cell).lower(): index for index, cell in enumerate(rows[0]) if _text(cell)}
    mapping = {}
    for raw, index in header.items():
        key = _header_key(raw)
        if key and key not in mapping:
            mapping[key] = index
    if "email" not in mapping or "identity" not in mapping:
        raise ValueError("header must name an identity column and an email column")

    out = []
    for offset, row in enumerate(rows[1:], start=2):
        def cell(key):
            index = mapping.get(key)
            return _text(row[index]) if index is not None and index < len(row) else ""
        role_word = cell("role").lower()
        out.append({
            "row": offset,
            "role": ROLE_WORDS.get(role_word, role_word),
            "identity": cell("identity"),
            "email": cell("email").lower(),
        })
    return [r for r in out if r["identity"] or r["email"]]


def _resolve_many(supabase, school_id: str, role: str, identities: list[str]) -> dict:
    """``identity -> [account]`` for one role, read in as few queries as possible.

    One query per role per page rather than one per row: a school of 700 pupils would
    otherwise pay 700 round-trips for one upload, on a box where a round-trip is
    ~150 ms. Officials are the exception — they are matched on ``full_name``, and a name
    is not unique, so every candidate is kept and the caller refuses an ambiguous row.
    """
    if not identities:
        return {}
    found: dict[str, list[dict]] = {}
    try:
        if role == "murid":
            rows = (supabase.table("students")
                    .select("id, nisn, profiles!inner(id, full_name, school_id, email)")
                    .in_("nisn", identities).eq("school_id", school_id)
                    .execute().data) or []
            for row in rows:
                profile = row.get("profiles") or {}
                found.setdefault(_text(row.get("nisn")), []).append(
                    {"id": str(row.get("id")), "email": _text(profile.get("email"))})
        elif role == "guru":
            rows = (supabase.table("teachers")
                    .select("id, employee_id, profiles!inner(id, full_name, school_id, email)")
                    .in_("employee_id", identities).eq("school_id", school_id)
                    .execute().data) or []
            for row in rows:
                profile = row.get("profiles") or {}
                found.setdefault(_text(row.get("employee_id")), []).append(
                    {"id": str(row.get("id")), "email": _text(profile.get("email"))})
        else:
            rows = (supabase.table("profiles")
                    .select("id, full_name, role, school_id, email")
                    .eq("role", role).eq("school_id", school_id)
                    .in_("full_name", identities)
                    .execute().data) or []
            for row in rows:
                found.setdefault(_text(row.get("full_name")), []).append(
                    {"id": str(row.get("id")), "email": _text(row.get("email"))})
    except Exception as exc:                                      # noqa: BLE001
        logger.warning("email upload: could not read %s rows: %s", role, exc)
        return {}
    return found


def plan(supabase, school_id: str, rows: list[dict]) -> list[dict]:
    """Decide per row what would happen, without writing anything.

    The whole upload is planned before a single address is written, so a sheet with a
    duplicate address in it (two pupils typed into one mailbox) is reported as a whole
    rather than half-applied to the accounts that came first.
    """
    # The sheet's own words are canonicalised here, not only in `read_rows`. `plan` is a
    # public entry point, and a caller that hands it raw rows (`kepala`) would otherwise
    # be told "unknown role" for a role this very module teaches the parser to accept.
    rows = [dict(row, role=ROLE_WORDS.get(_text(row["role"]).lower(), row["role"]))
            for row in rows]

    wanted: dict[str, list[str]] = {}
    for row in rows:
        if row["identity"]:
            wanted.setdefault(row["role"], []).append(row["identity"])
    cache = {role: _resolve_many(supabase, school_id, role, identities)
             for role, identities in wanted.items()}

    seen_emails: dict[str, int] = {}
    planned = []
    for row in rows:
        role, identity, address = row["role"], row["identity"], row["email"]
        outcome = {"row": row["row"], "role": role, "identity": identity,
                   "email": address, "status": "", "user_id": None, "current": "",
                   "reason": ""}

        if not role or role not in IDENTITY_LABEL:
            outcome.update(status="error", reason="peran tidak dikenal")
            planned.append(outcome)
            continue
        if not identity:
            outcome.update(status="error", reason="identitas kosong")
            planned.append(outcome)
            continue
        if not address:
            outcome.update(status="error", reason="email kosong")
            planned.append(outcome)
            continue
        if not EMAIL_SHAPE.match(address):
            outcome.update(status="error", reason="bentuk email tidak wajar")
            planned.append(outcome)
            continue

        first = seen_emails.get(address)
        if first is not None:
            outcome.update(status="error",
                           reason=f"email ini juga dipakai baris {first}")
            planned.append(outcome)
            continue
        seen_emails[address] = row["row"]

        matches = cache.get(role, {}).get(identity, [])
        if not matches:
            label = IDENTITY_LABEL[role][0]
            outcome.update(status="error", reason=f"{label} tidak ditemukan di sekolah ini")
        elif len(matches) > 1:
            outcome.update(status="error",
                           reason="lebih dari satu akun memakai nama ini — pakai akun lain")
        elif _text(matches[0].get("email")).lower() == address:
            outcome.update(status="same", user_id=matches[0]["id"],
                           current=_text(matches[0].get("email")),
                           reason="email sudah sama")
        else:
            outcome.update(status="update", user_id=matches[0]["id"],
                           current=_text(matches[0].get("email")))
        planned.append(outcome)
    return planned


def apply(plan_rows: list[dict], supabase) -> list[dict]:
    """Write the planned addresses: Supabase Auth first, then the mirror.

    A row that fails is carried in its own ``reason`` rather than raised: the operator
    asked for a sheet to be applied, and "these three addresses could not be changed"
    belongs in the report next to the others that could.
    """
    for row in plan_rows:
        if row["status"] != "update":
            continue
        try:
            supabase.auth.admin.update_user_by_id(
                row["user_id"], {"email": row["email"], "email_confirm": True})
        except Exception as exc:                                  # noqa: BLE001
            row["status"] = "error"
            row["reason"] = f"ditolak Auth: {str(exc)[:80]}"
            continue
        try:
            supabase.table("profiles").update({"email": row["email"]}) \
                .eq("id", row["user_id"]).execute()
        except Exception as exc:                                  # noqa: BLE001
            # The address works — sign-in and reset both read it from Auth — so this is
            # a stale mirror, not a failed change. Reported as such.
            row["reason"] = f"email aktif, salinan di profil gagal ditulis: {str(exc)[:60]}"
    return plan_rows


def template_bytes(school_name: str = "") -> bytes:
    """The upload sheet, with the three columns and one worked example per role."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Email"
    headers = ["Peran", "Identitas (NISN/NIP/Nama)", "Email"]
    for column, text in enumerate(headers, 1):
        cell = ws.cell(row=1, column=column, value=text)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    examples = [
        ("murid", "1234567801", "murid.contoh@sekolah.sch.id"),
        ("guru", "19876543", "guru.contoh@sekolah.sch.id"),
        ("kepala", "Nama Kepala Sekolah", "kepsek@sekolah.sch.id"),
        ("wakil kepala", "Nama Wakil Kepala", "wakil@sekolah.sch.id"),
    ]
    for offset, example in enumerate(examples, start=2):
        for column, value in enumerate(example, 1):
            ws.cell(row=offset, column=column, value=value)
    for column, width in ((1, 16), (2, 34), (3, 34)):
        ws.column_dimensions[ws.cell(row=1, column=column).column_letter].width = width
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def summarise(plan_rows: list[dict]) -> dict:
    """``{updated, same, errors, total}`` — the counts the page flashes."""
    return {
        "updated": sum(1 for r in plan_rows if r["status"] == "update"),
        "same": sum(1 for r in plan_rows if r["status"] == "same"),
        "errors": sum(1 for r in plan_rows if r["status"] == "error"),
        "total": len(plan_rows),
    }
