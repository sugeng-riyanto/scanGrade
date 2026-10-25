"""The fixture provisioner, driven against a database that is a dict.

`deploy/scanmeet_staging_fixture.py` writes into a live Supabase project, which a
test cannot do and must not need to. What it *can* be held to is the shape of
what it writes: two schools, one class each, one subject, four profiles with the
first-sign-in flag cleared, and one published paper with the switch on, assigned
to the class both pupils are in — plus the two refusals that keep it out of the
production project.
"""
import sys
from pathlib import Path

import pytest

from deploy import scanmeet_staging_fixture as fixture
from deploy.supabase_region import REF as PRODUCTION_REF


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    """The supabase-py chain shape this module actually uses."""

    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op = "select"
        self.filters = []
        # `is_(col, "null")` is how the fixture finds the one account that belongs
        # to no school. It is deliberately not `eq(col, None)`: PostgREST reads
        # that as the string `None` and answers no rows, which would make a
        # re-run create a second copy of the account every time.
        self.nulls = []
        self.payload = None
        self.limit_n = None

    def select(self, _columns="*"):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def is_(self, key, value):
        assert str(value).lower() == "null", value
        self.nulls.append(key)
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def _matches(self, row):
        return (all(str(row.get(k)) == str(v) for k, v in self.filters)
                and all(row.get(k) is None for k in self.nulls))

    def execute(self):
        rows = self.db.data.setdefault(self.name, [])
        if self.op == "insert":
            row = dict(self.payload)
            row.setdefault("id", f"{self.name}-{len(rows) + 1}")
            rows.append(row)
            self.db.writes.append((self.name, "insert", row))
            return Result([row])
        matched = [row for row in rows if self._matches(row)]
        if self.op == "update":
            for row in matched:
                row.update(self.payload)
            self.db.writes.append((self.name, "update", dict(self.payload)))
            return Result(matched)
        return Result(matched[: self.limit_n] if self.limit_n else matched)


class Admin:
    def __init__(self, db):
        self.db = db

    def update_user_by_id(self, uid, payload):
        self.db.passwords_changed.append((uid, payload.get("password")))
        return Result([{"id": uid}])


class FakeDb:
    def __init__(self):
        self.data = {}
        self.writes = []
        self.passwords_changed = []
        self.auth = type("Auth", (), {"admin": Admin(self)})()

    def table(self, name):
        return Query(self, name)


def fake_create(db, **kwargs):
    """`account_creation.create_account`, reduced to the rows it writes."""
    uid = "user-" + kwargs["email"].split("@")[0]
    profile = {
        "id": uid, "full_name": kwargs["full_name"], "role": kwargs["role"],
        "school_id": kwargs["school_id"], "status": kwargs.get("status", "active"),
        # The real service sets this; a fake that omits it would let the
        # clearing of the flag pass without ever having had anything to clear.
        "must_change_password": True,
    }
    profile.update(kwargs.get("profile_fields") or {})
    db.data.setdefault("profiles", []).append(profile)
    if kwargs.get("role_table"):
        db.data.setdefault(kwargs["role_table"], []).append(
            {"id": uid, **kwargs.get("role_fields", {})})
    return uid


def quiet(*_a, **_k):
    pass


def provision(write=True, password="fixture-pw"):
    db = FakeDb()
    report = fixture.ensure(db, write=write, say=quiet, create=fake_create,
                            password=password)
    return db, report


# ── the refusals ─────────────────────────────────────────────────────────────

def test_the_production_project_is_refused_in_words():
    settings = {"SUPABASE_URL": f"https://{PRODUCTION_REF}.supabase.co",
                "SUPABASE_SERVICE_KEY": "service"}
    found = fixture.problems(settings, PRODUCTION_REF)
    assert found and "production project" in found[0]
    assert "refusing to write" in found[0]


def test_a_placeholder_or_a_non_project_url_is_refused():
    assert fixture.problems({"SUPABASE_URL": "https://your-project.supabase.co",
                             "SUPABASE_SERVICE_KEY": "service"}, PRODUCTION_REF)
    assert any("placeholder" in p for p in fixture.problems(
        {"SUPABASE_URL": "https://your-project.supabase.co",
         "SUPABASE_SERVICE_KEY": "service"}, PRODUCTION_REF))
    assert any("project URL" in p for p in fixture.problems(
        {"SUPABASE_URL": "https://staging.example.com",
         "SUPABASE_SERVICE_KEY": "service"}, PRODUCTION_REF))


def test_missing_keys_are_named_rather_than_assumed():
    found = fixture.problems({}, PRODUCTION_REF)
    assert any("SUPABASE_URL" in p for p in found)
    assert any("SUPABASE_SERVICE_KEY" in p for p in found)


def test_ref_of_reads_a_project_and_nothing_else():
    assert fixture.ref_of("https://abcdefghijkl.supabase.co") == "abcdefghijkl"
    assert fixture.ref_of("https://abcdefghijkl.supabase.co/") == "abcdefghijkl"
    assert fixture.ref_of("https://scangrade.web.id") == ""
    assert fixture.ref_of("") == ""


