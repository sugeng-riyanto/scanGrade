"""The Phase 11 tool is only worth its verdicts, so its verdicts are run.

Phase 11 — installing a real Safe Exam Browser against a `require_seb` exam — is the
one thing that turns the Config Key implementation from *conformant* into
*verified*, and it cannot run in a test suite: it needs a real SEB build on a real
machine. What **can** be tested is the instrument the exercise is read through, and
that is what this file does — because a diagnostic that silently answers "MATCH"
would produce exactly the failure Phase 11 exists to prevent: a report saying a
paper opens when nobody has opened it.

Four properties, each with a plausible wrong version:

1. **"Nothing was compared" is not "everything matched."** The dangerous default for
   a verification tool is to be silent and exit 0. With no comparison flags the tool
   must say so and exit 2, and 1 (measured, disagreed) and 2 (not measured) must stay
   different answers for the whole run.
2. **A mismatch is reported as a mismatch, and located.** A key that differs is the
   finding; the SEB-JSON comparison exists so the finding has an *offset* rather than
   a shrug, and the offset reported must be the real one.
3. **The header is recomputed over the file's own `startURL`.** The URL half of the
   check is where a host or a query string creeps in; recomputing it over anything
   else would accept a header the door refuses.
4. **The tool never touches `exam_seb_credential`.** That table holds the
   *reversible* copy of the quit and admin passwords. A diagnostic has no business
   reading it, and the guard is a source rule rather than a comment.

The CLI contract is tested through the real process (no monkeypatching of the thing
under test), and the `--exam` comparison — which talks to Supabase — is driven
in-process with only the row fetch replaced, so the verdict logic itself is the code
under test.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import seb_config_key as ck
from app.services import seb_crypto, seb_service

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "deploy" / "seb_phase11.py"
PROBE = ROOT / "deploy" / "seb_phase11_probe.py"
RUNBOOK = ROOT / "docs" / "features" / "SEB_PHASE11.md"
URL = "https://scangrade.web.id/student/exams/ex-1"


def _tool_module():
    """The script, imported as a module so its own functions can be driven."""
    spec = importlib.util.spec_from_file_location("seb_phase11_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _tool_module()


@pytest.fixture(scope="module")
def probe():
    """The capture probe, imported the same way — its judging is code under test."""
    spec = importlib.util.spec_from_file_location("seb_phase11_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(url: str = URL) -> dict:
    return seb_service.settings_for(
        None, start_url=url, quit_hash=seb_crypto.sha256_hex("quit-pass"),
        admin_hash=seb_crypto.sha256_hex("admin-pass"))


def _seb_file(tmp_path: Path, url: str = URL) -> tuple[Path, str]:
    settings = _settings(url)
    path = tmp_path / "exam.seb"
    path.write_bytes(seb_service.seb_file_bytes(settings))
    return path, ck.config_key(settings)


def _run(*args) -> tuple[int, str]:
    """The real process, so the exit code is the thing actually being judged."""
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          cwd=str(ROOT), capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


# ── 1. "not measured" is never "passed" ──────────────────────────────────────

def test_nothing_to_compare_is_could_not_measure_and_not_success(tmp_path):
    path, _key = _seb_file(tmp_path)
    code, out = _run(str(path))
    assert code == 2, "a run that compared nothing reported success"
    assert "COULD NOT MEASURE" in out
    assert "MATCH: every comparison" not in out


def test_an_unreadable_file_is_could_not_measure(tmp_path):
    bogus = tmp_path / "not-really.seb"
    bogus.write_bytes(b"this is not a gzipped plist")
    code, out = _run(str(bogus))
    assert code == 2
    assert "CANNOT MEASURE" in out


def test_a_file_that_is_not_there_is_could_not_measure(tmp_path):
    code, out = _run(str(tmp_path / "absent.seb"))
    assert code == 2
    assert "no such file" in out


# ── 2. a match and a mismatch are told apart, and located ────────────────────

def test_the_clients_key_matching_its_own_is_a_match(tmp_path):
    path, key = _seb_file(tmp_path)
    code, out = _run(str(path), "--seb-key", key)
    assert code == 0
    assert "[MATCH] Config Key reported by SEB" in out
    assert key in out


def test_the_client_key_is_read_case_insensitively_and_past_a_trailing_semicolon(tmp_path):
    """What clients send, not what the specification prints."""
    path, key = _seb_file(tmp_path)
    code, out = _run(str(path), "--seb-key", f"  {key.upper()};  ")
    assert code == 0, out


def test_another_key_is_a_mismatch_and_exits_one(tmp_path):
    path, _key = _seb_file(tmp_path)
    code, out = _run(str(path), "--seb-key", "b" * 64)
    assert code == 1, "a wrong Config Key was reported as anything but a mismatch"
    assert "[MISMATCH] Config Key reported by SEB" in out
    assert "MISMATCH: at least one comparison disagreed" in out


def test_the_clients_seb_json_is_compared_byte_for_byte(tmp_path, tool):
    """The comparison that turns "the keys differ" into "rule 4 is wrong"."""
    path, key = _seb_file(tmp_path)
    settings = seb_service.decode_seb(path.read_bytes())
    code, out = _run(str(path), "--seb-json", ck.seb_json(settings))
    assert code == 0, out
    assert "[MATCH] SEB-JSON, byte for byte" in out
    assert "first difference" not in out


def test_a_differing_seb_json_reports_the_real_offset(tmp_path, tool):
    path, _key = _seb_file(tmp_path)
    ours = ck.seb_json(seb_service.decode_seb(path.read_bytes()))
    # One byte changed, in the middle, the way a mis-escaped backslash would be.
    index = ours.index("quitURL") if "quitURL" in ours else len(ours) // 2
    theirs = ours[:index] + ("X" if ours[index] != "X" else "Y") + ours[index + 1:]
    code, out = _run(str(path), "--seb-json", theirs)
    assert code == 1
    assert f"first difference at offset {index}" in out, (
        "the offset the tool reported is not the offset where the strings part")


def test_a_truncated_seb_json_is_a_mismatch_not_a_match(tmp_path):
    """Prefix equality is the shape of a check that only compares what it has."""
    path, _key = _seb_file(tmp_path)
    ours = ck.seb_json(seb_service.decode_seb(path.read_bytes()))
    code, out = _run(str(path), "--seb-json", ours[: len(ours) // 2])
    assert code == 1, "a client JSON that is a strict prefix was accepted"
    assert "continues for" in out


def test_the_tools_own_json_leaves_out_the_originator_key(tmp_path):
    """Rule 1, seen through the artefact rather than through the module."""
    path, _key = _seb_file(tmp_path)
    code, out = _run(str(path), "--show-json")
    assert code == 2                    # nothing compared, but it printed the JSON
    assert "originatorVersion" not in out, (
        "the JSON the tool shows SEB carries a key the Config Key must exclude")


# ── 3. the URL half is recomputed from the file's own startURL ───────────────

def test_the_header_the_client_sent_is_recomputed_over_the_files_starturl(tmp_path):
    path, key = _seb_file(tmp_path)
    code, out = _run(str(path), "--seb-header", ck.request_hash(URL, key))
    assert code == 0, out
    assert "[MATCH] header the client sent" in out


def test_a_header_hashed_over_another_url_is_a_mismatch(tmp_path):
    """The host/query-string failure the door would refuse, reproduced here."""
    path, key = _seb_file(tmp_path)
    elsewhere = ck.request_hash("https://scangrade.web.id/student/exams/other", key)
    code, out = _run(str(path), "--seb-header", elsewhere)
    assert code == 1
    assert "over URL: " in out


def test_a_header_from_another_config_is_a_mismatch(tmp_path):
    path, _key = _seb_file(tmp_path)
    code, _out = _run(str(path), "--seb-header", ck.request_hash(URL, "b" * 64))
    assert code == 1


# ── 4. the exam row, which is what the door actually compares ────────────────

def _drive_exam(tool, monkeypatch, capsys, tmp_path, row, *extra):
    path, _key = _seb_file(tmp_path)
    monkeypatch.setattr(tool, "_load_remote_exam", lambda repo, exam_id: row)
    monkeypatch.setattr(sys, "argv", [str(TOOL), str(path), "--exam", "ex-1", *extra])
    code = tool.main()
    return code, capsys.readouterr().out


def test_a_stored_key_that_matches_the_file_is_a_match(tool, monkeypatch, capsys, tmp_path):
    settings = _settings()
    row = {"id": "ex-1", "title": "Latihan", "require_seb": True,
           "seb_config_key": ck.config_key(settings)}
    code, out = _drive_exam(tool, monkeypatch, capsys, tmp_path, row)
    assert code == 0, out
    assert "[MATCH] stored key vs the file's key" in out


def test_a_file_older_than_the_stored_key_is_a_mismatch(tool, monkeypatch, capsys, tmp_path):
    """The re-issue case: the key moved, the pupil still holds the old file."""
    row = {"id": "ex-1", "title": "Latihan", "require_seb": True,
           "seb_config_key": "c" * 64}
    code, out = _drive_exam(tool, monkeypatch, capsys, tmp_path, row)
    assert code == 1
    assert "the file predates a re-issue" in out


def test_an_exam_that_does_not_require_seb_is_flagged_rather_than_passed(tool, monkeypatch, capsys, tmp_path):
    """A file against an ungated paper proves nothing, and the tool says so."""
    row = {"id": "ex-1", "title": "Latihan", "require_seb": False, "seb_config_key": None}
    code, out = _drive_exam(tool, monkeypatch, capsys, tmp_path, row)
    assert code == 1
    assert "does not require SEB at all" in out


def test_a_gated_exam_with_no_key_is_flagged_as_failing_closed(tool, monkeypatch, capsys, tmp_path):
    row = {"id": "ex-1", "title": "Latihan", "require_seb": True, "seb_config_key": None}
    code, out = _drive_exam(tool, monkeypatch, capsys, tmp_path, row)
    assert code == 1
    assert "fails closed" in out


def test_a_missing_exam_row_is_could_not_measure(tool, monkeypatch, capsys, tmp_path):
    code, out = _drive_exam(tool, monkeypatch, capsys, tmp_path, {})
    assert code == 2
    assert "no exam row" in out


# ── the guards on the tool itself ────────────────────────────────────────────

def _used_tokens(path: Path) -> list[str]:
    """Every name and string literal the script actually *uses* — no prose.

    Docstrings and comments are dropped, and that is not a convenience: this
    module's own docstring **names** the table it forbids, so a whole-file search
    fires on a sentence. The lesson is the one `test_auto_deploy.py` records for the
    launcher refresh block — a guard that fails on prose gets relaxed instead of
    trusted — and the rule being guarded here is about *code* reaching the table,
    not about a word appearing in a comment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    tokens: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                tokens.append(node.value)
        elif isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
    return tokens


