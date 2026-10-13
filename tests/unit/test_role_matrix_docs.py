"""The messaging matrix must name every role, and list what the code lists.

`AGENTS.md` carried a "Pertukaran Pesan — RBAC Matrix" whose header named **four**
roles while the application has **six** since migration 038 (`principal` and
`vice_principal`), and nothing anywhere failed when the two drifted apart: a
document that speaks of four roles beside a database that accepts six is how a
reader concludes the officials "do not exist".

The matrix now names all six, and the prose beneath it prints the recipient list of
every role. This file keeps the two honest:

* **every role the app knows is named in the matrix**, read from
  `DASHBOARD_FOR_ROLE` — so adding a seventh role fails here until the table is
  updated, instead of silently going undocumented;
* **the lists printed in the document are the lists the code returns**
  (`api._get_allowed_recipient_roles`), parsed out of the prose and compared as
  sets — a doc that *describes* the code is the only kind worth having;
* **the three readings that a table like this invites are written down** — the
  one-directional list versus the mutual conversation, what addressing your own role
  means, and the fact that a pupil's channels did not change. A table without those
  sentences is a table someone will misread.

The behaviour itself is pinned in `tests/unit/test_official_messaging.py`; this file
is the half that fails when the *documentation* drifts from it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
ROLES_DOC = ROOT / "docs" / "ROLES_CLARIFICATION.md"

SECTION_HEAD = "## Pertukaran Pesan — RBAC Matrix"

#: Every role has a printed `role → [...]` line now: the officials joined the table
#: rather than being described as absent from it.
DOCUMENTED_SENDERS = ("super_admin", "admin_sekolah", "principal", "vice_principal",
                      "guru", "murid")


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
    def test_the_paragraph_prints_all_six_recipient_lists(self, matrix_section):
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

    def test_the_officials_are_in_the_documented_table(self, matrix_section):
        """The fact this table exists to record: the officials have channels."""
        from app.routes.api import _get_allowed_recipient_roles as allowed

        assert allowed("principal"), "the officials are back to sending nothing"
        assert "guru" in allowed("principal") and "principal" in allowed("guru"), (
            "the head of school and the teachers must reach each other"
        )


class TestTheThreeReadingsAreWrittenDown:
    """A table invites three mistakes; the section names all three."""

    def test_the_mutual_pair_rule_is_explained(self, matrix_section):
        assert "_conversation_roles" in matrix_section, (
            "the one-directional list versus the mutual conversation is the first "
            "thing a reader gets wrong: print the reading that filters dead contacts"
        )
        assert "403" in matrix_section

    def test_addressing_your_own_role_is_explained(self, matrix_section):
        assert "grup" in matrix_section, (
            "reaching your own role means the group, which is why the entry stays"
        )

    def test_the_pupils_channels_are_stated_as_unchanged(self, matrix_section):
        assert "murid" in matrix_section and "1-on-1" in matrix_section, (
            "announcing to a role and holding a conversation with it are different "
            "acts; say so where the pupils' unchanged list is"
        )


class TestTheReferenceDocRecordsTheDecision:
    def test_the_standing_reference_names_the_function_it_read(self):
        text = _text(ROLES_DOC)
        assert "`_ALLOWED_RECIPIENT_ROLES`" in text, (
            "the reference doc must name the table it read, so a later reader can "
            "check it rather than trust it"
        )

    def test_the_officials_section_is_no_longer_an_open_question(self):
        text = _text(ROLES_DOC)
        assert "sistem pesan" in text and "principal" in text, (
            "docs/ROLES_CLARIFICATION.md is the standing reference; the officials' "
            "messaging belongs there"
        )
        assert "belum diputuskan" not in text, (
            "the decision has been made and implemented — a section still calling it "
            "undecided tells the next session to leave it alone"
        )