def test_an_env_file_inside_the_checkout_is_refused(tmp_path, monkeypatch):
    checkout = Path(fixture.__file__).resolve().parents[1]
    target = checkout / "staging.env"
    # --env pointing into the checkout must be refused before it is read, so the
    # file does not even have to exist for the rule to hold.
    assert fixture.main(["--env", str(target)]) == 1
    assert fixture.main(["--env", str(tmp_path / "absent.env")]) == 2


def test_a_production_env_is_refused_before_a_client_is_built(tmp_path):
    env = tmp_path / "staging.env"
    env.write_text(f"SUPABASE_URL=https://{PRODUCTION_REF}.supabase.co\n"
                   "SUPABASE_SERVICE_KEY=service\n", encoding="utf-8")
    assert fixture.main(["--env", str(env)]) == 1


# ── what it writes ───────────────────────────────────────────────────────────

def test_the_plan_writes_nothing_but_names_the_paper_too():
    said = []
    db = FakeDb()
    report = fixture.ensure(db, write=False, say=said.append, create=fake_create)
    assert db.writes == []
    assert report["exam_id"] is None
    text = "\n".join(said)
    assert "would create the staged exam" in text
    assert "would create" in text and fixture.SCHOOL_A["name"] in text


class UuidQuery(Query):
    """A Query that refuses a filter value Postgres would refuse.

    `schools.id` is a uuid, so `school_id=eq.planned:school:99990021` is not an
    empty result — it is `22P02 invalid input syntax for type uuid`, which
    aborted the plan on its second line against the real project while the dict
    fake above answered "no rows" and the plan looked fine.
    """

    def eq(self, key, value):
        if isinstance(value, str) and value.startswith("planned:"):
            raise RuntimeError(f"22P02 invalid input syntax for type uuid: {value}")
        return super().eq(key, value)


class UuidDb(FakeDb):
    def table(self, name):
        return UuidQuery(self, name)


def test_the_plan_never_queries_the_database_with_an_id_it_invented():
    said = []
    db = UuidDb()
    report = fixture.ensure(db, write=False, say=said.append, create=fake_create)
    text = "\n".join(said)
    assert db.writes == []
    assert "would create the staged exam" in text
    assert report["exam_id"] is None
    # The outlets a planned school reaches are all still described.
    assert f"would create class {fixture.CLASS_NAME}" in text
    assert any("would create murid" in line for line in said)


def test_a_planned_class_id_never_becomes_a_pupil_s_class_id():
    said = []
    db = FakeDb()
    fixture.ensure(db, write=False, say=said.append, create=fake_create)
    assert db.writes == []
    assert fixture._is_planned(fixture._planned("class", "7A")) is True
    assert fixture._is_planned("3f8b0e4a-1c2d-4e5f-8a9b-0c1d2e3f4a5b") is False


def test_two_schools_two_classes_and_one_subject():
    db, _report = provision()
    assert [s["npsn"] for s in db.data["schools"]] == [
        fixture.SCHOOL_A["npsn"], fixture.SCHOOL_B["npsn"]]
    assert len(db.data["classes"]) == 2
    assert db.data["subjects"][0]["name"] == fixture.SUBJECT


def test_the_identities_sit_the_schools_the_gates_expect():
    db, report = provision()
    profiles = db.data["profiles"]
    assert len(profiles) == 5
    school_a = report["school_a"]
    school_b = report["school_b"]
    by_name = {p["full_name"]: p for p in profiles}
    assert by_name[fixture.IDENTITY_ROWS[0].full_name]["school_id"] == school_a
    for identity in fixture.IDENTITY_ROWS[1:3]:
        profile = by_name[identity.full_name]
        assert profile["school_id"] == school_a
        assert profile["class_id"] == db.data["classes"][0]["id"]
        assert profile["nisn"] == identity.nisn
    outsider = by_name[fixture.IDENTITY_ROWS[3].full_name]
    assert outsider["school_id"] == school_b
    assert {p["role"] for p in profiles} == {"guru", "murid"}


def test_the_account_of_no_school_has_no_school_and_no_class():
    """The fifth identity is the rule, not a pupil: NULL school, no class."""
    db, report = provision()
    noschool = [p for p in db.data["profiles"]
                if p["full_name"] == fixture.IDENTITY_ROWS[4].full_name][0]
    assert fixture.IDENTITY_ROWS[4].key == "noschool"
    assert fixture.IDENTITY_ROWS[4].school == ""
    assert noschool["school_id"] is None
    assert "class_id" not in noschool, (
        "a class would put the account in a school by the back door")
    # It is a real account, not a marker: the gate has to be able to sign in as it.
    assert noschool["id"] in {row["id"] for row in report["identities"]}