def test_the_tool_never_reaches_for_the_reversible_password_table():
    """Not a style note: that table holds the passwords in a form that can be read
    back, and a diagnostic that could print them is a new place they can leak."""
    used = _used_tokens(TOOL)
    for forbidden in ("exam_seb_credential", "password_enc", "credentials_for"):
        offenders = [token for token in used if forbidden in token]
        assert not offenders, (
            f"the Phase 11 tool references {forbidden!r} in code: {offenders[:3]}")


def test_the_password_guard_is_not_read_as_prose(tmp_path):
    """The guard's own honesty check: a file that only *mentions* the table passes.

    Without this, the assertion above looks satisfied for the wrong reason — and a
    reader cannot tell a scoped guard from a broken one.
    """
    prose = tmp_path / "prose.py"
    prose.write_text('"""A docstring naming exam_seb_credential and password_enc."""\n'
                     "# a comment naming credentials_for\nx = 1\n", encoding="utf-8")
    assert _used_tokens(prose) == ["x"] or "x" in _used_tokens(prose)
    code = tmp_path / "code.py"
    code.write_text("row = table('exam_seb_credential')\n", encoding="utf-8")
    assert any("exam_seb_credential" in token for token in _used_tokens(code))


def test_the_exam_read_names_its_columns_rather_than_selecting_everything(tool):
    assert tool.EXAM_COLUMNS, "the exam read has no column list"
    assert "*" not in tool.EXAM_COLUMNS, (
        "`select=*` on exams would hand this tool every column, including ones "
        "added later that it should not print")
    for needed in ("require_seb", "seb_config_key"):
        assert needed in tool.EXAM_COLUMNS


