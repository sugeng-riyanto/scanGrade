"""A credential-shaped literal is refused before it is pushed, not after.

The defect this guards cost a day. A test fixture written as
`scheme://someone:<password>@db.example.edu` went up in a pull request; GitHub's own
secret scanner failed the check there, which is *after* the branch exists and
after the literal is in a commit — and a follow-up commit that removes it does not
clear the finding, because the finding is in the history. The branch had to be
rewritten and force-pushed.

Nothing about the old checks was wrong; they simply never saw the file. The
pre-commit hook runs the theme gate only when the staged paths match a filter that
did not include `tests/`, so a commit adding one test file was scanned by nothing.

So `deploy/secret_scan.py` runs in the hook **before** that filter, on every
staged path, and reads the index rather than the working tree. These tests are
about three things, in order of importance:

* the hook actually runs it, on every commit — a scanner no commit runs is the
  same defect with more code;
* the *detection* fires on the shape that shipped and stays quiet on the sixteen
  placeholders already in this repository, because a check that fails on
  `postgres:postgres` is a check people learn to bypass;
* the report never prints the secret it is refusing. A tool that blocks a commit
  because a credential exists must not be the second place that credential leaks.

The fixtures here are **assembled from parts** rather than written literally, and
that is not fussiness: a literal would be a finding in this repository's own tree
scan, and an inline exemption here would be the tool exempting its own test from
itself.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import re

import pytest

from tests.unit.git_env import git

REPO = pathlib.Path(__file__).resolve().parents[2]
TOOL = REPO / "deploy" / "secret_scan.py"
HOOK = REPO / "deploy" / "git-hooks" / "pre-commit"
GATE = REPO / "deploy" / "theme_gate.sh"

_spec = importlib.util.spec_from_file_location("secret_scan", TOOL)
secret_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_scan)


def userinfo(user, password):
    """`user:password`, assembled so that no line of this file *is* one.

    The scanner's own fixtures have to be written this way. A literal here is a
    finding in the tree scan, and it is also what GitHub's scanner reads as a
    PostgreSQL credential even with no scheme in front of it — which is how this
    file failed that check once already.
    """
    return f"{user}:{password}"


def credential_url(user="someone", password="hunter2", host="db.example.edu",
                   scheme="https"):
    """The shape that shipped, built rather than written."""
    return f"{scheme}://{userinfo(user, password)}@{host}/v1"


def dsn(user, password, host="db.example.edu", port="5432"):
    """A pooler DSN, assembled for the same reason `userinfo` is."""
    return f"postgresql://{userinfo(user, password)}@{host}:{port}/postgres"


def service_role_jwt(signature="A" * 43):
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.{signature}"


def findings_of(text, path="fixture.py"):
    return secret_scan.scan_text(path, text)


# ── the detector fires ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, detector", [
    (f'dsn = "{credential_url()}"', "credential-url"),
    ("-----BEGIN PRIVATE KEY-----\n" + "A" * 64, "private-key"),
    ('token = "ghp_' + "a" * 36 + '"', "provider-token"),
    ('key = "AKIA' + "B" * 16 + '"', "provider-token"),
    ('auth = "Bearer ' + "c" * 40 + '"', "bearer-token"),
    ('h = {"Authorization": "' + service_role_jwt() + '"}', "service-role-jwt"),
    # A DSN role with its password and no scheme in front: how a credential gets
    # quoted in a docstring. The shape GitHub's scanner failed a pull request for.
    ('"' + userinfo("postgres.abcdef", "p%40ss") + '"', "dsn-credential"),
])
def test_each_shape_is_reported(text, detector):
    found = findings_of(text)
    assert [f.detector for f in found] == [detector], (
        f"the scanner did not report a {detector} in a line that carries one")


def test_the_literal_that_actually_shipped_is_reported():
    """The exact shape that had to be force-pushed out of a branch."""
    text = f'EXAMPLE = "{credential_url("someone", "hunter2")}"'
    found = findings_of(text)
    assert len(found) == 1 and found[0].detector == "credential-url"
    assert found[0].line_no == 1


# ── and stays quiet on what grants nothing ──────────────────────────────────

@pytest.mark.parametrize("line", [
    # The repository's own DSNs: a default role, not a credential.
    'url = f"' + dsn("postgres", "postgres", "aws-1-ap-south-1.pooler.supabase.com") + '"',
    'url = "' + dsn("postgres.example", "pw") + '"',
    'url = "' + credential_url("user", "private", "sfu.example", scheme="wss") + '"',
    # An expansion is read at run time; the committed bytes hold no value.
    'DIRECT_URL=' + dsn("postgres", "${DB_PASSWORD}"),
    'url = f"' + dsn("postgres", "[YOUR-PASSWORD]", "h.pooler.example") + '"',
    # A key header with no body under it is a string, and this repo has one.
    '"private-key": "-----BEGIN PRIVATE KEY-----",',
    # A two-character signature cannot be a signed key.
    '"jwt": "eyJhbGciOiJIUzI1NiJ9.payload.QA",',
    # Not a URL with credentials: an scp-style remote names the account only.
    'remote = "git@github.com:sugeng-riyanto/scanGrade.git"',
    'url = "https://db.example.edu/v1"',
    # A role and a port, which is what `postgres:5432` is everywhere in this repo.
    '"' + userinfo("postgres", "5432") + '"',
])
def test_a_value_that_grants_nothing_is_not_a_finding(line):
    assert findings_of(line) == [], (
        "this line carries no credential, and refusing it is how a guard becomes "
        "something people pass --no-verify to avoid")


def test_a_role_and_a_password_without_a_scheme_is_still_reported():
    """No scheme in front, and still a credential — the shape a docstring quotes."""
    found = findings_of('"' + userinfo("postgres.abcdef", "p%40ss") + '"')
    assert [f.detector for f in found] == ["dsn-credential"]


def test_the_placeholder_rule_is_what_lets_the_repository_pass():
    """Measured: the DSNs this repository already stages are defaults, not leaks."""
    assert secret_scan.is_placeholder_userinfo(userinfo("postgres", "postgres")) is True
    assert secret_scan.is_placeholder_userinfo(userinfo("postgres", "$DB_PASSWORD")) is True
    assert secret_scan.is_placeholder_userinfo(userinfo("postgres", "5432")) is True
    assert secret_scan.is_placeholder_userinfo(userinfo("someone", "hunter2")) is False
    assert secret_scan.is_placeholder_userinfo(
        userinfo("postgres.abcdef", "p%40ss")) is False


# ── the report must not leak what it refuses ────────────────────────────────

def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_excerpt_redacts_every_credential_on_the_line_not_just_the_reported_one(
        tmp_path, capsys):
    """One finding is reported per line; a naive excerpt prints the other one too."""
    write(tmp_path, "two.py",
          f'PRIMARY = "{credential_url("first", "alpha-secret")}"  '
          f'SECONDARY = "{credential_url("second", "beta-secret")}"\n')
    assert secret_scan.main(["--paths", "two.py", "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert err.count("<redacted>") >= 2, (
        "only the reported match was redacted, so the line printed the other one")
    for secret in ("alpha-secret", "beta-secret"):
        assert secret not in err, (
            f"the refusal printed {secret!r} — the report is a second copy of the leak")


def test_the_token_itself_never_reaches_the_output(tmp_path, capsys):
    token = "ghp_" + "z" * 36
    write(tmp_path, "keys.py", f'TOKEN = "{token}"\n')
    assert secret_scan.main(["--paths", "keys.py", "--root", str(tmp_path)]) == 1
    assert token not in capsys.readouterr().err


# ── the allowance has to say why ────────────────────────────────────────────

def test_an_allowance_with_a_reason_suppresses_that_line():
    line = (f'x = "{credential_url()}"  '
            "# secret-scan:allow a DSN fixture the parser is asked to split")
    assert findings_of(line) == []


def test_a_bare_allowance_is_itself_a_finding():
    found = findings_of(f'x = "{credential_url()}"  # secret-scan:allow')
    assert [f.detector for f in found] == ["bare-allowance"], (
        "a suppression with no reason is the shape every forgotten exemption "
        "starts as, so the marker without one is refused rather than honoured")


def test_an_allowance_with_a_one_word_reason_is_refused():
    found = findings_of(f'x = "{credential_url()}"  # secret-scan:allow fixture')
    assert [f.detector for f in found] == ["bare-allowance"]


def test_the_repository_s_only_exemptions_are_the_two_dsn_fixtures():
    """The exemption surface, pinned to what it is supposed to be.

    An exemption is a line the marker actually saves from a finding — prose that
    names the marker is not one, which is why the count is taken over lines that
    carry a credential shape. All of them are fixture DSNs in one test file. A new
    one anywhere fails this and has to be added here deliberately.
    """
    exempting = {}
    for rel, text in secret_scan.tracked(REPO):
        for number, line in enumerate(text.splitlines(), start=1):
            if secret_scan.ALLOW not in line:
                continue
            reason = secret_scan._reason(line)
            if reason is None or len(reason) < secret_scan.MIN_REASON:
                continue          # already refused as a finding, not an exemption
            # `_spans_by_line` on one line keys it 1, whatever its line number.
            if secret_scan._spans_by_line(line).get(1):
                exempting.setdefault(rel, []).append(number)
    assert list(exempting) == ["tests/unit/test_apply_migration.py"], (
        f"the marker exempts lines in {sorted(exempting)}; the only exemptions this "
        f"repository is meant to carry are the two DSN fixtures in "
        f"tests/unit/test_apply_migration.py — update this list deliberately")
    assert len(exempting["tests/unit/test_apply_migration.py"]) == 2


def test_no_exemption_exists_in_the_code_that_serves():
    """Under `app/` a marker is not a fixture, it is a live credential left in place."""
    carriers = [rel for rel, text in secret_scan.tracked(REPO)
                if rel.startswith("app/") and secret_scan.ALLOW in text]
    assert not carriers, (
        f"the exemption marker appears in the code that serves: {carriers} — a "
        f"credential there is a real one, so the fix is to remove the value")


# ── it reads the index, which is the whole reason it can run in a hook ──────

def scratch_repo(tmp_path):
    git("init", "-q", cwd=str(tmp_path), check=True)
    git("config", "user.email", "test@example.invalid", cwd=str(tmp_path), check=True)
    git("config", "user.name", "Fixture", cwd=str(tmp_path), check=True)
    return tmp_path


def test_the_staged_half_is_read_not_the_working_tree(tmp_path):
    """A half-staged file is the case the two disagree, and the index is what ships."""
    repo = scratch_repo(tmp_path)
    path = write(repo, "f.py", f'DSN = "{credential_url()}"\n')
    git("add", "f.py", cwd=str(repo), check=True)
    path.write_text("DSN = None\n", encoding="utf-8")     # the disk is clean now

    staged = secret_scan.staged(repo)
    assert [rel for rel, _ in staged] == ["f.py"]
    assert secret_scan.scan_text(*staged[0]), (
        "the scanner read the working tree and missed what this commit would "
        "actually contain")
    assert secret_scan.main(["--root", str(repo)]) == 1


def test_a_clean_index_exits_zero(tmp_path, capsys):
    repo = scratch_repo(tmp_path)
    write(repo, "f.py", "DSN = None\n")
    git("add", "f.py", cwd=str(repo), check=True)
    assert secret_scan.main(["--root", str(repo)]) == 0
    assert "no credential-shaped literal" in capsys.readouterr().out


def test_a_directory_that_is_not_a_repository_could_not_be_measured(tmp_path, capsys):
    """Exit 2, the box's property — it must not be reported as a finding."""
    assert secret_scan.main(["--root", str(tmp_path)]) == 2
    assert "NOT MEASURED" in capsys.readouterr().err


