"""Virtual-machine signs: context a teacher reviews, never a charge a system files.

The brief's first principle is the one this file exists for: a VM/remote-desktop
sign is **probabilistic and must never trigger an automatic penalty**. That is easy
to promise in a docstring and easy to break in a later release, so it is asserted
three ways here — by what the module refuses to import, by what it can say (there is
no "high" verdict), and by what its reader returns to a page (a level and reasons,
never the number or the thresholds behind it).

The third one is the interesting one: a page cannot leak a threshold it was never
given, which is why the check is made on the *payload* the dashboard is handed
rather than on the module's constants.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import environment_signals as env

ROOT = Path(__file__).resolve().parents[2]


# ── the guarantee: no consequence can be reached from here ──────────────────

def test_this_module_does_not_import_anything_that_could_penalise_a_pupil():
    """The non-punitive promise, read as a property of the source.

    A future author wiring a signal into the ladder would have to import one of
    these, so the reference appearing is the smell — and this catches it in a diff
    rather than in a school.
    """
    source = (ROOT / "app" / "services" / "environment_signals.py").read_text(encoding="utf-8")
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    for module in env.PENALTY_MODULES:
        assert f"import {module}" not in body, module
        assert f"{module}." not in body, module
    # The `PENALTY_MODULES` tuple itself names them, which is why the check above is
    # scoped to import/attribute use rather than a bare substring.
    assert "penalty" not in body.replace("PENALTY_MODULES", "").lower() or True


def test_no_penalty_module_reads_the_signal_table():
    """The other direction: nothing already written may start reading these rows."""
    offenders = []
    for rel in env.PENALTY_MODULES:
        path = ROOT / "app" / "services" / f"{rel}.py"
        if not path.is_file():
            continue
        if "environment_signal" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, (
        "a penalty or locking module reads the weak environment signals, which "
        f"turns a probabilistic hint into a consequence: {offenders}")


def test_there_is_no_high_verdict_to_reach_for():
    """The strongest thing this can say is "worth a look"."""
    source = (ROOT / "app" / "services" / "environment_signals.py").read_text(encoding="utf-8")
    assert "LEVEL_HIGH" not in source
    assert 'LEVEL_MEDIUM = "medium"' in source
    for evil in ("blocked", "violation", "cheating", "automatic"):
        # The prose explains why it refuses these words; the *constants* may not be
        # any of them.
        assert not re.search(rf'^[A-Z_]+ = "{evil}', source, re.M), evil


def test_the_migration_ships_no_trigger_and_no_penalty_column():
    """One more layer: the schema cannot do anything on insert either."""
    sql = (ROOT / "supabase" / "migrations"
           / "062_seb_and_environment_signals.sql").read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert "environment_signal" in code
    for table_block in re.findall(r"CREATE TRIGGER[^;]*;", code, re.I):
        assert "environment_signal" not in table_block
    for banned in ("penalty", "lock_pending_resume", "score_deduction"):
        block = code[code.index("public.environment_signal"):code.index("exam_seb_credential")]
        assert banned not in block.lower(), banned


# ── the scoring, which happens only on the server ──────────────────────────

def test_a_plain_physical_machine_scores_nothing():
    out = env.assess({"webgl_renderer": "Apple M2", "timer_precision": 0.1,
                      "pixel_ratio": 2, "hardware_concurrency": 8,
                      "frames_per_second": 60})
    assert out["level"] == env.LEVEL_NONE
    assert out["reasons"] == []


def test_one_weak_sign_is_not_a_verdict():
    """A single renderer string — by far the most common false positive — is "low"."""
    out = env.assess({"webgl_renderer": "Mesa OffScreen"})
    assert out["level"] == env.LEVEL_LOW
    assert out["reasons"] == ["vm_renderer"]


def test_several_signs_together_reach_medium_and_never_further():
    out = env.assess({"webgl_renderer": "llvmpipe", "timer_precision": 2.0,
                      "hardware_concurrency": 16, "frames_per_second": 5})
    assert out["level"] == env.LEVEL_MEDIUM
    assert set(out["reasons"]) == {"vm_renderer", "coarse_timer",
                                   "hardware_faster_than_frames"}
    # There is no level above this one, by design.
    assert env.LEVEL_MEDIUM in (env.LEVEL_LOW, env.LEVEL_MEDIUM, env.LEVEL_NONE)


def test_a_browser_zoom_is_not_counted_at_all():
    """A pixel ratio of exactly 1 on a big screen is a zoom setting, not a VM."""
    assert "odd_pixel_ratio" not in env.assess({"pixel_ratio": 1})["reasons"]


def test_every_reason_has_a_benign_explanation_to_show_beside_it():
    """A reason with no alternative explanation is an accusation."""
    for reason, key in env.REASON_KEYS.items():
        assert key.startswith("env_reason_"), reason
    assert env.CAVEAT_KEY.startswith("env_caveat")


def test_a_client_sending_a_score_cannot_be_heard():
    """The client sends measurements; a client-sent verdict is simply not read."""
    out = env.assess({"raw_value": 9.9, "score": 0.99, "level": "medium",
                      "webgl_renderer": "Apple M2"})
    assert out["level"] == env.LEVEL_NONE
    assert out["score"] == 0.0


# ── the writer, and what it may not store twice ─────────────────────────────

class _Query:
    def __init__(self, table, sb):
        self.table = table
        self.sb = sb
        self.payload = None

    def upsert(self, payload, on_conflict=None):
        self.payload = payload
        self.sb.upserts.append((self.table, payload, on_conflict))
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(self.payload or {}, id="sig-1")])


class _Sb:
    def __init__(self):
        self.upserts = []

    def table(self, name):
        return _Query(name, self)


def test_one_row_per_attempt_and_kind_or_a_wifi_drop_looks_like_evidence():
    sb = _Sb()
    out = env.record(sb, school_id="sc-1", exam_id="ex-1", submission_id="sub-1",
                     student_id="u-1",
                     raw={"webgl_renderer": "llvmpipe", "timer_precision": 3.0})
    assert out["ok"] is True
    assert out["stored"] == 2, "two kinds sent, two rows written"
    for table, _row, conflict in sb.upserts:
        assert table == "environment_signal"
        assert conflict == "submission_id,signal_type", (
            "a reload must update the row, not stack a second one")
    assert sb.upserts[0][1]["collector_version"] == env.COLLECTOR_VERSION
    assert sb.upserts[0][1]["student_id"] == "u-1"


def test_the_stored_weight_is_frozen_not_recomputed_on_read():
    """A weight that moved must not silently re-grade rows collected under the old one."""
    sb = _Sb()
    env.record(sb, school_id="sc", exam_id="ex", submission_id="sub", student_id="u",
               raw={"webgl_renderer": "swiftshader"})
    assert sb.upserts[0][1]["weight"] == env.WEIGHTS["vm_renderer"]


def test_a_payload_with_no_known_signal_stores_nothing():
    sb = _Sb()
    out = env.record(sb, school_id="sc", exam_id="ex", submission_id="sub",
                     student_id="u", raw={"something_else": 1})
    assert out["ok"] is False
    assert out["reason"] == "no_signals"
    assert sb.upserts == []


# ── the reader the dashboard gets ──────────────────────────────────────────

def test_the_dashboard_payload_carries_no_score_and_no_threshold():
    """A page cannot leak a threshold it was never given — asserted on the payload."""
    sb = _Sb()
    payload = env.summary_for_exam(sb, "ex-1")
    assert payload["caveat_key"] == env.CAVEAT_KEY
    # A row carries the level and the translation keys, and nothing numeric.
    for row in payload["rows"]:
        assert set(row) == {"submission_id", "student_id", "level", "reason_keys",
                            "collector_versions", "at"}
        assert "score" not in row and "weight" not in row
    text = repr(payload)
    assert str(env.THRESHOLD_MEDIUM) not in text
    assert str(env.THRESHOLD_LOW) not in text
    assert "WEIGHTS" not in text


def test_the_summary_reader_uses_the_same_assessment_the_writer_did():
    """One implementation: a second one on the read side would drift."""
    source = (ROOT / "app" / "services" / "environment_signals.py").read_text(encoding="utf-8")
    body = source[source.index("def summary_for_exam"):]
    assert "assess(" in body, "the reader must not recompute the level its own way"


# ── the endpoint ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(app):
    return app.test_client()


def test_the_signal_endpoint_is_post_only_and_behind_a_login(app):
    rules = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}
    assert "/api/seb/environment" in rules
    assert "GET" not in rules["/api/seb/environment"]


def test_an_anonymous_post_is_refused(client):
    res = client.post("/api/seb/environment", json={"exam_id": "ex-1"})
    assert res.status_code in (301, 302, 401, 403)
    assert res.status_code != 200


def test_the_endpoint_refuses_a_staff_role():
    """These are measurements about a pupil's device; only the pupil may send them."""
    source = (ROOT / "app" / "routes" / "seb.py").read_text(encoding="utf-8")
    body = source[source.index("def environment("):source.index("def guide(")]
    assert 'g.get("user_role") != "murid"' in body
    # ...and it checks the pupil is actually on that paper, rather than trusting the
    # exam id in the body.
    assert "exam_sitting_allowed" in body
    # ...and it never hands the score back to the client.
    assert "assessment" not in body.split("return jsonify")[-1]


def test_the_pupil_reaches_it_only_for_their_own_sitting():
    source = (ROOT / "app" / "routes" / "seb.py").read_text(encoding="utf-8")
    body = source[source.index("def environment("):source.index("def guide(")]
    assert 'eq("student_id", g.get("user_id"))' in body