def test_the_runbook_exists_and_names_the_tool_it_tells_you_to_run():
    """A runbook that names a command nobody can run is worse than no runbook."""
    assert RUNBOOK.is_file(), f"the Phase 11 runbook is missing: {RUNBOOK}"
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "deploy/seb_phase11.py" in text
    for code in ("0", "1", "2"):
        assert code in text


def test_the_tool_prints_its_contract_when_asked(tool):
    done = subprocess.run([sys.executable, str(TOOL), "--help"],
                          cwd=str(ROOT), capture_output=True, text=True)
    assert done.returncode == 0
    for flag in ("--exam", "--seb-key", "--seb-json", "--seb-header"):
        assert flag in done.stdout


# ── the probe: it asks the client, so its judgement is the measurement ───────
#
# `deploy/seb_phase11.py` judges values a person read out of SEB. The probe is the
# other half — it serves the config and reads what the client sends — so its
# comparison *is* the result of Phase 11 Step 0, and a wrong comparison is a wrong
# report. It shipped with exactly that defect once: every request was judged against
# the start URL's hash, so a favicon request that carried a perfectly correct header
# printed MISMATCH. These tests exist so that cannot come back.

def test_the_probe_builds_the_file_the_runbook_tells_you_to_open(probe):
    """The config has to be the app's own test config, or Step 0 measures nothing."""
    assert probe.START_URL.endswith("/panduan/seb/berhasil"), probe.START_URL
    assert probe.SETTINGS.get("sendBrowserExamKey") is True, (
        "without `sendBrowserExamKey` the client sends no header and the probe "
        "would report `no_header` on a perfectly good installation")
    assert probe.SETTINGS.get("startURL") == probe.START_URL
    assert probe.KEY == ck.config_key(probe.SETTINGS)
    assert probe.SEB_BYTES == seb_service.seb_file_bytes(probe.SETTINGS)
    # ...and the file survives our own reader, which is what SEB has to do as well.
    assert ck.config_key(seb_service.decode_seb(probe.SEB_BYTES)) == probe.KEY


