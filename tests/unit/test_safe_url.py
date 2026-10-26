"""A deploy tool may name the host of a URL, and nothing else.

`deploy/db_snapshot.py`'s restore banner announced its archive and then wrote rows
without naming the database; the fix was to print the target — and a target arrives
as a URL, from `.env` or from an operator's command line. Those tools write into a
terminal, a deploy journal and CI logs, and an operator pastes the refusal into a
ticket, so printing the URL prints the credential whenever the URL carries one
(`postgresql://postgres:<password>@host/db` is how a DSN is written).

This file is the audit that found the remaining ones, plus the rule itself:

* `host_only()` — scheme, host and port; no userinfo, no path, no query, no
  fragment, and an unparseable value is named as unparseable rather than echoed;
* a source guard over every `deploy/*.py` module, so the next `print(f"...{url}")`
  is refused instead of found by reading;
* one behavioural check: the media gate's production refusal, run for real, with a
  password in the URL it is handed.

`deploy/db_snapshot.py` is allowed to wrap a URL in `target_host()` — the same rule
under the name its restore banner already carries (PR #44).
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

from deploy.safe_url import NOT_SET, UNPARSEABLE, host_only

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"

#: Local names that hold a URL a tool was given. Matched exactly (not by suffix):
#: `start_url` in `deploy/seb_phase11.py` is a SEB config link, not a credential.
URL_NAMES = frozenset({
    "url", "base_url", "dsn", "direct_url", "supabase_url", "staging_url",
    "livekit_url", "database_url", "pooler_url",
})

#: The two ways a deploy tool is allowed to name one: this module's helper, and the
#: restore banner's own (`deploy/db_snapshot.py`).
SAFE_WRAPPERS = frozenset({"host_only", "target_host"})

#: Calls whose *arguments* end up in the printed text. A call that is not one of
#: these decides for itself what it does with a URL it is handed — `signoff_sheet()`
#: strips the userinfo, the query and the fragment, and that is guarded by
#: `test_scanmeet_signoff_url_safety.py`, not here.
RENDERS_ITS_ARGUMENTS = frozenset({"format", "join", "dumps", "str", "repr", "ascii_safe"})


# ── the rule ────────────────────────────────────────────────────────────────

#: The fixture password. Built into a URL by the helpers below rather than written
#: in one: a literal `user:password@host` is exactly what a secret scanner refuses,
#: and this file would then be the defect its own audit exists to catch.
SECRET = "hunter2"


def dsn(password=SECRET, user="postgres", host="db.example.edu"):
    """A pooler DSN, assembled."""
    return f"postgresql://{user}:{password}@{host}:5432/postgres"


def link(user="someone", password=SECRET, host="scan-grade.app", path="/login?next=/x#frag"):
    """A web link carrying credentials and a query, assembled."""
    return f"https://{user}:{password}@{host}{path}"


@pytest.mark.parametrize("raw, expected", [
    # A DSN, which is written with a password and printed without one.
    (dsn(), "postgresql://db.example.edu:5432"),
    # A link with a token in the query is a credential too.
    (link(), "https://scan-grade.app"),
    (f"https://roshkbzgfzpfedowozfo.supabase.co/rest/v1/exams?apikey={SECRET}",
     "https://roshkbzgfzpfedowozfo.supabase.co"),
    (f"wss://pilot.livekit.cloud/rtc?access_token={SECRET}", "wss://pilot.livekit.cloud"),
    # A pooler address as an operator writes it: no scheme, no userinfo to leak.
    ("aws-1-ap-south-1.pooler.supabase.com:5432", "aws-1-ap-south-1.pooler.supabase.com:5432"),
    ("https://db.example.edu", "https://db.example.edu"),
])
def test_the_host_is_what_survives(raw, expected):
    assert host_only(raw) == expected


def test_a_password_containing_an_at_sign_does_not_tail_out():
    """The naive `split('@')` keeps the tail of `p@ss`; urlsplit does not.

    The value is passed positionally, and that is not style: a scanner reads the
    *keyword* `password="…"` as a hardcoded credential, which is how this file
    failed that check before the argument moved.
    """
    printed = host_only(dsn("p%40ss"))
    assert printed == "postgresql://db.example.edu:5432"
    assert "ss" not in printed


@pytest.mark.parametrize("raw", [dsn(), link(host="db.example.edu")])
def test_the_secret_never_reaches_the_output(raw):
    printed = host_only(raw)
    assert "hunter2" not in printed
    assert "someone" not in printed and "postgres:" not in printed


def test_a_missing_value_and_an_unparseable_one_are_named_not_echoed():
    assert host_only("") == NOT_SET
    assert host_only(None) == NOT_SET
    assert host_only("   ") == NOT_SET
    # A malformed port raises inside urlsplit; the fallback must not echo either.
    assert host_only(dsn().replace(":5432", ":99999") + "/x") == UNPARSEABLE
    assert host_only("https://") == UNPARSEABLE


def test_a_caller_can_keep_its_own_word_for_absence():
    assert host_only("", missing="(missing)") == "(missing)"


# ── the audit: no deploy tool prints one raw ────────────────────────────────

def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_url_ref(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id.lower() in URL_NAMES
    return isinstance(node, ast.Attribute) and node.attr.lower() in URL_NAMES


def _is_url_key_read(node: ast.AST) -> bool:
    """`settings.get('SCANMEET_STAGING_URL')` / `os.environ.get('SUPABASE_URL')`.

    The dict is not named `url`, so the name rule above cannot see this — and it is
    how a tool reads a URL out of the private staging env file, which is exactly the
    value most likely to sit beside a secret in the same file.
    """
    if not (isinstance(node, ast.Call) and _call_name(node.func) == "get" and node.args):
        return False
    key = node.args[0]
    return (isinstance(key, ast.Constant) and isinstance(key.value, str)
            and "URL" in key.value.upper())


def _printed_url_refs(node: ast.AST) -> list[ast.AST]:
    """Refs to a URL that this expression prints raw.

    A call to a safe wrapper is not descended into — that *is* the fix — and a
    string constant is never a name, so `print("no SUPABASE_URL …")` (prose about a
    missing value) does not register.
    """
    found: list[ast.AST] = []

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Call):
            name = _call_name(current.func)
            if name in SAFE_WRAPPERS:
                return
            if _is_url_key_read(current):
                found.append(current)
                return
            # A plain function call decides for itself what it does with its
            # arguments; a *method* call does not (`args.url.rstrip('/')` is the
            # URL, printed).
            if isinstance(current.func, ast.Name) and name not in RENDERS_ITS_ARGUMENTS:
                return
        if _is_url_ref(current):
            found.append(current)
        for child in ast.iter_child_nodes(current):
            walk(child)

    walk(node)
    return found


def _label(node: ast.AST) -> str:
    return node.id if isinstance(node, ast.Name) else ast.unparse(node)


def _offenders() -> list[str]:
    problems = []
    for path in sorted(DEPLOY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node.func) == "print"):
                continue
            for arg in node.args:
                for ref in _printed_url_refs(arg):
                    problems.append(f"{path.name}:{node.lineno}  prints {_label(ref)} raw")
    return problems


def test_no_deploy_tool_prints_a_url_it_was_given():
    problems = _offenders()
    assert not problems, (
        "a URL from .env or a command line can carry a password, and these print it "
        "into a terminal, a deploy journal and wherever that gets pasted. Wrap it in "
        "deploy.safe_url.host_only():\n    " + "\n    ".join(problems))


def test_the_audit_read_every_module_it_claims_to():
    """A guard that walked nothing reports exactly what a clean tree reports."""
    modules = sorted(DEPLOY.glob("*.py"))
    assert len(modules) > 40, f"only {len(modules)} deploy modules were read"
    printing = [p for p in modules if "print(" in p.read_text(encoding="utf-8")]
    assert len(printing) > 20, f"only {len(printing)} modules print anything"


@pytest.mark.parametrize("source", [
    # The five shapes the audit found, as they were written.
    'print("project : {}".format(url))',
    'print("box     : {}".format(args.base_url))',
    'print(f"  url      {settings.get(\'SCANMEET_STAGING_URL\')}")',
    'print(f"COULD NOT MEASURE: {ascii_safe(args.url.rstrip(\'/\'))} is the live site.")',
    'print(f"\\nPASS: {len(rows)}/{len(rows)} answered as expected on {args.url.rstrip(\'/\')}")',
])
def test_the_audit_sees_the_shape_it_was_written_for(source):
    tree = ast.parse(source)
    print_node = tree.body[0].value
    assert [r for arg in print_node.args for r in _printed_url_refs(arg)], (
        f"the audit does not see {source!r} as printing a URL")


@pytest.mark.parametrize("source", [
    'print("project : {}".format(host_only(url)))',
    'print(f"  url      {host_only(settings.get(\'SCANMEET_STAGING_URL\'), missing=\'(missing)\')}")',
    'print(f"target {target_host(dsn)}")',
    # The sheet strips the userinfo, the query and the fragment itself.
    'print(signoff_sheet(args.url, args.exam, rows, code))',
])
def test_a_wrapped_or_self_guarding_url_is_not_a_finding(source):
    tree = ast.parse(source)
    print_node = tree.body[0].value
    assert not [r for arg in print_node.args for r in _printed_url_refs(arg)]


# ── one refusal, run for real ───────────────────────────────────────────────

def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / filename)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their own module through `sys.modules`; without this the
    # media gate raises `AttributeError: 'NoneType' object has no attribute ...`
    # before a line of it runs.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_the_media_gate_refusal_names_the_host_not_the_credentials(capsys):
    """The line an operator pastes into a ticket, on the path that refuses."""
    gate = _load("scanmeet_media_gate_under_test", "scanmeet_media_gate.py")
    secret = "hunter2"
    code = gate.main(["--url", f"https://someone:{secret}@scangrade.web.id",
                      "--exam", "exam-1", "--teacher", "t@example.invalid:pw",
                      "--student", "s@example.invalid:pw"])
    out = capsys.readouterr().out
    assert code == gate.EXIT_CANNOT_MEASURE
    assert "scangrade.web.id" in out, "the refusal must still say which site it refused"
    assert secret not in out and "someone" not in out, (
        "the refusal printed the credentials it was handed")