# ── the wire: does a commit actually run it? ────────────────────────────────

def test_the_hook_runs_the_scan():
    assert "deploy/secret_scan.py" in HOOK.read_text(encoding="utf-8")


def test_the_scan_runs_before_the_path_filter():
    """After the filter, the commit that shipped a credential would run nothing.

    The filter watches templates, stylesheets, `app/` Python, SQL and `deploy/`, so
    a commit touching one file under `tests/` — the commit that actually leaked —
    matches nothing in it. Order is therefore the defect, not a style choice.
    """
    lines = HOOK.read_text(encoding="utf-8").splitlines()
    scan = next(i for i, ln in enumerate(lines) if "deploy/secret_scan.py" in ln
                and "$PY" in ln)
    gate_filter = next(i for i, ln in enumerate(lines)
                       if "grep -qE" in ln and "CHANGED" in ln)
    assert scan < gate_filter, (
        "the credential scan sits behind the staged-path filter, so a commit that "
        "matches none of its patterns — which is how the last one leaked — is "
        "scanned by nothing")


def test_the_scan_is_not_guarded_by_the_staged_path_list():
    """Nothing between the path list being read and the scan may consult it."""
    lines = HOOK.read_text(encoding="utf-8").splitlines()
    listed = next(i for i, ln in enumerate(lines) if ln.startswith("CHANGED="))
    scan = next(i for i, ln in enumerate(lines) if "deploy/secret_scan.py" in ln
                and "$PY" in ln)
    between = lines[listed + 1:scan]
    assert not [ln for ln in between if "grep" in ln or "$CHANGED" in ln], (
        "the scan is conditioned on which paths changed, which is the filter it was "
        "deliberately put before")


