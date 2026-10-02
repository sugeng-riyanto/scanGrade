"""A rename must reach the Auth copy too — and existing drift must be healable.

The dashboard's glitch (`/teacher/dashboard` greeting showed
"Akun berhasil dibuat. Email: …, Password: …") was one instance of a wider class:
`profiles.full_name` is renamed by the school, but `auth.users.user_metadata`
keeps the name the account was *created* with. The session now reads the profile
(see `test_identity_name_source.py`), which fixes the display; this suite pins the
other half — that a rename updates the Auth copy as well, so no reader can ever
fall back onto a stale or pasted name.
"""
from __future__ import annotations

import pathlib

from types import SimpleNamespace

from app.services import identity_names

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Admin:
    def __init__(self, metadata=None, *, refuse=False):
        self.metadata = dict(metadata or {})
        self.calls = []
        self.refuse = refuse

    def get_user_by_id(self, uid):
        if self.refuse:
            raise RuntimeError("auth refused")
        return SimpleNamespace(user=SimpleNamespace(id=uid, user_metadata=dict(self.metadata)))

    def update_user_by_id(self, uid, attrs):
        if self.refuse:
            raise RuntimeError("auth refused")
        self.calls.append((uid, attrs))
        self.metadata.update(attrs.get("user_metadata", {}))
        return SimpleNamespace(user=SimpleNamespace(id=uid, user_metadata=dict(self.metadata)))


class _Sb:
    def __init__(self, admin):
        self.auth = SimpleNamespace(admin=admin)


def test_a_rename_mirrors_the_new_name_into_auth_metadata():
    admin = _Admin({"role": "guru", "full_name": "Akun berhasil dibuat. Password: xyz"})
    changed = identity_names.mirror_display_name(_Sb(admin), "u-1", "Aji Wahyu Budiyanto")
    assert changed is True
    assert admin.metadata["full_name"] == "Aji Wahyu Budiyanto"
    # The role survives: the mirror merges, it does not replace the whole blob.
    assert admin.metadata["role"] == "guru"


def test_an_unchanged_name_writes_nothing():
    admin = _Admin({"full_name": "Aji Wahyu Budiyanto"})
    assert identity_names.mirror_display_name(_Sb(admin), "u-1", "Aji Wahyu Budiyanto") is False
    assert admin.calls == []


def test_a_blank_name_is_never_written():
    admin = _Admin({"full_name": "Aji Wahyu Budiyanto"})
    assert identity_names.mirror_display_name(_Sb(admin), "u-1", "   ") is False
    assert admin.calls == []


def test_a_refusal_is_reported_not_raised():
    admin = _Admin({"full_name": "old"}, refuse=True)
    assert identity_names.mirror_display_name(_Sb(admin), "u-1", "new") is False


# ── the rename routes call it ────────────────────────────────────────────────

def test_the_teacher_edit_mirrors_the_name():
    src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
    body = src.split("def edit_teacher(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
    assert "mirror_display_name" in body, (
        "editing a teacher renames only profiles, leaving the Auth copy to go stale")


def test_the_official_edit_mirrors_the_name():
    src = (ROOT / "app" / "services" / "school_officials.py").read_text(encoding="utf-8")
    body = src.split("def update_official(", 1)[1].split("\ndef ", 1)[0]
    assert "mirror_display_name" in body, (
        "renaming an official leaves the Auth copy stale")


def test_the_student_edit_mirrors_the_name():
    src = (ROOT / "app" / "routes" / "admin_sekolah.py").read_text(encoding="utf-8")
    body = src.split("def edit_student(", 1)[1].split("\n@admin_sekolah_bp", 1)[0]
    assert "mirror_display_name" in body, (
        "renaming a pupil leaves the Auth copy stale")


# ── the repair script ────────────────────────────────────────────────────────

import importlib.util  # noqa: E402
import sys  # noqa: E402

SCRIPT = ROOT / "deploy" / "repair_identity_names.py"


def repair_module():
    spec = importlib.util.spec_from_file_location("sg_repair_names", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GLITCH_TEXT = "Akun berhasil dibuat. Email: a@b.c, Password: 5Ahj@tMlQc7z"


def test_the_glitch_string_is_flagged_as_a_credential():
    assert repair_module().looks_like_credential(GLITCH_TEXT) is True
    assert repair_module().looks_like_credential("Aji Wahyu Budiyanto") is False


def test_the_repair_mirrors_a_drifted_name():
    decision = repair_module().decide(
        {"id": "u-1", "full_name": "Aji Wahyu Budiyanto", "role": "guru"}, GLITCH_TEXT)
    assert decision["action"] == repair_module().MIRROR
    assert decision["credential"] is True


def test_the_repair_is_a_noop_when_names_agree():
    decision = repair_module().decide(
        {"id": "u-1", "full_name": "Aji Wahyu Budiyanto", "role": "guru"},
        "Aji Wahyu Budiyanto")
    assert decision["action"] == repair_module().OK


def test_the_repair_never_invents_a_name():
    decision = repair_module().decide(
        {"id": "u-1", "full_name": "", "role": "guru"}, "some old value")
    assert decision["action"] == repair_module().OK


def test_duplicate_identities_group_by_name_and_school():
    people = [
        {"id": "a", "full_name": "Aji Wahyu Budiyanto", "school_id": "S1", "role": "guru"},
        {"id": "b", "full_name": "Aji Wahyu Budiyanto", "school_id": "S1", "role": "vice_principal"},
        {"id": "c", "full_name": "Aji Wahyu Budiyanto", "school_id": "S2", "role": "guru"},
    ]
    groups = repair_module().duplicate_identities(people)
    assert len(groups) == 1
    assert {p["id"] for p in groups[0]} == {"a", "b"}


def test_pupils_sharing_a_name_are_not_reported_as_duplicates():
    """A real roster is full of identical pupil names; only staff matter here."""
    pupils = [
        {"id": "a", "full_name": "Budi Santoso", "school_id": "S1", "role": "murid"},
        {"id": "b", "full_name": "Budi Santoso", "school_id": "S1", "role": "murid"},
    ]
    assert repair_module().duplicate_identities(pupils) == []


def test_an_apply_without_yes_writes_nothing():
    assert repair_module().main(["--apply"], supabase=object()) == 2
