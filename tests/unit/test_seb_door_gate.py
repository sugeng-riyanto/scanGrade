"""The SEB door smoke check: what it measures, and that the deploy actually runs it.

Why this file exists
--------------------
`deploy/seb_door_gate.py` is the only thing in this tree that answers *"is the
`require_seb` toggle enforced by the release this box is serving, on the live
database?"* Every other SEB check is a reading or a self-consistency test: the
generator agrees with itself, the file downloads, the panel renders, the unit tests
drive a fake client. None of them can see the failure this exists for — a toggle
that reads ON, a file that downloads, and a paper a plain browser opens anyway —
and a smoke check that is never run against the served release measures nothing
whatever its verdict logic says.

So this file guards two things that are easy to lose and expensive to lose
silently: **the verdict table** (what counts as enforced, and what is only "could
not measure") and **the wiring** (the deploy runs it before the release is declared
healthy, the conf key it reads is one the installer writes, and a finding can only
take a release down when the box has been armed for it).

The verdict is a pure function
------------------------------
`judge()` takes what the five requests answered and returns an exit code plus the
`seb door:` lines. No network, no database — which is what makes it testable here
while the run itself can only be measured on a box. The cases below are the
interesting ones: a control that did not open must not read as an SEB failure (it
reads as *not measured*), a release that predates the door must not read as a
security regression, and admitting a header hashed over a *different* key has to be
a finding — without that last case, "a header passes" and "the right header passes"
are indistinguishable, and that is the whole reason the gate is worth running.

The instrument's own contract
-----------------------------
Three properties are asserted on the source, because each is a way the gate could
quietly become a different tool: it must never read `exam_seb_credential` (the
reversible passwords of a real paper are not a smoke check's business), it must not
implement the Config Key hash a second time (one implementation, imported), and its
cleanup has to be verifiable rather than promised — a throwaway exam left in a live
school is the one side effect it cannot hand back.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import app_instance

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "deploy" / "seb_door_gate.py"
DEPLOY = ROOT / "deploy" / "scangrade-deploy.sh"
INSTALL = ROOT / "deploy" / "install-auto-deploy.sh"
SMOKE = ROOT / "deploy" / "smoke_test.py"

#: The two blocks of `scangrade-deploy.sh` around the gate. Sliced by their own
#: headers, so a guard is about the gate's block and not about the whole file: a
#: `HEALTHY=0` anywhere else in a 3,700-line script would satisfy a whole-file search
#: and prove nothing about the branch that holds the finding.
GATE_HEADER = "# ── Gate 4c: is the SEB door *enforced* on the release being served?"
NEXT_HEADER = "# ── Gate 5: do the numbers on the landing page still describe this box?"
#: Where the release stops being a candidate and becomes the one being served.
HEALTHY_ANCHOR = 'log "DEPLOY OK: $BEFORE -> $AFTER"'

#: The column the SEB route used to name and no migration has ever created. It is
#: here rather than only in the source comment because the lesson is the shape, not
#: the name: a select written as a module constant was invisible to
#: `deploy/schema_contract.py` until it learned to read one.
UNCREATED_COLUMN_STORY = "manual_unlock"


def _load(name: str, path: Path):
    """Import a deploy tool by path — they are scripts, not an installed package.

    Registered in `sys.modules` *before* it is executed: `judge` returns a
    `@dataclass`, and `dataclasses` resolves the module by name to look up string
    annotations, so an unregistered module dies inside the decorator.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("sg_seb_door_gate_under_test", GATE)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(first: str, second: str, path: Path = DEPLOY) -> str:
    """The text between two headers, exclusive of the second."""
    text = _source(path)
    start = text.index(first)
    return text[start:text.index(second, start)]


def _code(text: str) -> str:
    """A block with its comment lines removed.

    A guard that searches prose is satisfied by the block's own explanation — the
    lesson `test_auto_deploy.py` records for the launcher refresh, where "exit 1"
    was a substring of a comment. Deleting the invocation while keeping the
    paragraph that describes it has to fail, so the search is over commands.
    """
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _seen(**overrides) -> "gate.Seen":
    """An enforced run, with one observation moved. Defaults are the passing case."""
    fields = dict(control=200, control_title=True, gated=302, claim=200, matched=200,
                  matched_title=True, foreign=302,
                  # The JavaScript half: three claims that must be refused, the paper
                  # still closed after them, then an honest claim that opens it.
                  js_wrong_address=403, js_foreign=403, js_stale=403, js_closed=302,
                  js_accepted=200, js_accepted_ok=True, js_opened=200,
                  js_opened_title=True,
                  # The client half: the shipped page's own script, in a real browser.
                  # One report from its first load, the route's 200, one row for this
                  # pupil carrying this run's marker — and the paper still shut.
                  client_measured=True, client_seb_api=False, client_state="refused",
                  client_refused_shown=True, client_reports=1,
                  client_reason="no_client", client_token=True, client_claims=0,
                  client_status=200, client_reports_total=2, client_rows_total=5,
                  client_rows_marked=2, client_rows_foreign=0, client_relocked=True,
                  client_record_missing=False)
    fields.update(overrides)
    if not fields.get("gated_location"):
        fields["gated_location"] = f"/student/exams/x{gate.CLAIM_SUFFIX}"
    if not fields.get("js_closed_location"):
        fields["js_closed_location"] = f"/student/exams/x{gate.CLAIM_SUFFIX}"
    return gate.Seen(**fields)


# ── 1. the verdict, as a decision table ──────────────────────────────────────

