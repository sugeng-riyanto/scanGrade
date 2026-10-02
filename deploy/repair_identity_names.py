#!/usr/bin/env python3
"""Heal the display name split between `profiles` and Supabase Auth.

Reported live: a vice principal who also teaches Chemistry greeted every page on
`/teacher/dashboard` with

    Akun berhasil dibuat. Email: viceprincipalshb@shb.sch.id, Password: 5Ahj@tMlQc7z

The name was correct in `profiles` ("Aji Wahyu Budiyanto"); the *Auth* copy
(`auth.users.user_metadata.full_name`) held that message, because every creation
path writes both copies while every rename updated only the profile. The app now
reads the profile (`app/utils/auth.py`) and mirrors renames
(`app/services/identity_names.py`), but accounts made before that still carry the
drift — and this message is a *password* sitting in user metadata.

This script repairs the existing drift. It is deliberately timid:

* **Dry run by default.** With no flags it reads, plans and prints. `--apply
  --yes` is the only way to write, and it writes only the Auth `full_name`.
* **The profile is the source of truth.** It never invents a name; a profile with
  no `full_name` is left alone. The name it writes is exactly the row the school
  reads.
* **Idempotent and non-destructive.** A second run proposes nothing; it never
  deletes a user, changes a role, or touches `profiles`.
* **It flags what it cannot fix.** Metadata that looks like a credential
  ("Password:", "Akun berhasil dibuat") is called out in the report; the password
  itself is never printed.

It also lists **duplicate identities** — the same name in the same school on two
accounts — because that is the shape the reported case had (a `guru` and a
`vice_principal` for one person). Duplicates are *reported, never merged*: only a
human decides which account keeps the history.

Usage
-----
    python deploy/repair_identity_names.py                     # dry run
    python deploy/repair_identity_names.py --apply             # refused: needs --yes
    python deploy/repair_identity_names.py --apply --yes

Exit codes
----------
    0  ran (a dry run, or an apply that wrote)
    2  `--apply` without `--yes`, or the database could not be read
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import identity_names  # noqa: E402

OK = "ok"           # the two copies already agree
MIRROR = "mirror"   # the profile name differs; --apply writes it to Auth

#: Substrings that mark metadata as a message rather than a name. Deliberately
#: few and literal: they name the two shapes seen in the wild, and a false
#: positive only prints a warning.
CREDENTIAL_MARKERS = ("Password:", "Akun berhasil dibuat", "Password :")

#: The roles whose duplicate is worth an operator's attention. Pupils share
#: names by the hundred in a real school, so grouping them would bury the one
#: case that matters — one *member of staff* holding two accounts.
STAFF_ROLES = ("guru", "teacher", "principal", "vice_principal", "admin_sekolah")


def looks_like_credential(value: str) -> bool:
    text = value or ""
    return any(marker in text for marker in CREDENTIAL_MARKERS)


def decide(profile, auth_name) -> dict:
    """One profile's decision. Pure, so the rule is testable without a database."""
    profile_name = (profile.get("full_name") or "").strip()
    auth_name = (auth_name or "").strip()
    row = {
        "user_id": profile.get("id"),
        "email": profile.get("email") or "",
        "role": profile.get("role") or "",
        "school_id": profile.get("school_id"),
        "profile_name": profile_name,
        "auth_name": auth_name,
        "action": OK,
        "reason": "",
        "credential": looks_like_credential(auth_name),
    }
    if not profile_name:
        row["reason"] = "profil tanpa full_name — tidak ada yang bisa dicerminkan"
        return row
    if auth_name == profile_name:
        return row
    row.update(action=MIRROR,
               reason="nama profil dan salinan Auth berbeda")
    return row


def duplicate_identities(profiles, roles=STAFF_ROLES) -> list[list[dict]]:
    """Staff accounts that look like one person: same name, same school, two rows.

    Restricted to staff by default. A pupil roster is full of identical names, so
    including pupils would turn one real finding into a hundred decoys.
    """
    wanted = set(roles)
    groups: dict = {}
    for p in profiles:
        if p.get("role") not in wanted:
            continue
        name = (p.get("full_name") or "").strip().casefold()
        school = p.get("school_id")
        if not name or not school:
            continue
        groups.setdefault((school, name), []).append(p)
    return [rows for rows in groups.values() if len(rows) > 1]


