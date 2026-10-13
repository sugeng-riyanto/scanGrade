"""The messaging RBAC matrix must name every role, and list what the code lists.

`AGENTS.md` carried a "Pertukaran Pesan — RBAC Matrix" whose header named **four**
roles while the application has **six** since migration 038 (`principal` and
`vice_principal`), and nothing anywhere failed when the two drifted apart: a
document that speaks of four roles beside a database that accepts six is how a
reader concludes the officials "do not exist". This file is the guard that keeps
those two facts in one place.

It pins three things, and each one is a way the drift happened:

* **every role the app knows is named in the matrix**, read from
  `DASHBOARD_FOR_ROLE` — so adding a seventh role fails here until the table is
  updated, instead of silently going undocumented;
* **the recipient lists printed in the document are the lists the code returns**
  (`api._get_allowed_recipient_roles`), parsed out of the prose and compared as
  sets — a doc that *describes* the code is the only kind worth having;
* **the officials' situation is recorded as an open product decision**, not as a
  silent gap, because `principal`/`vice_principal` cannot send — or be addressed
  by — anything today and "fixing" that changes what the role means.

All three are source-level checks over the same tree the app ships, in the style
of `tests/unit/test_principal_signature.py`: no database, no request.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
ROLES_DOC = ROOT / "docs" / "ROLES_CLARIFICATION.md"

SECTION_HEAD = "## Pertukaran Pesan — RBAC Matrix"

#: The roles that may be *addressed* (`[]` means "nobody"), as the document states
#: them. Kept here as the reading contract: the parser has to find all four.
DOCUMENTED_SENDERS = ("super_admin", "admin_sekolah", "guru", "murid")


def _text(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(ROOT)} is part of the role contract"
    return path.read_text(encoding="utf-8")


def _section(text: str, head: str) -> str:
    """The body of one `## ` section, without the next one."""
    start = text.index(head)
    rest = text[start + len(head):]
    nxt = rest.find("\n## ")
    return head + (rest if nxt == -1 else rest[:nxt])


def _documented_lists(section: str) -> dict[str, list[str]]:
    """`role → [a, b]` pairs out of the verified paragraph."""
    return {
        role: [r.strip() for r in names.split(",") if r.strip()]
        for role, names in re.findall(r"`([a-z_]+) → \[([^\]]*)\]`", section)
    }


@pytest.fixture(scope="module")
def matrix_section() -> str:
    return _section(_text(AGENTS), SECTION_HEAD)


class TestTheMatrixNamesEveryRole:
    def test_the_section_still_exists(self, matrix_section):
        assert "| Fitur |" in matrix_section, (
            "the messaging matrix is the standing reference for who may talk to whom"
        )

    def test_every_role_the_app_knows_is_named(self, matrix_section):
        from app.utils.auth import DASHBOARD_FOR_ROLE

        missing = [r for r in DASHBOARD_FOR_ROLE if r not in matrix_section]
        assert not missing, (
            f"{missing} exist as roles (app/utils/auth.py) but the messaging matrix "
            "never names them — update the table, don't leave them undocumented"
        )

    def test_the_officials_share_one_column_and_say_so(self, matrix_section):
        """`principal` and `vice_principal` are one column, labelled as such."""
        header = next(
            line for line in matrix_section.splitlines() if line.startswith("| Fitur |")
        )
        assert "principal" in header and "vice_principal" in header, (
            f"the officials' column is missing from the header: {header!r}"
        )


class TestTheListsAreTheCode:
    def test_the_paragraph_prints_all_four_recipient_lists(self, matrix_section):
        documented = _documented_lists(matrix_section)
        missing = [r for r in DOCUMENTED_SENDERS if r not in documented]
        assert not missing, (
            f"no `role → [...]` line for {missing} — the prose is the thing this "
            "test compares against the code"
        )

    def test_the_printed_lists_equal_the_function(self, matrix_section):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        documented = _documented_lists(matrix_section)
        for role in DOCUMENTED_SENDERS:
            assert set(documented[role]) == set(allowed(role)), (
                f"{role}: document says {sorted(documented[role])}, "
                f"code says {sorted(allowed(role))}"
            )

    def test_the_officials_recipient_list_is_still_empty(self, matrix_section):
        """The fact the paragraph exists to record: officials have nobody to write to."""
        from app.routes.api import _get_allowed_recipient_roles as allowed

        assert allowed("principal") == [] and allowed("vice_principal") == [], (
            "the officials' messaging was opened — record that decision in the "
            "matrix and docs/ROLES_CLARIFICATION.md, and delete this pin"
        )
        assert "peran lain → `[]`" in matrix_section, (
            "the paragraph must say the remaining roles have an empty list, which "
            "is what the two `assert` lines above just proved"
        )


class TestTheOpenDecisionIsWrittenDown:
    def test_the_standing_reference_records_it(self):
        text = _text(ROLES_DOC)
        assert "sistem pesan" in text and "principal" in text, (
            "docs/ROLES_CLARIFICATION.md is the standing reference; the officials' "
            "messaging gap belongs there, not only in a changelog"
        )
        assert "keputusan produk" in text, (
            "the gap must be named as a product decision to make, not as a defect "
            "someone should quietly repair"
        )

    def test_both_documents_agree_on_the_recipient_list(self, matrix_section):
        """The reference doc prints the same `[]` the matrix does."""
        roles_doc = _text(ROLES_DOC)
        assert "`_get_allowed_recipient_roles`" in roles_doc, (
            "the reference doc must name the function it read, so a later reader "
            "can check it rather than trust it"
        )
        assert "**tidak ada** (`[]`)" in roles_doc, (
            "the reference doc's recipient row for the officials must read `[]`"
        )