class TestWhatCountsAsEnforced:
    def test_the_enforced_run_asks_for_the_paper_every_way_that_matters(self):
        """The passing case: the control opens, a plain browser is handed the
        handshake, the handshake answers, the paper's own key is admitted, another
        key's header is not — on the JavaScript transport the three claims that must
        be refused are refused, leave the door shut, and the honest one opens the
        paper with no header at all — and the shipped page's own script, driven in a
        real browser, reports one refusal that the server records."""
        code, lines = gate.judge(_seen())
        assert code == gate.EXIT_OK, lines
        assert lines[0].startswith("seb door: OK"), lines
        assert not any("FAILED" in line for line in lines), lines

    def test_a_control_that_did_not_open_is_not_an_seb_finding(self):
        """Without the control, "refused" could be the window, the class, the
        attempt cap or the school — the run would prove nothing about SEB, and
        saying so is the point."""
        code, lines = gate.judge(_seen(control=403, control_title=False))
        assert code == gate.EXIT_UNMEASURED, lines
        assert any("the control did not open" in line for line in lines), lines
        assert not any(line.startswith("seb door: FAILED") for line in lines), lines

    def test_a_control_page_without_the_paper_s_own_title_is_not_a_pass_either(self):
        """HTTP 200 is not the control — the paper's own title in the body is."""
        code, lines = gate.judge(_seen(control=200, control_title=False))
        assert code == gate.EXIT_UNMEASURED, lines

    def test_a_release_that_predates_the_door_is_not_a_regression(self):
        """A paper that opens with `require_seb` on, on a release whose claim route
        does not exist, is a release from before this feature — not a release that
        stopped enforcing it."""
        for missing in (404, 405):
            code, lines = gate.judge(_seen(gated=200, claim=missing))
            assert code == gate.EXIT_UNMEASURED, (missing, lines)
            assert any("predates" in line for line in lines), lines

    def test_a_plain_browser_opening_a_gated_paper_is_the_finding(self):
        code, lines = gate.judge(_seen(gated=200))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("plain browser opened" in line for line in lines), lines

    def test_a_gated_paper_that_answers_neither_the_paper_nor_the_handshake(self):
        code, lines = gate.judge(_seen(gated=500, gated_location=""))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("neither the paper" in line for line in lines), lines

    def test_the_handshake_road_missing_is_the_finding(self):
        """The road exists for the clients that cannot send the header at all; a door
        that sends a pupil to a 500 has refused them with a detour."""
        code, lines = gate.judge(_seen(claim=500))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("handshake page" in line for line in lines), lines

    def test_an_honest_client_refused_is_the_finding(self):
        """The door fails closed on a matching key: nobody can open the paper."""
        for matched in (302, 403, 500):
            code, lines = gate.judge(_seen(matched=matched, matched_title=False))
            assert code == gate.EXIT_NOT_ENFORCED, (matched, lines)
            assert any("was not admitted" in line or "not admitted" in line
                       for line in lines), lines

    def test_a_matching_header_without_the_paper_in_the_body_is_the_finding(self):
        code, lines = gate.judge(_seen(matched=200, matched_title=False))
        assert code == gate.EXIT_NOT_ENFORCED, lines

    def test_admitting_another_key_s_header_is_the_finding(self):
        """The falsification. Without this case C1 and C2 are equally explained by
        \"any header passes\" — the failure the whole check exists to catch."""
        code, lines = gate.judge(_seen(foreign=200))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("different" in line and "key" in line for line in lines), lines

    # ── the JavaScript half: the road every WKWebView client depends on ──────

    @pytest.mark.parametrize("field,needle", [
        ("js_wrong_address", "over the paper's own address"),
        ("js_foreign", "another key"),
        ("js_stale", "before the key was re-issued"),
    ])
    def test_a_javascript_value_that_must_be_refused_is_the_finding(self, field, needle):
        """Three different defects, each named on its own line.

        They are not one check with three inputs: a value hashed over the paper's
        address is the binding backwards (the failure this path is likeliest to
        have), a value over another key is the falsification, and a value from
        before a re-issue is a file that should be dead still opening the paper.
        """
        for wrong in (200, 302, 500):
            code, lines = gate.judge(_seen(**{field: wrong}))
            assert code == gate.EXIT_NOT_ENFORCED, (field, wrong, lines)
            assert any("FAILED" in line and needle in line for line in lines), lines

    def test_a_refused_claim_that_still_opened_the_paper_is_the_finding(self):
        """The claim is kept in the session, so a refusal that minted one leaves the
        door open for the rest of the sitting: 'must be refused' has to mean the
        paper stays shut, not merely that the POST answered 403."""
        for closed, location in ((200, ""), (302, "/student/exams/x"), (403, "")):
            code, lines = gate.judge(_seen(js_closed=closed,
                                           js_closed_location=location))
            assert code == gate.EXIT_NOT_ENFORCED, (closed, location, lines)
            assert any("refused claim" in line for line in lines), lines

    def test_an_honest_javascript_client_refused_is_the_finding(self):
        """A handshake that refuses the exam's own value locks out every iPad — the
        exact failure this half exists to catch, and one the header test cannot see."""
        for accepted, ok in ((403, False), (302, False), (200, False)):
            code, lines = gate.judge(_seen(js_accepted=accepted, js_accepted_ok=ok))
            assert code == gate.EXIT_NOT_ENFORCED, (accepted, ok, lines)
            assert any("refused a client reporting" in line for line in lines), lines

    def test_a_claim_that_did_not_open_the_paper_is_the_finding(self):
        """The route accepted it, so anything but the paper means the door is not
        reading the claim it was given — a 200 with the wrong body included."""
        code, lines = gate.judge(_seen(js_opened=302, js_opened_title=False))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("was not admitted to the paper" in line for line in lines), lines
        code, lines = gate.judge(_seen(js_opened=200, js_opened_title=False))
        assert code == gate.EXIT_NOT_ENFORCED, lines

    def test_a_release_that_predates_the_javascript_handshake_is_not_a_regression(self):
        """The claim route absent is an older release, the same reading C1b gives,
        and never a refusal on its own."""
        for missing in (404, 405):
            code, lines = gate.judge(_seen(js_wrong_address=missing))
            assert code == gate.EXIT_UNMEASURED, (missing, lines)
            assert any("predates" in line for line in lines), lines
            assert not any(line.startswith("seb door: FAILED") for line in lines), lines

    def test_the_exit_code_and_the_lines_still_agree(
            self):
        """The invariant a caller depends on: the exit code the deploy branches on
        and the line an operator reads cannot disagree."""
        cases = [
            _seen(),
            _seen(control=403, control_title=False),
            _seen(gated=200, claim=404),
            _seen(gated=200),
            _seen(gated=500, gated_location=""),
            _seen(claim=500),
            _seen(matched=403, matched_title=False),
            _seen(foreign=200),
            _seen(gated=200, claim=200, matched=200, foreign=200),
            _seen(js_wrong_address=404),
            _seen(js_wrong_address=200),
            _seen(js_foreign=200),
            _seen(js_stale=302),
            _seen(js_closed=200, js_closed_location=""),
            _seen(js_accepted=403, js_accepted_ok=False),
            _seen(js_opened=302, js_opened_title=False),
        ]
        for case in cases:
            code, lines = gate.judge(case)
            failed = [line for line in lines if line.startswith("seb door: FAILED")]
            assert code in (gate.EXIT_OK, gate.EXIT_NOT_ENFORCED, gate.EXIT_UNMEASURED)
            assert (code == gate.EXIT_NOT_ENFORCED) == bool(failed), (case, code, lines)
            if code == gate.EXIT_UNMEASURED:
                assert any("NOT MEASURED" in line for line in lines), lines