def test_the_filter_would_not_have_caught_the_commit_that_leaked():
    """The measured reason the scan cannot be a filtered path, written down."""
    src = HOOK.read_text(encoding="utf-8")
    pattern = re.search(r"grep -qE '([^']+)'", src).group(1)
    assert not re.search(pattern, "tests/unit/test_secret_scan.py")
    assert not re.search(pattern, "tests/unit/test_db_snapshot.py")


def test_the_gate_runs_this_suite():
    """The VPS deploy runs the gate, so the suite is not only a laptop's habit."""
    listed = [ln for ln in GATE.read_text(encoding="utf-8").splitlines()
              if ln.startswith("TESTS=")]
    assert listed and "tests/unit/test_secret_scan.py" in listed[0]
    armed = [ln for ln in GATE.read_text(encoding="utf-8").splitlines()
             if ln.startswith("ARMAMENT=")]
    assert armed and "deploy/secret_scan.py" in armed[0], (
        "the tool is not in the gate's arming list, so a release that deletes it "
        "would drop the check silently instead of refusing (exit 3)")


# ── and this repository is clean, with a floor so "clean" means something ───

def test_the_real_tree_carries_no_credential_shaped_literal(capsys):
    assert secret_scan.main(["--tracked"]) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    scanned = int(re.search(r"(\d+) tracked file", out).group(1))
    assert scanned > 900, (
        f"the scan reports {scanned} files; the tree has far more than that, so "
        f"it read almost nothing and 'clean' means nothing")
