"""Every long-lived process on the box, and the deploy's one question for all of them.

`served_commit_gate.py` asks gunicorn which commit it is serving and
`worker_commit_gate.py` asks the Celery worker. Those two are *hand-wired* — each is
its own gate, its own fence, its own command in the runner — which means the
arrangement was true on the day it was written and nothing keeps it true. A third
long-lived unit added to the box (a scheduler, a helper with no HTTP surface) would
grep its release into memory at start-up and hold it across every reload, and no gate
would ask it, because no gate knows it exists.

So the roster is *closed*: `deploy/long_lived.py` names the units that run this
checkout and how each is asked, and a unit discovered on the box that the roster does
not carry is a **refusal** on the release, not a shrug. A helper cannot be added to
the box without the deploy noticing it.

These tests hold that rule from both ends: the pure reading (what does a unit file
say, what is uncovered, what do the answers mean), and the wiring (the runner has a
`process-commit-gate` block, the deploy-status page has a sentence for its refusal,
and the gate ships the roster it judges).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GATE_PY = DEPLOY / "process_commit_gate.py"
ROSTER_PY = DEPLOY / "long_lived.py"
ATTEST_PY = ROOT / "app" / "utils" / "process_attest.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: `long_lived.py` defines a dataclass, and dataclasses
    # resolves its string annotations through `sys.modules[cls.__module__]`. A
    # module that exists only as a local name resolves to None and raises.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def roster_mod():
    return _load("long_lived", ROSTER_PY)


@pytest.fixture(scope="module")
def gate_mod():
    return _load("process_commit_gate", GATE_PY)


@pytest.fixture(scope="module")
def attest_mod():
    return _load("process_attest", ATTEST_PY)


# ── the roster knows the box's processes ─────────────────────────────────────

def test_the_roster_names_the_app_and_the_worker(roster_mod):
    units = {p.unit for p in roster_mod.ROSTER}
    assert units == {"scangrade", "scangrade-celery"}, (
        "the roster is the one place that says which units run this checkout; if it "
        "does not name the app and the worker, it is describing a different box")


def test_every_rostered_process_says_how_it_is_asked(roster_mod):
    for proc in roster_mod.ROSTER:
        assert proc.ask in {"http", "celery", "attestation"}, proc
        assert proc.action in {"reload", "restart"}, proc
        assert proc.unit, proc
        # The fence that proves the release ships the reading — the app and the
        # worker each carry one; a generic helper carries its own call site marker.
        assert proc.how.get("marker", "").endswith(":start"), proc


# ── discovery: which unit files run this checkout ────────────────────────────

def test_a_unit_working_in_the_checkout_is_discovered(roster_mod):
    text = "[Service]\nUser=scangrade\nWorkingDirectory=/opt/scangrade\n"
    assert roster_mod.unit_runs_checkout(text, "/opt/scangrade")


def test_a_unit_whose_command_lives_in_the_checkout_is_discovered(roster_mod):
    text = ("[Service]\n"
            "ExecStart=/opt/scangrade/.venv/bin/gunicorn wsgi:app\n")
    assert roster_mod.unit_runs_checkout(text, "/opt/scangrade")


def test_a_unit_that_does_not_touch_the_checkout_is_not_discovered(roster_mod):
    text = ("[Service]\n"
            "WorkingDirectory=/srv/other\n"
            "ExecStart=/usr/bin/redis-server /etc/redis.conf\n")
    assert not roster_mod.unit_runs_checkout(text, "/opt/scangrade")


def test_discovery_reads_the_unit_directory(tmp_path, roster_mod):
    (tmp_path / "scangrade.service").write_text(
        "[Service]\nWorkingDirectory=/opt/scangrade\n", encoding="utf-8")
    (tmp_path / "redis.service").write_text(
        "[Service]\nExecStart=/usr/bin/redis-server\n", encoding="utf-8")
    found = roster_mod.discover(tmp_path, "/opt/scangrade")
    assert found == ["scangrade"]


# ── the coverage rule: an unknown unit refuses the release ───────────────────

def test_a_discovered_unit_nobody_knows_is_uncovered(roster_mod):
    got = roster_mod.uncovered(["scangrade", "scangrade-celery", "sg-helper"])
    assert got == ["sg-helper"], (
        "a new long-lived unit running this checkout must not be silently absent "
        "from every gate")


def test_the_known_units_are_covered(roster_mod):
    assert roster_mod.uncovered(["scangrade", "scangrade-celery"]) == []


def test_an_exempted_unit_is_covered_and_says_why(roster_mod):
    # Whatever is exempted carries a reason a reader can act on; an exemption with
    # no sentence is the same as forgetting.
    assert roster_mod.EXEMPT, "there is at least the oneshot runner to exempt"
    for unit, why in roster_mod.EXEMPT.items():
        assert why and len(why) > 20, unit


# ── the verdict ──────────────────────────────────────────────────────────────

def test_an_uncovered_unit_refuses_and_names_it(gate_mod):
    verdict, why = gate_mod.judge_all(
        uncovered=["sg-helper"], per_process=[("app", "ok", "serving")], merged="a" * 40)
    assert verdict == gate_mod.REJECTED if hasattr(gate_mod, "REJECTED") else \
        verdict == "mismatch"
    assert "sg-helper" in why


def test_all_processes_on_the_merged_commit_pass(gate_mod):
    verdict, _ = gate_mod.judge_all(
        uncovered=[], merged="a" * 40,
        per_process=[("app", "ok", "serving"), ("worker", "ok", "running")])
    assert verdict == "ok"


def test_a_process_on_another_commit_refuses(gate_mod):
    verdict, why = gate_mod.judge_all(
        uncovered=[], merged="a" * 40,
        per_process=[("app", "ok", "serving"), ("worker", "mismatch", "running bbbbbbb")])
    assert verdict == "mismatch"
    assert "worker" in why


def test_no_commit_to_compare_is_unmeasured(gate_mod):
    verdict, _ = gate_mod.judge_all(
        uncovered=[], per_process=[("app", "ok", "serving")], merged="")
    assert verdict == "unmeasured"


def test_a_process_that_cannot_be_asked_is_unmeasured_not_refused(gate_mod):
    # A helper that is down and one built before the reading are the same answer;
    # neither is evidence about *this* release.
    verdict, _ = gate_mod.judge_all(
        uncovered=[], merged="a" * 40,
        per_process=[("app", "ok", "serving"), ("worker", "unmeasured", "no worker")])
    assert verdict == "unmeasured"


# ── the generic reporter ─────────────────────────────────────────────────────

def test_an_attestation_names_the_commit_and_the_pid(tmp_path, attest_mod):
    payload = attest_mod.publish(
        "sg-helper", state_dir=tmp_path,
        snapshot={"available": True, "full_commit": "a" * 40, "commit": "aaaaaaa"})
    assert payload["full_commit"] == "a" * 40
    assert payload["pid"] > 0
    assert (tmp_path / "processes" / "sg-helper.json").is_file()


def test_an_attestation_that_cannot_name_its_commit_still_writes_a_reason(tmp_path, attest_mod):
    payload = attest_mod.publish(
        "sg-helper", state_dir=tmp_path,
        snapshot={"available": False, "reason_key": "not_a_checkout"})
    assert payload["available"] is False
    assert payload["reason_key"] == "not_a_checkout"


def test_reading_an_attestation_returns_its_commit(tmp_path, gate_mod, attest_mod):
    attest_mod.publish("sg-helper", state_dir=tmp_path,
                       snapshot={"available": True, "full_commit": "b" * 40,
                                 "commit": "bbbbbbb"})
    # The reading is tied to the pid that wrote it, so a live process is read with
    # its own id — the same id `systemctl show -p MainPID` would hand the gate.
    state, commit, _ = gate_mod.read_attestation(
        tmp_path / "processes" / "sg-helper.json", pid=os.getpid())
    assert state == "ok"
    assert commit == "b" * 40


def test_an_attestation_from_a_dead_process_does_not_count(tmp_path, gate_mod, attest_mod):
    attest_mod.publish("sg-helper", state_dir=tmp_path,
                       snapshot={"available": True, "full_commit": "b" * 40})
    state, _, why = gate_mod.read_attestation(
        tmp_path / "processes" / "sg-helper.json", pid=999999)
    assert state == "unmeasured"
    assert "pid" in why.lower()


def test_a_missing_attestation_is_unmeasured(tmp_path, gate_mod):
    state, _, _ = gate_mod.read_attestation(tmp_path / "processes" / "gone.json", pid=1)
    assert state == "unmeasured"


# ── the runner asks every process, and the page can say so ───────────────────

def test_the_runner_has_a_process_block_after_the_worker():
    script = (DEPLOY / "scangrade-deploy.sh").read_text(encoding="utf-8-sig")
    assert "# process-commit-gate:start" in script and \
        "# process-commit-gate:end" in script, (
        "the runner must ask every long-lived process, with liftable delimiters")
    assert "deploy/process_commit_gate.py" in script
    assert script.index("# worker-commit-gate:end") < script.index(
        "# process-commit-gate:start"), (
        "the process gate asks the *other* processes, so it follows the worker gate")


def test_the_runner_restarts_every_rostered_unit():
    script = (DEPLOY / "scangrade-deploy.sh").read_text(encoding="utf-8-sig")
    assert "deploy/long_lived.py" in script, (
        "the restart loop reads the roster, so a new unit is restarted by being "
        "in the roster rather than by a second edit the deploy does not have")


def test_the_page_can_name_a_process_refusal():
    service = (ROOT / "app" / "services" / "deploy_status_service.py").read_text(
        encoding="utf-8-sig")
    assert '"process_commit"' in service, (
        "a refusal the page cannot name is a refusal an operator cannot read")
    assert '"processes"' in service, (
        "the run step must be named, or the page shows a raw slug")


def test_the_template_says_the_process_refusal_in_both_languages():
    template = (ROOT / "app" / "templates" / "super_admin" /
                "deploy_status.html").read_text(encoding="utf-8-sig")
    assert "q.gate_key == 'process_commit'" in template
    # `t('id','en')` — both languages present on the same line.
    line = [l for l in template.splitlines() if "q.gate_key == 'process_commit'" in l][0]
    assert "t('" in line and "','" in line, line


def test_the_gate_ships_the_roster_it_judges():
    gate = GATE_PY.read_text(encoding="utf-8-sig")
    assert "long_lived" in gate, (
        "the gate must judge the roster rather than a second copy of it")
