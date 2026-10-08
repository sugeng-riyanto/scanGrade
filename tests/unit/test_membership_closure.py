"""Penutupan keanggotaan: satu nama, penutupan total, dan asimetri yang sengaja.

Tiga kontrak yang dijaga berkas ini, dan ketiganya adalah keputusan produk yang
tidak boleh bisa dibalik tanpa satu uji ini gagal:

* **Satu nama untuk penutupan.** Sejak migrasi 064 hanya ada `closed`; nilai
  `inactive` dari 044 dipindahkan ke sana dan tidak ditulis kode mana pun lagi.
  Dua nama untuk satu keadaan adalah bagaimana sebuah halaman menampilkan dua
  jenis penutupan untuk satu peristiwa.
* **Penutupan itu total.** Setiap status selain `active` berarti tanpa akses,
  sehingga tidak ada jalan baca kedua yang harus diingat untuk ikut ditutup.
* **Asimetri pembukaan kembali.** Menyetujui dan menutup boleh tiga peran
  (admin_sekolah, principal, vice_principal); membuka kembali HANYA
  admin_sekolah — dan ditolak SEBELUM satu baris pun berubah.

Uji-uji ini menegakkan aturannya di tempat aturannya hidup (lapisan service),
lalu memaku bahwa rute memakai dekorator yang sama — sehingga sebuah rute tidak
bisa memperlonggar aturan yang service-nya sudah menegakkan.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import school_membership as membership

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "school_membership.py"
ROUTES = ROOT / "app" / "routes" / "membership_routes.py"
MIGRATION = ROOT / "supabase" / "migrations" / "064_membership_closure_canonical.sql"


class _Query:
    """A fake that really filters — so "not found" is an observation, not a stub."""

    def __init__(self, table, rows, log, fail=False):
        self.table = table
        self._all = list(rows)
        self.log = log
        self.fail = fail
        self.filters = []
        self.mode = None
        self.payload = None
        self.limited = None

    def select(self, *a, **k):
        if self.fail:
            raise RuntimeError("no such table")
        self.mode = "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, n):
        self.limited = n
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.mode = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def _matched(self):
        return [r for r in self._all
                if all(str(r.get(c)) == str(v) for c, v in self.filters)]

    def execute(self):
        if self.fail:
            raise RuntimeError("no such table")
        if self.mode == "update":
            self.log.append(("write", self.table, dict(self.payload),
                             dict(self.filters)))
            matched = self._matched()
            return SimpleNamespace(data=[dict(matched[0], **self.payload)]
                                   if matched else [])
        if self.mode == "upsert":
            self.log.append(("write", self.table, dict(self.payload),
                             dict(self.filters)))
            return SimpleNamespace(data=[dict(self.payload, id="m-1")])
        rows = self._matched()
        if self.limited:
            rows = rows[:self.limited]
        return SimpleNamespace(data=rows)


class _Sb:
    def __init__(self, tables=None, fail_tables=()):
        self.tables = tables or {}
        self.fail_tables = set(fail_tables)
        self.log = []

    def table(self, name):
        return _Query(name, self.tables.get(name, []), self.log,
                      fail=name in self.fail_tables)

    # ── assertions the tests share ───────────────────────────────────────────
    def writes(self):
        return [e for e in self.log if e[0] == "write"]

    def writes_to(self, table):
        return [e for e in self.writes() if e[1] == table]

    def invariants(self):
        """The statuses 044 and 064 define, for the closure vocabulary test."""
        return ("active", "invited", "inactive", "closed")


# ── satu nama untuk penutupan ────────────────────────────────────────────────

def test_closed_is_the_single_canonical_closure_status():
    assert membership.CLOSED_STATUS == "closed"


def test_no_code_path_still_writes_inactive():
    """The canonicalisation only holds if nothing keeps writing the old name."""
    src = SERVICE.read_text(encoding="utf-8")
    # The literal must be gone from the service entirely — a stray `"inactive"`
    # is how a second kind of closure reappears in the data.
    assert '"inactive"' not in src
    assert "'inactive'" not in src


def test_deactivate_writes_closed_with_an_author():
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "active"}]})
    membership.deactivate(sb, "sc-A", "u-1", actor_id="admin-9")

    writes = sb.writes_to("teacher_school_membership")
    assert len(writes) == 1
    _, _, payload, filters = writes[0]
    assert payload["status"] == "closed"
    assert payload["closed_by"] == "admin-9"
    assert payload["closed_at"], "a closure with no moment cannot be acted on"
    # Scoped to the PAIR: an id alone must not close a membership elsewhere.
    assert filters["school_id"] == "sc-A" and filters["user_id"] == "u-1"


def test_the_migration_moves_inactive_to_closed_and_is_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert re.search(r"SET\s+status\s*=\s*'closed'", code), "no data migration"
    assert re.search(r"WHERE\s+status\s*=\s*'inactive'", code), \
        "an unconditional UPDATE is not idempotent in the sense that matters"
    assert not re.search(r"DROP\s+(TABLE|COLUMN)", code), "destructive migration"
    assert "DELETE" not in code.upper().replace("ON DELETE", "")
    # The moment comes from the row's own history, not from when we ran the file.
    assert "COALESCE(closed_at, updated_at" in code


# ── penutupan itu total ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["invited", "inactive", "closed"])
def test_any_non_active_status_means_no_access(status):
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": status}]})
    assert membership.is_active_member(sb, "u-1", "sc-A") is False


def test_an_active_membership_still_resolves():
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "active"}]})
    assert membership.is_active_member(sb, "u-1", "sc-A") is True
    assert membership.member_school_ids(sb, "u-1") == {"sc-A"}


def test_a_closed_school_drops_out_of_the_chooser():
    """A closed membership must not survive in `member_school_ids`."""
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-home", "status": "active"},
        {"user_id": "u-1", "school_id": "sc-closed", "status": "closed"}]})
    assert membership.member_school_ids(sb, "u-1") == {"sc-home"}
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-closed",
                                            "guru") == "sc-home"


def test_a_missing_table_still_fails_closed():
    sb = _Sb(fail_tables=("teacher_school_membership",))
    assert membership.is_active_member(sb, "u-1", "sc-A") is False
    assert membership.resolve_active_school(sb, "u-1", "sc-home", "sc-A",
                                            "guru") is None


# ── asimetri approve vs reopen ───────────────────────────────────────────────

def test_three_roles_may_approve():
    for role in ("admin_sekolah", "principal", "vice_principal"):
        assert membership.may_approve(role), role
    for role in ("guru", "murid", "super_admin", None):
        assert not membership.may_approve(role), role


def test_only_the_school_admin_may_reopen():
    assert membership.may_reopen("admin_sekolah")
    for role in ("principal", "vice_principal", "guru", "murid", "super_admin",
                 None):
        assert not membership.may_reopen(role), role


@pytest.mark.parametrize("role", ["principal", "vice_principal", "guru",
                                  "murid", "super_admin"])
def test_reopen_by_anyone_else_is_refused_before_any_write(role):
    """The asymmetry, and the part that matters: nothing was written."""
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "closed"}]})
    out = membership.reopen_membership(sb, "sc-A", "u-1", actor_id="x",
                                       actor_role=role)
    assert out == {"ok": False, "reason": "not_authorised"}
    assert sb.writes() == [], f"{role} caused a write while being refused"


def test_the_admin_reopens_and_the_closure_history_is_kept():
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "closed",
         "closed_by": "admin-1", "closed_at": "2026-01-01T00:00:00Z"}]})
    out = membership.reopen_membership(sb, "sc-A", "u-1", actor_id="admin-9",
                                       actor_role="admin_sekolah")
    assert out["ok"] is True
    _, _, payload, _ = sb.writes_to("teacher_school_membership")[0]
    assert payload["status"] == "active"
    assert payload["reopened_by"] == "admin-9" and payload["reopened_at"]
    # Reopening is a NEW fact, not an erasure of the old one.
    assert "closed_by" not in payload and "closed_at" not in payload


def test_reopening_something_that_is_not_closed_changes_nothing():
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "active"}]})
    out = membership.reopen_membership(sb, "sc-A", "u-1", actor_id="admin-9",
                                       actor_role="admin_sekolah")
    assert out["ok"] is False and out["reason"] == "not_closed_or_missing"


def test_reopening_is_scoped_to_the_school():
    """An admin of another school cannot reopen this membership."""
    sb = _Sb({"teacher_school_membership": [
        {"user_id": "u-1", "school_id": "sc-A", "status": "closed"}]})
    out = membership.reopen_membership(sb, "sc-OTHER", "u-1", actor_id="admin-9",
                                       actor_role="admin_sekolah")
    assert out["ok"] is False and out["reason"] == "not_closed_or_missing"


# ── keputusan permintaan ─────────────────────────────────────────────────────

def _pending_request(school="sc-A", teacher="t-1", status="pending"):
    return {"id": "req-1", "teacher_id": teacher, "target_school_id": school,
            "status": status}


@pytest.mark.parametrize("role", ["guru", "murid", "super_admin", None])
def test_a_non_approver_cannot_decide_a_request(role):
    sb = _Sb({"school_membership_request": [_pending_request()]})
    out = membership.decide_request(sb, "sc-A", "req-1", "approved",
                                    actor_id="x", actor_role=role)
    assert out == {"ok": False, "reason": "not_authorised"}
    assert sb.writes() == [], f"{role} caused a write while being refused"


def test_a_request_in_another_school_is_not_found_not_forbidden():
    """The caller must not even learn that another school's request exists."""
    sb = _Sb({"school_membership_request": [_pending_request(school="sc-B")]})
    out = membership.decide_request(sb, "sc-A", "req-1", "approved",
                                    actor_id="a", actor_role="admin_sekolah")
    assert out == {"ok": False, "reason": "not_found"}
    assert sb.writes() == []