def test_a_second_run_finds_the_account_of_no_school_instead_of_copying_it():
    db, _first = provision()
    fixture.ensure(db, write=True, say=quiet, create=fake_create, password="pw2")
    noschool = [p for p in db.data["profiles"]
                if p["full_name"] == fixture.IDENTITY_ROWS[4].full_name]
    assert len(noschool) == 1, "the null-school lookup is what keeps this idempotent"


def test_the_first_sign_in_flag_is_cleared_for_every_account():
    db, _report = provision()
    assert all(p["must_change_password"] is False for p in db.data["profiles"])
    assert all(w[1] == "update" for w in db.writes if w[0] == "profiles")


def test_the_paper_is_published_assigned_and_switched_on():
    db, report = provision()
    exam = db.data["exams"][0]
    assert exam["id"] == report["exam_id"]
    assert exam["scan_live_enabled"] is True
    assert exam["is_published"] is True and exam["status"] == "active"
    assert exam["start_at"] is None and exam["end_at"] is None
    assert exam["class_ids"] == [db.data["classes"][0]["id"]]
    teacher = [p for p in db.data["profiles"] if p["role"] == "guru"][0]
    assert exam["teacher_id"] == teacher["id"]
    assert db.data["teachers"][0]["id"] == teacher["id"]


def test_a_second_run_repairs_rather_than_duplicates():
    db, first = provision()
    report = fixture.ensure(db, write=True, say=quiet, create=fake_create,
                            password="second-pw")
    assert report["exam_id"] == first["exam_id"]
    assert len(db.data["schools"]) == 2
    assert len(db.data["classes"]) == 2
    assert len(db.data["profiles"]) == 5
    assert len(db.data["exams"]) == 1
    # The password is reset to this run's value, and the switch is put back on —
    # "the rows exist" and "the paper can be sat" are different claims.
    assert len(db.passwords_changed) == 5
    assert {pw for _uid, pw in db.passwords_changed} == {"second-pw"}
    assert db.data["exams"][0]["scan_live_enabled"] is True


def test_a_switch_a_teacher_turned_off_is_put_back_on():
    db, _report = provision()
    db.data["exams"][0]["scan_live_enabled"] = False
    fixture.ensure(db, write=True, say=quiet, create=fake_create, password="pw")
    assert db.data["exams"][0]["scan_live_enabled"] is True


# ── what it verifies ─────────────────────────────────────────────────────────

def test_verify_passes_on_what_it_wrote():
    db, report = provision()
    assert fixture.verify(db, report) == []


def test_a_missing_switch_names_migration_071():
    db, report = provision()
    db.data["exams"][0]["scan_live_enabled"] = False
    found = fixture.verify(db, report)
    assert found and "071" in found[0]


def test_a_paper_assigned_to_another_class_is_a_finding():
    db, report = provision()
    db.data["exams"][0]["class_ids"] = ["some-other-class"]
    found = fixture.verify(db, report)
    assert found and "not assigned" in found[0]


def test_a_plan_verifies_nothing_because_it_wrote_nothing():
    db, report = provision(write=False)
    assert fixture.verify(db, report) == []


# ── what it prints ───────────────────────────────────────────────────────────

def test_the_gate_arguments_are_printed_once_with_the_run_password():
    _db, report = provision(password="one-time-pw")
    lines = []
    fixture._print_report(report, say=lines.append)
    text = "\n".join(lines)
    for identity in fixture.IDENTITY_ROWS:
        assert f"--{identity.key}" in text
        assert f"{identity.email}:one-time-pw" in text
    assert report["exam_id"] in text
    for name in ("SCANMEET_TEACHER", "SCANMEET_STUDENT", "SCANMEET_STUDENT2",
                 "SCANMEET_OUTSIDER", "SCANMEET_EXAM_ID"):
        assert name in text


def test_a_plan_prints_no_password():
    _db, report = provision(write=False, password="never-written")
    lines = []
    fixture._print_report(report, say=lines.append)
    text = "\n".join(lines)
    assert "never-written" not in text
    assert "--write" in text


def test_the_password_is_chosen_per_run_and_is_url_safe():
    import secrets as _secrets
    picked = _secrets.token_urlsafe(12)
    _db, report = provision(password=picked)
    # `email:password` is split on the first colon by the gate, so the password
    # must not be able to add one of its own.
    assert ":" not in picked
    assert report["password"] == picked


def test_the_repository_carries_no_fixture_password():
    """A generated password must never reach a file — this is that, checked."""
    root = Path(fixture.__file__).resolve().parents[1]
    for path in list(root.glob("deploy/*.py")) + list(root.glob("tests/unit/*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "scanmeet.invalid:" not in text, f"{path} names a fixture password"


@pytest.mark.parametrize("key", ["teacher", "student", "student2", "outsider"])
def test_every_gate_argument_the_media_gate_asks_for_exists(key):
    from deploy import scanmeet_media_gate as gate
    text = Path(gate.__file__).read_text(encoding="utf-8")
    assert f"--{key}" in text, "the media gate no longer takes this argument"
    assert key in {identity.key for identity in fixture.IDENTITY_ROWS}