# ── 2. the instrument's own contract ─────────────────────────────────────────

def _used_tokens(path: Path) -> list[str]:
    """Every name and string literal a script actually uses — no prose.

    The same reader `tests/unit/test_seb_phase11.py` applies to its own tool, and
    for the same reason: this module's docstring **names** the table it forbids, so
    a whole-file search fires on a sentence and the guard gets relaxed instead of
    trusted.
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


class TestWhatTheInstrumentMustNeverDo:
    def test_it_never_reaches_for_the_credential_table(self):
        """`exam_seb_credential` holds the *reversible* quit and admin passwords.
        They exist so a teacher can read one aloud; a smoke check that runs
        unattended has no business touching that table."""
        offenders = [token for token in _used_tokens(GATE)
                     if "exam_seb_credential" in token]
        assert not offenders, (
            f"the gate references the reversible-password table in code: {offenders}")

    def test_that_guard_is_about_code_and_not_prose(self, tmp_path):
        """The guard's own honesty check: a file that only *mentions* the table must
        pass, or the assertion above would look satisfied for the wrong reason."""
        prose = tmp_path / "prose.py"
        prose.write_text('"""A docstring naming exam_seb_credential."""\n'
                         "# a comment naming it too\nx = 1\n", encoding="utf-8")
        assert not [t for t in _used_tokens(prose) if "exam_seb_credential" in t]
        code = tmp_path / "code.py"
        code.write_text("row = table('exam_seb_credential')\n", encoding="utf-8")
        assert [t for t in _used_tokens(code) if "exam_seb_credential" in t]

    def test_the_hash_is_imported_rather_than_implemented_a_second_time(self):
        """One implementation of the Config Key rule, or the smoke check measures its
        own arithmetic instead of the door's."""
        text = _source(GATE)
        assert "from app.services import seb_config_key" in text
        assert "ck.request_hash(" in text
        assert "import hashlib" not in text, (
            "a second hash implementation can drift from the one the door uses")
        assert re.search(r"\bsha256\s*\(", text) is None, (
            "the hash is `seb_config_key`'s, never this file's")

    def test_the_throwaway_row_is_marked_and_swept_by_name(self):
        """A run killed mid-measurement leaves something the *next* run deletes,
        rather than something a school has to find."""
        text = _source(GATE)
        assert gate.TITLE in text
        assert "def sweep(" in text and "def leftovers(" in text
        sweep = ast.get_source_segment(text, _method(GATE, "Rest", "sweep"))
        assert "leftovers(" in sweep, "the sweep does not look for the marker"

    def test_the_cleanup_is_in_a_finally_and_is_verified_by_reading_the_rows_back(self):
        """`finally` is the promise; reading the rows back is the evidence. A
        throwaway exam left in a live school is the one side effect this check
        cannot hand back."""
        source = _source(GATE)
        guarded = [node for node in ast.walk(_function(GATE, "main"))
                   if isinstance(node, ast.Try) and node.finalbody]
        assert guarded, "the cleanup is not in a `finally`"
        cleanup = "\n".join(ast.get_source_segment(source, stmt) or ""
                            for stmt in guarded[0].finalbody)
        assert "forget(" in cleanup, "the cleanup does not delete what it created"
        assert "LEFTOVER" in cleanup, (
            "a cleanup that cannot verify itself must report the leftover")

    def test_the_columns_it_writes_to_the_live_database_exist_in_the_migrations(self):
        """The gate posts to production. A column no migration creates would turn
        every run into \"could not measure\" — a smoke check that never fires, which
        is the failure mode this whole task is about."""
        import deploy.schema_contract as sc

        schema = sc.schema_from_migrations()
        payload = _method(GATE, "Rest", "create")
        keys = set()
        for node in ast.walk(payload):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "update" and node.args \
                    and isinstance(node.args[0], ast.Dict):
                keys |= {k.value for k in node.args[0].keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        assert keys, "the create payload could not be read"
        missing = sorted(c for c in keys if c not in schema["exams"])
        assert not missing, (
            f"the gate writes {missing}, which no migration creates — every run "
            "would report \"could not measure\" and the door would never be judged")
        # The pupil lookup, for the same reason: a column absent from this select
        # arrives *absent* and reads as `None`.
        for column in ("id", "role", "class_id", "school_id"):
            assert column in schema["profiles"], column


def _function(path: Path, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"no function named {name} in {path}")


def _bound_by(fn: ast.AST) -> set[str]:
    """Every name this function binds in its own scope, nested scopes aside.

    Parameters, assignments, `for`/`with` targets, `except … as`, comprehension
    targets and names bound by a nested `def`/`class` (a call can name them). Imports
    are bindings too: the gate imports the Config Key module inside the function that
    uses it, and a reader that only understood assignment would call that a NameError
    and be right about nothing.
    """
    names = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node is not fn:
            names.add(node.name)
    return names


def _walk_own_scope(node: ast.AST):
    """Nodes in this scope only: a nested def is yielded but not descended into.

    Without this the reader would judge a nested function's parameters against the
    outer function's bindings and invent a NameError of its own.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                              ast.Lambda)):
            yield child
            continue
        yield child
        yield from _walk_own_scope(child)


def _unbound_names(path: Path) -> list[tuple[str, int, str]]:
    """(function, line, name) for every call argument no scope binds."""
    import builtins

    tree = ast.parse(_source(path))
    module = set(dir(builtins))
    # Names the interpreter puts in every module's globals: `__file__` is one the gate
    # reads, and it is not in `dir(builtins)`.
    module |= {"__file__", "__name__", "__doc__", "__package__", "__spec__",
               "__loader__", "__builtins__", "__annotations__", "__cached__"}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module.add(node.name)
        elif isinstance(node, ast.Assign):
            module |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module.add(node.target.id)
        elif isinstance(node, ast.Import):
            module |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            module |= {(a.asname or a.name) for a in node.names}

    found: list[tuple[str, int, str]] = []

    def visit(fn: ast.AST, enclosing: set[str]) -> None:
        allowed = _bound_by(fn) | enclosing | module
        for node in _walk_own_scope(fn):
            if not isinstance(node, ast.Call):
                continue
            parts = [node.func] + list(node.args) + [k.value for k in node.keywords]
            for part in parts:
                for inner in ast.walk(part):
                    if (isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load)
                            and inner.id not in allowed):
                        found.append((getattr(fn, "name", "<lambda>"),
                                      node.lineno, inner.id))
        for child in ast.walk(fn):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not fn:
                visit(child, allowed | _bound_by(fn))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit(node, set())
    # One defect can be reached through two call nodes (the outer call's callee is the
    # inner call), and reporting it twice reads like two defects.
    return sorted(set(found))


def _method(path: Path, cls: str, name: str) -> ast.AST:
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return child
    raise AssertionError(f"no method {cls}.{name} in {path}")


class TestTheJavascriptHalfPostsWhatThePagePosts:
    """The gate speaks the handshake's protocol, and the protocol lives in two other
    files: the page's own script and the app's CSRF reader. A smoke check that spells
    either name itself measures its own mistake — the wrong field is a refusal it
    caused, and a missing token is the guard answering instead of the door."""

    CLAIM_PAGE = ROOT / "app" / "templates" / "student" / "seb_claim.html"
    CSRF = ROOT / "app" / "utils" / "csrf.py"

    def test_the_field_it_posts_is_the_field_the_page_posts(self):
        sent = re.search(r"JSON\.stringify\(\{(\w+): value\}\)",
                         _source(self.CLAIM_PAGE))
        assert sent, "the page's post body is not a JSON.stringify of one field"
        assert sent.group(1) == gate.JS_VALUE_FIELD, (
            f"the gate posts {gate.JS_VALUE_FIELD!r} while the page posts "
            f"{sent.group(1)!r}, so every claim would be refused as empty")

    def test_the_header_it_carries_is_the_header_the_app_reads(self):
        page = _source(self.CLAIM_PAGE)
        assert f"'{gate.CSRF_HEADER}':" in page, (
            "the gate sends a token the shipped page does not send")
        csrf = _source(self.CSRF)
        assert f"request.headers.get('{gate.CSRF_HEADER}'" in csrf, (
            "the app's CSRF reader no longer accepts the header the gate sends, so "
            "every claim would be refused by the guard rather than by the door")

    def test_a_claim_page_without_a_token_is_reported_rather_than_skipped(self):
        """No meta tag means the page's own script cannot post either, so it is not a
        state to quietly carry on from: the run says so and stops being a verdict.

        Read as a shape rather than as two strings that merely appear: a condition
        widened to `if not token and False:` keeps both the read and the sentence
        while never refusing, and a guard satisfied by its own message is a guard
        that reports a catch it never made.
        """
        code = _code(_source(GATE))
        assert "CSRF_RE.search(claim_body)" in code
        assert re.search(r"if not token:\n\s+raise RuntimeError", code), (
            "nothing refuses a claim page that carries no token")
        assert "carries no csrf-token meta tag" in code

    def test_the_javascript_value_is_hashed_over_the_claim_page_not_the_paper(self):
        """The one binding this path can get backwards, as a source rule.

        The header transport hashes the *paper's* address (C2); the JavaScript value
        is over the address of the page the script ran on, which is the handshake
        page. Three posts use one and exactly one uses the other.
        """
        code = _code(_source(GATE))
        assert code.count("ck.request_hash(claim_url") >= 3, (
            "the JavaScript values are not hashed over the handshake page's address "
            "— every honest client would be refused")
        # The two places the *paper's* address belongs: C2, which proves an honest
        # header is admitted, and C4a, which proves a value hashed over it is not.
        assert code.index("good = ck.request_hash(paper, run_key)") \
            < code.index('step = "C4a')
        wrong_address = code[code.index('step = "C4a'):code.index('step = "C4b')]
        assert "ck.request_hash(paper" in wrong_address, (
            "C4a no longer posts a value over the paper's address, so it measures "
            "nothing about the binding")

    def test_the_stale_value_is_computed_before_the_key_is_re_issued(self):
        """Compute, then re-issue: the value has to be the one that was honest a
        moment ago. The other order would post a value that never matched anything."""
        code = _code(_source(GATE))
        computed = code.index("stale_value = ck.request_hash(claim_url, run_key)")
        reissued = code.index("rest.gate(exam_id, fresh_key)")
        assert computed < reissued, (
            "the stale value is computed after the re-issue, so it is not stale")
        assert "fresh_key = secrets.token_hex(32)" in code, (
            "the re-issue does not write a key of its own")

    def test_the_honest_claim_is_posted_only_after_the_three_refusals(self):
        """A claim lives in the session: posted first, it would open the paper for
        every later request and C4d would prove nothing."""
        code = _code(_source(GATE))
        refusals = [code.index(f'step = "C4{a}') for a in ("a", "b", "c")]
        honest = code.index('step = "C5, the honest JavaScript claim"')
        assert honest > max(refusals), (
            "the honest claim is posted before the refusals, so their 'the paper "
            "stayed shut' observation is worthless")
        closed = code.index('step = "C4d, the paper after three refused claims"')
        assert max(refusals) < closed < honest, (
            "the paper is not checked between the refusals and the honest claim")


class TestTheAddressesItDialsAreTheAppSOwnRoutes:
    """The gate composes the paper's URL itself, so it has to be the same string the
    door hashes. If it drifted, the run would compare two different addresses and
    report a finding about a door that is fine."""

    @staticmethod
    def _rules() -> dict[str, str]:
        # Werkzeug spells a placeholder `<exam_id>`; the gate builds the same string
        # with `{}`, because it has the id in hand. One path, two spellings.
        return {rule.endpoint: rule.rule.replace("<", "{").replace(">", "}")
                for rule in app_instance().url_map.iter_rules()}

    def test_the_paper_path_is_the_take_exam_rule(self):
        rules = self._rules()
        assert rules["student.take_exam"] == gate.PAPER_PATH, (
            "the gate dials a different path than the route that serves the paper")
        assert rules["seb.js_claim"] == gate.PAPER_PATH + gate.CLAIM_SUFFIX, (
            "the handshake page the gate expects is not the one the door redirects to")

    def test_it_signs_in_at_the_pupil_door_with_the_field_the_door_reads(self):
        rules = self._rules()
        assert rules["auth.login_user"] == gate.LOGIN_PATH
        # One convention for the CSRF token, shared with the smoke test rather than
        # restated: a second name is a credential that never signs in.
        smoke = _load("sg_smoke_test_under_test", SMOKE)
        assert gate.CSRF_RE.pattern == smoke.CSRF_RE.pattern, (
            "two readers of the same meta tag, with two patterns, will drift")
        assert gate.CSRF_RE.search('<meta name="csrf-token" content="abc">')
        assert "_csrf_token" in _source(GATE)


# ── 3. the deploy actually runs it ───────────────────────────────────────────

class TestTheDeployRunsItBeforeCallingTheReleaseHealthy:
    def test_the_gate_is_run_by_the_deploy(self):
        block = _code(_block(GATE_HEADER, NEXT_HEADER))
        assert "seb_door_gate.py" in block, (
            "the block that explains the gate no longer runs it — the paragraph is "
            "not the check")
        assert "SEB_RC=$?" in block, "the gate's exit code is not read"

    def test_it_runs_before_the_release_is_declared_healthy(self):
        """\"Runs before a release is declared healthy\" is the request; this is the
        assertion that makes it true of the script and not of a docstring.

        Anchored on the quoted command path rather than on the file name: the
        name also appears in the comment that describes the gate, so a search for
        it would find a sentence after the invocation had been deleted.
        """
        text = _source(DEPLOY)
        invocation = '"$REPO/deploy/seb_door_gate.py"'
        assert invocation in text, "the deploy does not invoke the gate"
        assert text.index(invocation) < text.index(HEALTHY_ANCHOR), (
            "the SEB door gate runs after the release has already been declared OK")

    def test_it_reuses_gate_four_s_credentials_rather_than_a_second_conf(self):
        block = _block(GATE_HEADER, NEXT_HEADER)
        assert '--base "${SMOKE_BASE_URL:-}"' in block
        assert '--student "${SMOKE_MURID:-}"' in block
        assert '--repo "$REPO"' in block
        assert "[ -f \"$SMOKE_CONF\" ]" in block, (
            "the gate reads credentials out of the smoke conf")

    def test_a_finding_rolls_back_only_when_the_box_is_armed_for_it(self):
        block = _block(GATE_HEADER, NEXT_HEADER)
        assert 'if [ "${SEB_ENFORCE:-false}" = "true" ]' in block
        assert block.count("HEALTHY=0") == 1, (
            "exactly one branch of the gate may take the release down")
        assert block.index("HEALTHY=0") > block.index('if [ "${SEB_ENFORCE:-false}"'), (
            "the rollback is outside the arm check — an unarmed box would refuse")
        assert 'FAIL_REASON="seb door gate' in block

    def test_exit_two_never_takes_the_release_down(self):
        """\"Could not measure\" is not a verdict, and a box with no fixture must not
        refuse every good release."""
        block = _block(GATE_HEADER, NEXT_HEADER)
        arm = block.split("\n    2)", 1)[1].split("\n    *)", 1)[0]
        assert "HEALTHY=0" not in arm, (
            "the \"could not measure\" arm rolls the release back")
        assert "could not measure" in arm, (
            "the exit-2 arm does not say what happened")

    def test_the_box_is_armed_only_after_the_gate_has_measured_once(self):
        """The installer runs it before it arms it, the discipline the smoke and DOM
        gates already use: a gate armed against a broken measurement would refuse
        good releases."""
        text = _source(INSTALL)
        assert 'SEB_ENFORCE="false"' in text, "the conf template lacks the key"
        assert text.index('SEB_ENFORCE="true"') > text.index("seb_door_gate.py"), (
            "the installer arms the gate without running it")
        # The one writer of `true` is the arm of exit 0, so a run that measured
        # nothing (`2`) or failed (`*`) cannot arm the gate by accident.
        arms = re.findall(r"case \"\$SEB_RC\" in(.*?)esac", text, re.S)
        assert arms, "no case on the gate's exit code in the installer"
        assert 'SEB_ENFORCE="true"' in arms[0].split("    2)", 1)[0]
        assert 'SEB_ENFORCE="true"' not in arms[0].split("    2)", 1)[1]

    def test_the_conf_keys_the_gate_block_reads_are_keys_the_installer_writes(self):
        """The conf is written in one file and read in another; a name only one of
        them knows is a gate that silently reads an empty credential."""
        block = _block(GATE_HEADER, NEXT_HEADER)
        read = set(re.findall(r"\$\{((?:SMOKE|SEB)_[A-Z0-9_]*):-", block))
        assert {"SMOKE_BASE_URL", "SMOKE_MURID", "SEB_ENFORCE"} <= read, (
            f"the gate block no longer reads the credentials it needs: {read}")
        written = set(re.findall(r"^((?:SMOKE|SEB)_[A-Z0-9_]*)=", _source(INSTALL), re.M))
        unknown = sorted(name for name in read if name not in written)
        assert not unknown, (
            f"the gate block reads {unknown}, which the installer never writes")

    def test_both_scripts_still_parse(self):
        for script in (DEPLOY, INSTALL):
            done = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                                  text=True)
            assert done.returncode == 0, f"{script.name}: {done.stderr}"


# ── 4. the one real defect this task found ───────────────────────────────────

class TestTheSebRouteSelectsOnlyColumnsThatExist:
    """The gate found this before it ever ran: `app/routes/seb.py` selected
    `manual_unlock`, no migration has ever created it, and the whole SEB panel
    answered PostgREST 42703 against the live database while every test drove a fake
    client. A smoke check that creates a row and opens a paper through those routes
    is worthless while the routes 500, so the column and the check arrive together.

    The general guard is `deploy/schema_contract.py`, which now reads a select
    written as a module constant; this is the instance, so a regression names the
    column instead of the reader.
    """

    def test_every_column_the_route_selects_is_created_by_a_migration(self):
        import deploy.schema_contract as sc
        from app.routes.seb import EXAM_COLUMNS

        columns = [column.strip() for column in EXAM_COLUMNS.split(",") if column.strip()]
        assert UNCREATED_COLUMN_STORY not in columns, (
            f"{UNCREATED_COLUMN_STORY} is not a column — it is the *name of a "
            "function* (`resume_code.manual_unlock`), and naming it in a select "
            "makes every SEB route fail on a real database")
        schema = sc.schema_from_migrations()["exams"]
        assert sorted(c for c in columns if c not in schema) == [], (
            "the SEB select names a column no migration creates")



# ── 5. the client half: the page's own script, in a real browser ─────────────

class TestTheClientHalfIsJudged:
    """C6: the *shipped page*, in a real browser, refusing a client it cannot ask.

    The header transport and the claim protocol are measured by this file speaking to
    the door itself; neither of them sees whether the page a pupil is actually given
    ever reports anything. That is the half `tests/unit/test_seb_js_api.py` drives
    under node, and the failure it exists to catch is the quietest one there is: the
    toggle reads ON, the .seb file downloads, every iPad is refused, and the teacher's
    panel cannot say that a single pupil was turned away.

    Three of these cases are *not measured* rather than findings — no browser on the
    box, a browser that reports a SEB API (so it is not this population), and a
    database without the record (so the release is older than migration 064). A
    reading that could not be taken must never be a pass, and must never be a
    regression either.
    """

    def test_a_pass_requires_the_page_s_own_script_to_have_worked(self):
        code, lines = gate.judge(_seen())
        assert code == gate.EXIT_OK, lines

    def test_a_box_with_no_browser_is_not_a_pass(self):
        """\"Enforced\" is a claim about both transports and both halves of the client.

        Exit 2 rather than exit 0, because a run that never pointed a browser at the
        page has not measured the half every WKWebView client depends on — and the
        deploy treats 2 loudly without rolling a good release back.
        """
        code, lines = gate.judge(_seen(client_measured=False))
        assert code == gate.EXIT_UNMEASURED, lines
        assert any("handshake page's own script was not measured" in line
                   for line in lines), lines
        assert any("SG_CHROME" in line for line in lines), (
            "the reading that was not taken does not name the key that arms it")
        assert not any(line.startswith("seb door: FAILED") for line in lines), lines

    def test_a_browser_that_reports_a_seb_api_is_not_this_population(self):
        code, lines = gate.judge(_seen(client_seb_api=True))
        assert code == gate.EXIT_UNMEASURED, lines
        assert any("SafeExamBrowser API" in line for line in lines), lines

    def test_a_database_without_the_record_is_an_older_release(self):
        code, lines = gate.judge(_seen(client_record_missing=True))
        assert code == gate.EXIT_UNMEASURED, lines
        assert any("migration 064" in line for line in lines), lines
        assert not any(line.startswith("seb door: FAILED") for line in lines), lines

    def test_a_page_that_did_not_end_refused_is_the_finding(self):
        """Either witness is enough to convict: the state the page reports, or the
        panel it actually laid out."""
        for state, shown in (("checking", False), ("error", False), ("refused", False),
                             ("", True), ("", False)):
            code, lines = gate.judge(_seen(client_state=state,
                                          client_refused_shown=shown))
            assert code == gate.EXIT_NOT_ENFORCED, (state, shown, lines)
            assert any("ended in state" in line for line in lines), lines

    @pytest.mark.parametrize("reports", [0, 2, 3])
    def test_a_visit_that_reports_other_than_once_is_the_finding(self, reports):
        """One locked-out visit leaves one report. The number on the panel is a
        headcount, so a page that reports twice doubles it — and the version of this
        gate that navigated to the paper *inside* the counted phase read 2 for a
        single healthy visit, which is how this row came to exist."""
        code, lines = gate.judge(_seen(client_reports=reports))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("POST(s) to" in line for line in lines), lines

    def test_the_wrong_reason_is_the_finding(self):
        """`no_key` here means the route either refused the report (no row) or
        recorded a cause the teacher cannot read."""
        code, lines = gate.judge(_seen(client_reason="no_key"))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any(gate.NO_CLIENT_REASON in line for line in lines), lines

    def test_a_report_with_no_csrf_header_is_the_finding(self):
        code, lines = gate.judge(_seen(client_token=False))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any(gate.CSRF_HEADER in line for line in lines), lines

    def test_a_browser_with_no_seb_that_claims_anyway_is_the_finding(self):
        code, lines = gate.judge(_seen(client_claims=1))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("claim(s) to" in line for line in lines), lines

    def test_a_refusal_route_that_did_not_take_the_report_is_the_finding(self):
        for status in (302, 400, 403, 500, 0):
            code, lines = gate.judge(_seen(client_status=status))
            assert code == gate.EXIT_NOT_ENFORCED, (status, lines)
            assert any("refusal route answered" in line for line in lines), lines

    def test_a_report_that_left_no_row_is_the_finding(self):
        code, lines = gate.judge(_seen(client_rows_marked=0))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("0 row(s) carrying this run's browser marker" in line
                   for line in lines), lines

    @pytest.mark.parametrize("marked", [1, 3])
    def test_a_lost_or_duplicated_write_is_the_finding(self, marked):
        """Two loads reported, so two rows are owed — and both directions are defects.

        One row means a report was dropped (a pupil turned away with no record); three
        mean the count a teacher reads is larger than the number of visits. The number
        that matters is not "one": it is "as many as the page reported", and a live
        run against a real database is what showed that the paper sends the browser
        back to the handshake page, so a locked-out visit reports **twice**.
        """
        code, lines = gate.judge(_seen(client_rows_marked=marked))
        assert code == gate.EXIT_NOT_ENFORCED, (marked, lines)
        assert any(f"{marked} row(s) carrying this run's browser marker" in line
                   for line in lines), lines

    def test_a_marked_row_that_is_not_this_pupil_s_is_the_finding(self):
        code, lines = gate.judge(_seen(client_rows_foreign=1))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("another pupil" in line for line in lines), lines

    def test_a_row_this_browser_did_not_leave_is_the_finding(self):
        """A count of rows cannot say who caused them; the marker can, and rows this run
        did not cause are not evidence that the page reported. This is also the
        distinction that makes the check usable at all: the three refused claims above
        leave rows for the same paper, deliberately, and they carry the gate's own
        `requests` User-Agent rather than this browser's marker.
        """
        code, lines = gate.judge(_seen(client_rows_total=5, client_rows_marked=0))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any(gate.BROWSER_UA in line for line in lines), lines
        assert any("5 row(s) exist for this paper in total" in line for line in lines), (
            "the finding does not explain the rows the protocol lanes left")

    def test_a_visit_that_never_reports_at_all_is_the_finding(self):
        code, lines = gate.judge(_seen(client_reports_total=0))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("never reported a refusal at all" in line for line in lines), lines

    def test_a_report_that_opened_the_paper_is_the_finding(self):
        code, lines = gate.judge(_seen(client_relocked=False))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any("admitted to the paper" in line for line in lines), lines

    def test_a_finding_outranks_a_reading_that_could_not_be_taken(self):
        """Both can be true at once, and the exit code has to be the one that says
        something is wrong: 2 would keep a release with a real defect on the door."""
        code, lines = gate.judge(_seen(client_measured=False, gated=200))
        assert code == gate.EXIT_NOT_ENFORCED, lines
        assert any(line.startswith("seb door: FAILED") for line in lines), lines


class TestTheClientHalfIsDrivenTheWayTheOtherGatesDrive:
    """Driven, and driven the way this box already drives a browser: one locator
    (`SG_CHROME`), one CDP client in the tree, and the page's own contract read rather
    than guessed at."""

    CLAIM_PAGE = ROOT / "app" / "templates" / "student" / "seb_claim.html"
    RENDER = ROOT / "deploy" / "exam_render_gate.py"

    @staticmethod
    def _imported(text: str) -> str:
        """The names taken from the shared module, however the import is spelt.

        Two spellings exist in this tree — one line and a parenthesised block — and a
        reader that only understands the first would pass on the second by finding an
        empty string."""
        rest = text.split("from touch_gate import", 1)[1]
        if rest.lstrip().startswith("("):
            return rest.split(")", 1)[0]
        return rest.split("\n", 1)[0]

    def test_the_gate_uses_the_shared_browser_plumbing(self):
        code = _code(_source(GATE))
        imported = self._imported(code)
        for name in ("_CDP", "browser_launch", "locate_browser", "_free_port"):
            assert name in imported, f"{name} is not taken from the touch gate"
        assert "class _CDP" not in code, (
            "the gate carries its own CDP client: the events a page emits while it "
            "loads are the shared client's job, and a second copy is the copy that "
            "goes blind")

    def test_the_render_gate_shares_it_now_too(self):
        """The copy that existed was a *second* implementation of the same socket —
        one that kept events and one that dropped them."""
        render = _code(_source(self.RENDER))
        assert "_CDP" in self._imported(render)
        assert "class _CDP" not in render

    def test_the_probe_reads_the_page_s_own_panels(self):
        """The state names are the page's contract, so probe and page cannot drift:
        `x-show="state === 'refused'"` is what makes the refusal the visible panel."""
        page = _source(self.CLAIM_PAGE)
        expression = gate.client_expression()
        for name in ("checking", "refused", "error"):
            assert f"state === '{name}'" in page, f"the page has no `{name}` panel"
            assert name in expression, f"the probe does not look for `{name}`"
        assert "getComputedStyle" in expression, (
            "the probe does not read the layout, which is the question a pupil answers "
            "by looking")

    def test_it_reads_the_state_from_the_scope_that_has_one(self):
        """Measured, not deduced: taking `_x_dataStack[0]` from the first `[x-data]` on
        the page reads the app shell's scope and answers `null` on a page whose refusal
        panel is plainly on the screen — which a real browser had to be pointed at
        this to find."""
        expression = gate.client_expression()
        assert "querySelectorAll('[x-data]')" in expression, (
            "the probe takes the first `[x-data]` on the page, which is not this page's "
            "own scope")
        assert "_x_dataStack" in expression

    def test_the_report_is_counted_for_one_page_load(self):
        """The paper navigation is what proves the report opened nothing, and it hands
        the browser the handshake page a second time: counting that load's report is
        how a healthy release reads as a finding."""
        code = _code(_source(GATE))
        assert "first_load = len(events)" in code
        assert "events[:first_load]" in code, (
            "the client reading is taken over the second load's events too")
        assert code.index("first_load = len(events)") \
            < code.index('await c.send("Page.navigate", url=paper)'), (
            "the phase is cut after the paper navigation, so it counts that load")

    def test_the_suffix_and_the_reason_are_the_route_s_and_the_record_s(self):
        """Three spellings of one fact: the gate's suffix, the route's own rule, and
        the reason the record module accepts — plus the page's own call. A smoke check
        that guesses any of them measures itself."""
        rules = {rule.endpoint: rule.rule.replace("<", "{").replace(">", "}")
                 for rule in app_instance().url_map.iter_rules()}
        assert rules["seb.refusal_report"] == gate.PAPER_PATH + gate.REFUSAL_SUFFIX, (
            "the gate dials a refusal route the app does not have")
        assert gate.REFUSAL_SUFFIX == gate.CLAIM_SUFFIX + "/refused"
        from app.services import seb_door_log
        assert gate.NO_CLIENT_REASON in seb_door_log.CLIENT_REASONS, (
            "the reason the gate expects is one the route would answer 400 to")
        assert f"report('{gate.NO_CLIENT_REASON}')" in _source(self.CLAIM_PAGE), (
            "the page does not report the reason this gate waits for")

    def test_the_marker_is_this_run_s_and_no_page_ever_sends_it(self):
        """The provenance of the row, and the reason a marker is needed at all: a count
        of rows for an exam says nothing about which client caused them."""
        assert gate.BROWSER_UA not in _source(self.CLAIM_PAGE)
        code = _code(_source(GATE))
        assert "Network.setUserAgentOverride" in code, (
            "the browser is not given the marker, so the row could be anyone's")
        assert "userAgent=BROWSER_UA" in code

    def test_a_database_without_the_record_is_read_rather_than_raised(self):
        """`missing_ok`: a release from before migration 064 is not a release whose door
        is broken — while every other 4xx stays an exception, because a mistyped column
        must never read as "nothing to see"."""
        source = _source(GATE)
        assert "def refusals(" in _code(source)
        refusals = ast.get_source_segment(source, _method(GATE, "Rest", "refusals"))
        assert "missing_ok=True" in refusals, (
            "the record read turns a missing table into a crash, so the whole run stops "
            "being a verdict instead of the client half being unmeasured")
        assert "if missing_ok and response.status_code in (400, 404)" in _code(source), (
            "no other 4xx may be swallowed as an absent object")

    def test_the_cleanup_verifies_the_refusals_too(self):
        """The report writes a row, and a row outlives the exam row it points at only
        as long as the cascade says so: the cleanup reads both back."""
        source = _source(GATE)
        forget = ast.get_source_segment(source, _method(GATE, "Rest", "forget"))
        assert "refusals(" in forget, (
            "the cleanup does not look at what the page's report left behind")
        guarded = [node for node in ast.walk(_function(GATE, "main"))
                   if isinstance(node, ast.Try) and node.finalbody]
        cleanup = "\n".join(ast.get_source_segment(source, stmt) or ""
                            for stmt in guarded[0].finalbody)
        assert "refusals_left" in cleanup, (
            "a leftover refusal row would never be reported")

    def test_the_run_actually_drives_the_page(self):
        """The half being driven and the half being judged are the same one.

        A perfect verdict table proves nothing about the run if nothing ever fills it
        in: a gate that answers "NOT MEASURED" on every release because it never called
        the driver is a gate nobody reads twice.
        """
        code = _code(_source(GATE))
        assert "reading, why = client_half(" in code, (
            "nothing in the run drives the handshake page in a browser")
        assert "seen.client_measured = True" in code, (
            "the browser reading is never handed to the verdict")
        assert "seen.client_rows_marked = " in code, (
            "the rows this browser left are never read back")
        assert "reading.reports_total += 1" in code, (
            "only the first load's reports are counted, so the second load's row "
            "cannot be compared against it — the operand has to be the *count*, not "
            "the field the count is copied into, or a mutation that counts the wrong "
            "load still satisfies a check for the name")

    def test_every_name_the_run_passes_is_a_name_the_run_has(self):
        """A call naming an unbound variable is a `NameError` **at run time**, and this
        gate catches it: the client half answers "NOT MEASURED" on every release, for
        ever, with a clean exit code and a line about a browser.

        That is how it shipped, twice, in the same lane. First `main` passed
        `base_url` while binding `base`; the fix then renamed the *definition's*
        parameter instead of the argument, so `client_half(base, …)` passed `base_url`
        to its own body. Both times every guard here read the *text* of a call, which
        was right down to the arguments it does not have, and only a run against a
        served release on a real database showed it: C1..C4d printed their two lines
        each, then the verdict said "could not measure".

        So the reading is over names, per scope: every name loaded in a call must be a
        parameter of that function, a local it binds, a name an enclosing function
        binds, a module-level name, or a builtin.
        """
        offenders = _unbound_names(GATE)
        assert not offenders, (
            f"a call reads a name nothing binds: {offenders} — a NameError at run "
            f"time, which this gate reports as 'could not measure'")