def read_all(supabase):
    """Every profile and every Auth user's display name, as two dicts."""
    profiles = []
    offset = 0
    while True:
        batch = (supabase.table("profiles")
                 .select("id, full_name, role, school_id, email")
                 .range(offset, offset + 999).execute().data or [])
        profiles.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    auth_names: dict = {}
    page = 1
    while True:
        users = supabase.auth.admin.list_users(page=page, per_page=1000)
        if not users:
            break
        for u in users:
            meta = getattr(u, "user_metadata", None) or {}
            auth_names[str(getattr(u, "id", ""))] = (meta.get("full_name") or "")
        page += 1
    return profiles, auth_names


def plan(profiles, auth_names):
    return [decide(p, auth_names.get(str(p.get("id")), "")) for p in profiles]


def render(rows, duplicates):
    to_mirror = [r for r in rows if r["action"] == MIRROR]
    creds = [r for r in rows if r["credential"]]
    lines = ["repair identity names — ringkasan",
             f"  profil dibaca        : {len(rows)}",
             f"  akan dicerminkan     : {len(to_mirror)}",
             f"  sudah sama           : {len(rows) - len(to_mirror)}",
             f"  metadata seperti kredensial: {len(creds)}",
             f"  identitas ganda      : {len(duplicates)}",
             ""]
    if to_mirror:
        lines.append("akan dicerminkan (Auth full_name <- profil):")
        for r in to_mirror[:50]:
            shown = r["auth_name"] if not r["credential"] else "(disembunyikan — memuat kredensial)"
            lines.append(f"    - {r['user_id']}  {r['role']}  "
                         f"{shown!r} -> {r['profile_name']!r}")
        if len(to_mirror) > 50:
            lines.append(f"    … dan {len(to_mirror) - 50} lagi")
        lines.append("")
    if creds:
        lines.append("PERHATIAN — metadata memuat kredensial (nilai tidak dicetak):")
        for r in creds:
            lines.append(f"    - {r['user_id']}  {r['role']}  {r['email']}")
        lines.append("")
    if duplicates:
        lines.append("IDENTITAS GANDA (dilaporkan, TIDAK pernah digabung otomatis):")
        for group in duplicates:
            lines.append("    " + " | ".join(
                f"{p.get('role')}:{p.get('id')}" for p in group))
        lines.append("")
    return "\n".join(lines)


def apply_repairs(supabase, rows) -> int:
    written = 0
    for r in rows:
        if r["action"] != MIRROR:
            continue
        if identity_names.mirror_display_name(supabase, r["user_id"], r["profile_name"]):
            written += 1
    return written


def build_client():
    """The service-role client, from the environment, with no app context."""
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
    except Exception:
        pass
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set")
    return create_client(url, key)


def main(argv=None, supabase=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the profile name into Auth metadata (needs --yes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and plan only (the default)")
    parser.add_argument("--yes", action="store_true",
                        help="confirm an --apply against a real database")
    args = parser.parse_args(argv)

    if args.apply and not args.yes:
        print("repair identity names: --apply needs --yes.\n"
              "    Run it without --apply first to read the plan; nothing was written.")
        return 2

    if supabase is None:
        supabase = build_client()

    try:
        profiles, auth_names = read_all(supabase)
    except Exception as exc:  # noqa: BLE001 — a read failure is not a write
        print(f"repair identity names: could not read the database — "
              f"{type(exc).__name__}: {exc}")
        return 2

    rows = plan(profiles, auth_names)
    duplicates = duplicate_identities(profiles)
    print(render(rows, duplicates))

    if args.apply:
        print(f"applied: {apply_repairs(supabase, rows)} nama dicerminkan ke Auth.")
    else:
        print("dry run — tidak ada yang ditulis. Tambahkan --apply --yes untuk menulis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