def test_an_already_decided_request_is_not_decided_twice():
    sb = _Sb({"school_membership_request": [_pending_request(status="approved")]})
    out = membership.decide_request(sb, "sc-A", "req-1", "rejected",
                                    actor_id="a", actor_role="admin_sekolah")
    assert out == {"ok": False, "reason": "not_found"}
    assert sb.writes() == []


def test_an_unknown_decision_token_is_refused():
    sb = _Sb({"school_membership_request": [_pending_request()]})
    out = membership.decide_request(sb, "sc-A", "req-1", "maybe",
                                    actor_id="a", actor_role="admin_sekolah")
    assert out["reason"] == "bad_decision"
    assert sb.writes() == []


@pytest.mark.parametrize("role", ["admin_sekolah", "principal", "vice_principal"])
def test_each_approver_role_can_approve_and_the_access_arrives_with_it(role):
    sb = _Sb({"school_membership_request": [_pending_request()]})
    out = membership.decide_request(sb, "sc-A", "req-1", "approved",
                                    actor_id="a", actor_role=role,
                                    reason=None)
    assert out["ok"] is True
    _, _, payload, filters = sb.writes_to("school_membership_request")[0]
    assert payload["status"] == "approved"
    assert payload["decided_role"] == role, "the authority must be recorded"
    assert filters["target_school_id"] == "sc-A"
    # Approval grants the membership in the same call: a request can never read
    # `approved` while the access it promised does not exist.
    granted = sb.writes_to("teacher_school_membership")
    assert granted and granted[0][2]["status"] == "active"


