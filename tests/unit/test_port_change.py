"""Guards for `deploy/port_change.py` — the refusal is the tool.

The pattern this replaces was a script per port whose anchor was a string that might have
moved: `if OLD not in text: exit`. Two things were wrong with it. A string that appears
twice is replaced once and reports success; a string that appears nowhere but has a
near-miss leaves the file silently unchanged, which reads exactly like a port. The tool
compares the target's **bytes** to a declared base instead, so every test here is a claim
about what happens to a target that is *not* what the caller said it was.

The checks a port runs are exercised through fake checkouts: a real `tests/` directory
pytest can collect, and a `deploy/mutation_check.py` that speaks the same two-line
`--list` format as the real registry. That shim is a coupling this suite owns on purpose —
`port_change.parse_harnesses` reads a human-readable listing because loading another
checkout's harness module here would import *this* checkout's `sys.path` under another
checkout's name. A change to that format should fail here, not silently discover nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "port_change.py"

BASE = b"def greet():\n    return 'hello'\n"
NEW = b"def greet():\n    return 'hello, world'\n"
DRIFT = b"def greet():\n    return 'hello there'\n\n# somebody else edited this\n"
PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_bad():\n    assert False\n"
RECORDING = (
    "from pathlib import Path\n"
    "\n"
    "def test_ok():\n"
    "    Path(__file__).with_name('SUITE_RAN').write_text('1')\n"
    "    assert True\n"
)

TWO_TESTS = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"
HARNESS_TWO = "print('2/2 injected defects caught')\nraise SystemExit(0)\n"

DOC_BASE = (b"# Notes\n\n"
            b"- **An earlier bullet.** It says `tests/test_suite.py` (9 tests).\n")
DOC_OK = DOC_BASE + b"- **A ported bullet.** Guard: `tests/test_suite.py` (2 tests).\n"
DOC_BAD = DOC_BASE + b"- **A ported bullet.** Guard: `tests/test_suite.py` (3 tests).\n"
DOC_BARE = DOC_BASE + b"- **A ported bullet.** Guard: `tests/test_suite.py` (2).\n"
DOC_MISSING = DOC_BASE + b"- **A ported bullet.** Guard: `tests/test_absent.py` (4 tests).\n"
DOC_SCORE_OK = DOC_BASE + (b"- **A ported bullet.** Checked **2/2 injected defects caught** "
                           b"(`.freebuff/mutate_thing.py`).\n")
DOC_SCORE_BAD = DOC_BASE + (b"- **A ported bullet.** Checked **3/3 injected defects caught** "
                            b"(`.freebuff/mutate_thing.py`).\n")
DOC_NO_NUMBER = DOC_BASE + b"- **A new paragraph with no numbers at all.**\n"
DOC_TWO_CLAIMS = DOC_BASE + (b"- **A ported bullet.** Guard: `tests/test_suite.py` (2 tests), "
                             b"and `tests/test_other.py` (5 tests).\n")

REGISTRY = '''import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "mutation_config.json").read_text())
if "--list" in sys.argv:
    for name, spec in CONFIG.items():
        print("{} \u2014 fake harness".format(name))
        print("    1 mutation(s) against 1 guard file(s): {}".format(", ".join(spec["guards"])))
    raise SystemExit(0)
name = sys.argv[sys.argv.index("--run") + 1]
if name not in CONFIG:
    print("no harness named {}".format(name))
    raise SystemExit(2)
result = CONFIG[name]["result"]
if result == "survivor":
    print("  SURVIVED keep the guard")
    print("\\n{}: 1/2 injected defects caught".format(name))
    raise SystemExit(1)
if result == "stale":
    print("  not injected: keep the guard")
    print("\\n{}: 1/2 injected defects caught".format(name))
    raise SystemExit(1)
print("\\n{}: 2/2 injected defects caught".format(name))
raise SystemExit(0)
'''


def make_checkout(root: Path, name: str, *, body: bytes = BASE, suite: str = PASSING,
                  registry: dict | None = None, convention: str | None = None,
                  relpath: str = "app/thing.py",
                  extra_tests: dict[str, str] | None = None) -> Path:
    checkout = root / name
    target_file = checkout / relpath
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(body)
    (checkout / "tests").mkdir(exist_ok=True)
    (checkout / "tests" / "test_suite.py").write_text(suite, encoding="utf-8")
    for extra, content in (extra_tests or {}).items():
        path = checkout / extra
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if registry is not None:
        (checkout / "deploy").mkdir(exist_ok=True)
        (checkout / "deploy" / "mutation_check.py").write_text(REGISTRY, encoding="utf-8")
        (checkout / "mutation_config.json").write_text(json.dumps(registry), encoding="utf-8")
    if convention is not None:
        (checkout / ".freebuff").mkdir(exist_ok=True)
        (checkout / ".freebuff" / "mutate_thing.py").write_text(convention, encoding="utf-8")
    return checkout


def make_source(root: Path, body: bytes = NEW, relpath: str = "app/thing.py") -> Path:
    source = root / "source"
    path = source / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return source


def make_base(root: Path, body: bytes = BASE) -> Path:
    path = root / "before.py"
    path.write_bytes(body)
    return path


def port(cwd: Path, backup: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(TOOL), *args, "--backup-dir", str(backup)],
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", env=env)


def port_all(root: Path, backup: Path, targets: list[Path], *args: str,
             source: Path | None = None, base: Path | None = None,
             file: str = "app/thing.py") -> subprocess.CompletedProcess:
    cmd = [file, "--from", str(source or make_source(root)),
           "--base", str(base or make_base(root))]
    for target in targets:
        cmd += ["--to", str(target)]
    return port(root, backup, *cmd, *args)


def manifest(backup: Path) -> dict:
    directories = [path for path in backup.iterdir() if path.is_dir()]
    assert len(directories) == 1, f"expected one backup run, found {directories}"
    return json.loads((directories[0] / "manifest.json").read_text(encoding="utf-8"))


def backup_dir(backup: Path) -> Path:
    directories = [path for path in backup.iterdir() if path.is_dir()]
    assert len(directories) == 1, f"expected one backup run, found {directories}"
    return directories[0]


def test_the_base_is_never_inferred_from_the_target(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    backup = tmp_path / "backups"

    proc = port(tmp_path, backup, "app/thing.py", "--from", str(source), "--to", str(target))

    assert proc.returncode == 2
    assert "the base is never inferred" in proc.stderr
    assert (target / "app" / "thing.py").read_bytes() == BASE
    assert not backup.exists()


def test_a_drifted_target_is_refused_before_anything_is_written(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", body=DRIFT)
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 2
    assert (target / "app" / "thing.py").read_bytes() == DRIFT
    assert not backup.exists()
    assert "first difference at line 2" in proc.stdout
    assert "return 'hello there'" in proc.stdout
    assert "refused" in proc.stdout


def test_a_line_ending_only_drift_still_refuses(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", body=BASE.replace(b"\n", b"\r\n"))
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 2, "a CRLF base must not be folded to LF and admitted"
    assert (target / "app" / "thing.py").read_bytes() == BASE.replace(b"\n", b"\r\n")
    assert not backup.exists()


def test_the_port_writes_and_keeps_the_base_in_a_manifest(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", registry={
        "mutate_thing": {"guards": ["app/thing.py"], "result": "caught"},
        "mutate_other": {"guards": ["app/other.py"], "result": "caught"},
    })
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "app" / "thing.py").read_bytes() == NEW
    recorded = manifest(backup)
    assert len(recorded["targets"]) == 1
    entry = recorded["targets"][0]
    assert entry["before_sha256"] == __import__("hashlib").sha256(BASE).hexdigest()
    assert entry["after_sha256"] == __import__("hashlib").sha256(NEW).hexdigest()
    assert (backup_dir(backup) / entry["backup"]).read_bytes() == BASE
    assert "pass" in proc.stdout
    assert "mutate_thing" in proc.stdout and "caught" in proc.stdout
    assert "mutate_other" not in proc.stdout, "a harness for another file was run"


def test_an_already_identical_target_is_not_rewritten_or_backed_up(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", body=NEW)
    target_file = target / "app" / "thing.py"
    stamp = target_file.stat().st_mtime_ns
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target_file.read_bytes() == NEW
    assert target_file.stat().st_mtime_ns == stamp, "an identical target was rewritten"
    assert not backup.exists(), "an identical target was backed up"
    assert "identical" in proc.stdout


def test_one_drifted_target_does_not_block_the_other(tmp_path):
    source = make_source(tmp_path)
    good = make_checkout(tmp_path, "good")
    drifted = make_checkout(tmp_path, "drifted", body=DRIFT)
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [good, drifted])

    assert proc.returncode == 2
    assert (good / "app" / "thing.py").read_bytes() == NEW
    assert (drifted / "app" / "thing.py").read_bytes() == DRIFT
    entries = manifest(backup)["targets"]
    assert [entry["checkout"] for entry in entries] == [str(good)]
    assert "refused" in proc.stdout


def test_a_red_suite_is_a_finding_not_a_pass(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", suite=FAILING)
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (target / "app" / "thing.py").read_bytes() == NEW
    assert "fail" in proc.stdout


def test_a_surviving_mutation_is_a_finding(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", registry={
        "mutate_thing": {"guards": ["app/thing.py"], "result": "survivor"},
    })
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "mutate_thing: survivor 1/2" in proc.stdout


def test_a_stale_anchor_is_a_finding(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", registry={
        "mutate_thing": {"guards": ["app/thing.py"], "result": "stale"},
    })
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "stale" in proc.stdout


def test_a_check_that_cannot_run_is_not_a_pass(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target], "--python", str(tmp_path / "no-python-here"))

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unmeasured" in proc.stdout
    assert (target / "app" / "thing.py").read_bytes() == NEW, "the write still happened"


def test_a_suite_that_collected_nothing_is_not_a_pass(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    (target / "empty").mkdir()
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target], "--suite", "empty")

    assert proc.returncode == 2, "a run that collected nothing was reported as green"
    assert "unmeasured" in proc.stdout


def test_a_broken_registry_is_not_a_silent_none(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    (target / "deploy").mkdir()
    (target / "deploy" / "mutation_check.py").write_text("raise SystemExit(3)\n",
                                                         encoding="utf-8")
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 2, "a registry that will not answer was reported as green"
    assert "discovery" in proc.stdout and "unmeasured" in proc.stdout


def test_a_checkout_without_a_registry_falls_back_to_the_naming_convention(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", convention=(
        "print('2/2 mutations caught')\nraise SystemExit(0)\n"))
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mutate_thing.py: caught 2/2" in proc.stdout
    assert "naming convention" in proc.stdout


def test_a_dry_run_writes_nothing_and_runs_nothing(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one", suite=RECORDING)
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target], "--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "app" / "thing.py").read_bytes() == BASE
    assert not (target / "tests" / "SUITE_RAN").exists(), "a dry run ran the suite"
    assert not backup.exists()
    assert "dry run" in proc.stdout


def test_the_json_report_carries_every_target(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target], "--json")

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["file"] == "app/thing.py"
    assert report["verdict"] == "ported (1 written, 0 already identical)"
    assert [entry["state"] for entry in report["targets"]] == ["written"]
    assert Path(report["backup"]).is_dir()


def test_restore_puts_the_declared_base_back(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    backup = tmp_path / "backups"
    assert port_all(tmp_path, backup, [target]).returncode == 0
    assert (target / "app" / "thing.py").read_bytes() == NEW

    proc = port(tmp_path, backup, "--restore", str(backup_dir(backup)))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "app" / "thing.py").read_bytes() == BASE


def test_restore_refuses_a_file_changed_after_the_port(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    backup = tmp_path / "backups"
    assert port_all(tmp_path, backup, [target]).returncode == 0
    after = target / "app" / "thing.py"
    after.write_bytes(b"# edited after the port\n")

    refused = port(tmp_path, backup, "--restore", str(backup_dir(backup)))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "changed after the port" in refused.stdout
    assert after.read_bytes() == b"# edited after the port\n"

    forced = port(tmp_path, backup, "--restore", str(backup_dir(backup)), "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert after.read_bytes() == BASE


@pytest.mark.skipif(os.name != "posix", reason="mode bits are not a Windows concept")
def test_the_written_file_keeps_its_mode(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    target_file = target / "app" / "thing.py"
    os.chmod(target_file, 0o755)
    backup = tmp_path / "backups"

    assert port_all(tmp_path, backup, [target]).returncode == 0

    assert stat.S_IMODE(target_file.stat().st_mode) == 0o755


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_a_git_base_is_read_from_each_checkout(tmp_path):
    source = make_source(tmp_path)
    target = make_checkout(tmp_path, "one")
    subprocess.run(["git", "init", "-q"], cwd=str(target), check=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "add", "-A"], cwd=str(target), check=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=str(target), check=True)
    backup = tmp_path / "backups"

    clean = port(tmp_path, backup, "app/thing.py", "--from", str(source),
                 "--to", str(target), "--base", "git:HEAD")
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert (target / "app" / "thing.py").read_bytes() == NEW

    (target / "app" / "thing.py").write_bytes(DRIFT)
    drifted = port(tmp_path, backup, "app/thing.py", "--from", str(source),
                   "--to", str(target), "--base", "git:HEAD")
    assert drifted.returncode == 2
    assert "declared base" in drifted.stdout

    unreadable = port(tmp_path, backup, "app/thing.py", "--from", str(source),
                      "--to", str(target), "--base", "git:no-such-revision")
    assert unreadable.returncode == 2
    assert "git show no-such-revision:app/thing.py" in unreadable.stdout


def doc_port(tmp_path, doc, *, target_body=DOC_BASE, suite=TWO_TESTS, extra=(), **kwargs):
    target = make_checkout(tmp_path, "one", body=target_body, suite=suite,
                           relpath="AGENTS.md", **kwargs)
    source = make_source(tmp_path, body=doc, relpath="AGENTS.md")
    backup = tmp_path / "backups"
    proc = port_all(tmp_path, backup, [target], *extra, source=source,
                    base=make_base(tmp_path, target_body), file="AGENTS.md")
    return target, backup, proc


def test_a_claim_that_holds_here_is_measured_here(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_OK)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_OK
    assert "1 of 1 hold" in proc.stdout


def test_a_claim_that_does_not_hold_refuses_the_write(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_BAD)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BASE, "the false sentence was written"
    assert not backup.exists(), "a refused claim still made a backup"
    assert "claims 3 tests" in proc.stdout
    assert "collects 2" in proc.stdout


def test_a_bare_count_is_a_claim_too(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_BARE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BARE
    assert "1 of 1 hold" in proc.stdout


def test_a_claim_naming_a_suite_this_checkout_lacks_refuses(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_MISSING)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BASE
    assert "no such test file" in proc.stdout


def test_a_mutation_score_is_measured_here_too(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_SCORE_OK, convention=HARNESS_TWO)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_SCORE_OK
    assert "1 of 1 hold" in proc.stdout


def test_a_mutation_score_that_does_not_hold_refuses(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_SCORE_BAD, convention=HARNESS_TWO)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BASE
    assert "claims 3/3 caught" in proc.stdout
    assert "reports 2/2" in proc.stdout


def test_only_the_lines_the_port_adds_are_read(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_NO_NUMBER)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_NO_NUMBER
    assert "claim:" not in proc.stdout, "a bullet already in the target was judged as this port's"


def test_a_dry_run_says_the_claims_are_not_checked(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_BAD, extra=("--dry-run",))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BASE
    assert not backup.exists()
    assert "not checked (dry run)" in proc.stdout


def test_claims_can_be_turned_off_explicitly(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_BAD, extra=("--claims", "off"))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BAD
    assert "claim:" not in proc.stdout


def test_a_claim_the_run_could_not_check_is_not_a_pass(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_TWO_CLAIMS,
                                    extra=("--claim-limit", "1"))

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == DOC_BASE
    assert "not checked (limit 1)" in proc.stdout


def test_the_json_report_carries_the_claim_numbers(tmp_path):
    target, backup, proc = doc_port(tmp_path, DOC_OK, extra=("--json",))

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    items = report["targets"][0]["claims"]["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "tests"
    assert items[0]["subject"] == "tests/test_suite.py"
    assert items[0]["claimed"] == 2
    assert items[0]["actual"] == 2
    assert items[0]["state"] == "ok"


def test_an_interpreter_that_cannot_run_is_reported_not_raised(tmp_path):
    target = make_checkout(tmp_path, "one")
    fake = tmp_path / "not-a-python"
    fake.write_text("#!/nonexistent\n", encoding="utf-8")
    backup = tmp_path / "backups"

    proc = port_all(tmp_path, backup, [target], "--python", str(fake))

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cannot run" in proc.stdout
    assert (target / "app" / "thing.py").read_bytes() == NEW, "the write still happened"


def test_each_count_is_read_against_the_path_it_follows(tmp_path):
    doc = DOC_BASE + (
        b"- **A ported bullet.** Guards: `tests/test_suite.py` (2 tests), "
        b"`test_second.py` (1 test), `test_third.py` (1 test).\n")
    target, backup, proc = doc_port(
        tmp_path, doc,
        extra_tests={"tests/test_second.py": "def test_a():\n    assert True\n",
                     "tests/test_third.py": "def test_b():\n    assert True\n"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 of 3 hold" in proc.stdout


def test_a_tool_is_never_the_subject_of_a_test_count(tmp_path):
    """`deploy/port_change.py` is not a suite, so a count beside it is not about it.

    The subject set used to be every path under `tests/`, `.freebuff/` and `deploy/`,
    and a bullet about this tool's own check bound an illustrative `(32 tests)` to the
    tool — collecting nothing, and refusing the port over the tool's own prose.
    """
    doc = DOC_BASE + (
        b"- **A ported bullet.** `deploy/tool.py` is a tool and not a suite, so the "
        b"`(7 tests)` in this sentence is prose about a shape.\n")
    target, backup, proc = doc_port(tmp_path, doc)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == doc
    assert "claim:" not in proc.stdout, "a tool was read as the subject of a test count"


def test_a_count_before_every_module_is_not_a_claim(tmp_path):
    """A count follows the module it counts; a sentence about shapes is not a claim."""
    doc = DOC_BASE + (
        b"- **A ported bullet.** The shape `(7 tests)` is what this sentence is about, "
        b"and `tests/test_suite.py` is where it would land.\n")
    target, backup, proc = doc_port(tmp_path, doc)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (target / "AGENTS.md").read_bytes() == doc
    assert "claim:" not in proc.stdout, "a count read against a path that follows it"
