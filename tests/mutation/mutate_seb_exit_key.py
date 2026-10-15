"""Mutation check for the SEB exit key — the secret a locked pupil depends on.

One claim is under test, and it is the whole feature: **the password a supervisor
reads off the panel is the password SEB accepts for the file that pupil is holding.**
On 15 October 2026 it was false, a real pupil could not leave a real paper, and the
first diagnosis — "the hash algorithm must be wrong" — was wrong. The client's own
source and its own unit-test vector say `sha256_hex` was right all along
(`app/services/seb_crypto.py` names both), and SEB's client log for that sitting records
the comparison it actually made, three times: *The user entered the wrong quit password.*

So the two defects that were real are the two families here:

* **one slot, two cells.** `seb_panel.html` kept a single Alpine `password` variable for
  the quit cell *and* the admin cell while tracking visibility per kind, so revealing
  the second overwrote the first — a supervisor could read the admin password out of the
  box labelled *quit*. Every mutation below turns one per-kind thing back into a shared
  one, and each is a *silent* defect: the page still renders, the button still works, and
  the value is simply the wrong secret.
* **a file nothing flags as void.** Issuing replaces both passwords and writes a new
  Config Key over the stored one, so every `.seb` already handed out stops matching —
  and `file_is_current` cannot see it, because the re-issue updates the yardstick it
  compares against. The mutations here make the warning fire when it must not (a page
  that tells a school to reprint for no reason) and never fire when it must.

The size of the key is defended too, in both directions: four capitals is what the
operator asked for after the space was named to them (26^4, 18.8 bits, written down in
the module), and a mutation that quietly changes the length or the alphabet is a
different promise than the one the docs make.

Run:  python deploy/mutation_check.py --run mutate_seb_exit_key     (~1 minute)
      python deploy/mutation_check.py --check                      (anchors only)
"""
from __future__ import annotations

import sys

from tests.mutation.harness import main

GUARDS = [
    "tests/unit/test_seb_exit_key.py",
    "tests/unit/test_seb_service.py",
]

CRYPTO = "app/services/seb_crypto.py"
SERVICE = "app/services/seb_service.py"
ROUTE = "app/routes/seb.py"
PANEL = "app/templates/teacher/seb_panel.html"

MUTATIONS = [
    # ── the defect that reached production: one slot, two cells ──────────────
    ("the two cells go back to one shared variable",
     PANEL,
     "x-text=\"passwords['{{ kind }}']\"",
     "x-text=\"password\""),
    ("the reveal writes to the shared slot again",
     PANEL,
     "if (data.ok) { this.passwords[kind] = data.password;",
     "if (data.ok) { this.password = data.password;"),
    ("hiding one kind blanks the other one",
     PANEL,
     "this.shown[kind] = false; this.passwords[kind] = '';",
     "this.shown[kind] = false; this.password = '';"),
    ("the reveal button always asks for the quit password",
     PANEL,
     "reveal('{{ kind }}')",
     "reveal('quit')"),
    ("the request always names the quit password",
     PANEL,
     "fd.append('kind', kind);",
     "fd.append('kind', 'quit');"),

    # ── a file the issue has voided, and the sentence about it ───────────────
    ("the void warning can never fire",
     ROUTE,
     "file_void=live and seb_service.file_void_after_reissue(credential, access_log),",
     "file_void=False and seb_service.file_void_after_reissue(credential, access_log),"),
    ("the flag the route passes stops being the one the panel reads",
     ROUTE,
     "        file_void=live and seb_service.file_void_after_reissue",
     "        file_void_flag=live and seb_service.file_void_after_reissue"),
    ("a void file is claimed out of an empty log",
     SERVICE,
     "    if not moments:\n        return False\n    return issued_at > max(moments)",
     "    if not moments:\n        return False\n    return True"),
    ("reading a password starts counting as handing out a file",
     SERVICE,
     "if isinstance(row, dict) and row.get(\"access_type\") == DOWNLOADED]",
     "if isinstance(row, dict)]"),
    ("a missing issue moment is read as \"void\"",
     SERVICE,
     "    issued_at = _row_moment({\"created_at\": (credential or {}).get(\"updated_at\")})\n"
     "    if issued_at is None:\n        return False",
     "    issued_at = _row_moment({\"created_at\": (credential or {}).get(\"updated_at\")})\n"
     "    if False:\n        return False"),

    # ── the algorithm, and the size the operator asked for ───────────────────
    ("the hash stops being SHA-256 over UTF-8",
     CRYPTO,
     "hashlib.sha256(str(text or \"\").encode(\"utf-8\")).hexdigest()",
     "hashlib.sha256(str(text or \"\").encode(\"utf-16-le\")).hexdigest()"),
    ("the exit key goes back to six characters",
     CRYPTO,
     "PASSWORD_LENGTH = 4",
     "PASSWORD_LENGTH = 6"),
    ("the alphabet picks up lower case again",
     CRYPTO,
     "PASSWORD_ALPHABET = \"ABCDEFGHIJKLMNOPQRSTUVWXYZ\"",
     "PASSWORD_ALPHABET = \"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz\""),

    # ── the copy that tells a teacher to reprint ─────────────────────────────
    ("the warning stops saying it happened after the file went out",
     PANEL,
     "'These passwords were regenerated AFTER the last .seb file was downloaded.'",
     "'These passwords were regenerated.'"),
]

if __name__ == "__main__":
    sys.exit(main("mutate_seb_exit_key", GUARDS, MUTATIONS))