def test_rejection_grants_nothing():
    sb = _Sb({"school_membership_request": [_pending_request()]})
    out = membership.decide_request(sb, "sc-A", "req-1", "rejected",
                                    actor_id="a", actor_role="admin_sekolah",
                                    reason="kelas penuh")
    assert out["ok"] is True and out["decision"] == "rejected"
    assert sb.writes_to("teacher_school_membership") == []


# ── rute memakai aturan yang sama ────────────────────────────────────────────

def _route_source(name: str) -> str:
    src = ROUTES.read_text(encoding="utf-8")
    start = src.index(f"def {name}(")
    # Walk back over the decorator block above the def.
    head = src[:start]
    tail = head[head.rindex("\n\n") + 2:] if "\n\n" in head else head
    return tail


def test_the_reopen_route_is_the_school_admin_only():
    block = _route_source("membership_reopen")
    assert "@admin_sekolah_required" in block
    assert "principal" not in block, "the reopen door admits an official"


def test_the_approve_and_close_routes_admit_the_two_officials():
    for name in ("membership_request_decide", "membership_close"):
        block = _route_source(name)
        assert "principal" in block and "vice_principal" in block, name


def test_every_membership_route_is_audited():
    src = ROUTES.read_text(encoding="utf-8")
    for name in ("membership_request_decide", "membership_close",
                 "membership_reopen"):
        start = src.index(f"def {name}(")
        end = src.index("\ndef ", start + 1) if "\ndef " in src[start:] else len(src)
        body = src[start:end]
        assert "log_activity(" in body, f"{name} is not audited"