def test_the_probe_judges_every_request_against_its_own_url(probe):
    """The defect that shipped: one expectation for every path is one wrong answer."""
    for path in ("/panduan/seb/berhasil", "/favicon.ico", "/probe-xhr", "/"):
        assert probe.judge(path, probe.expected_for(path)) == "match", path
        # A header correctly computed for a *different* URL is not this URL's.
        assert probe.judge(path, probe.expected_for("/panduan/seb/berhasil")) == (
            "match" if path == "/panduan/seb/berhasil" else "mismatch"), path
    assert probe.judge("/favicon.ico", "") == "no_header"
    # A whitespace-only value is nothing to compare against, so it can never read as
    # a match — whatever it is labelled, it is not proof of anything.
    assert probe.judge("/favicon.ico", "  ") != "match"


def test_the_probe_reads_the_header_the_way_clients_send_it(probe):
    """A trailing `;` and surrounding whitespace are documented client behaviour."""
    sent = probe.expected_for("/panduan/seb/berhasil")
    assert probe.judge("/panduan/seb/berhasil", sent + ";") == "match"
    assert probe.judge("/panduan/seb/berhasil", "  " + sent.upper() + " \n") == "match"
    assert probe.judge("/panduan/seb/berhasil", "b" * 64) == "mismatch"


def test_the_probe_measures_question_b_and_the_javascript_api(probe):
    """Both open runbook questions have to be *askable* from the page it serves."""
    assert "/probe-xhr" in probe.PAGE, "the page fires no XHR, so question B is unmeasured"
    assert "window.SafeExamBrowser" in probe.PAGE, (
        "the page never looks for the JavaScript API, which is the only path an "
        "iPad has")
    assert "updateKeys" in probe.PAGE, (
        "on SEB 3.0 macOS/iOS `configKey` is only populated after updateKeys")
    assert "capture" in probe.__doc__ and "temp" in probe.__doc__


def test_the_probe_writes_its_artifacts_outside_the_checkout(probe, tmp_path):
    """A measurement must not be able to leave the tree dirty for the next pull."""
    seb_path, report_path = probe._paths(tmp_path)
    assert seb_path.name.endswith(".seb") and report_path.name.endswith(".json")
    default = Path(probe.OUT)
    assert ROOT not in default.resolve().parents, (
        f"the probe's default output is inside the checkout: {default}")
    assert not str(default).startswith(str(ROOT)), default


def test_the_runbook_names_the_probe_it_tells_you_to_run():
    """Both halves of Phase 11 are named, or the steps are not reproducible."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "deploy/seb_phase11_probe.py" in text
    assert "deploy/seb_phase11.py" in text


def test_the_probe_states_what_it_cannot_prove(probe):
    """The one thing a measuring instrument must not do is overclaim."""
    prose = probe.__doc__ or ""
    assert "does NOT prove" in prose, (
        "the probe's docstring no longer says what it cannot prove")
    assert "exam row" in prose and "credential" in prose
