"""A release that merged is not a release that is being served.

Every gate in `deploy/scangrade-deploy.sh` after the reload claims to measure
*this* release: the smoke test signs in as each role, the claims gate re-reads the
published capacity table, the perf gate compares a reference load with the last
release that passed. All of them have the same unstated premise — that the code
answering is the code that was just merged — and none of them can see it. A
`systemctl reload` that silently does nothing leaves the previous release serving,
every gate measures the *old* code against its own expectations, and a release
that landed and was never loaded is indistinguishable from a release that was.

That is not hypothetical: it is the shape the box was found in with the served
commit reading (the deploy-status page's own new card), where the checkout had
moved four commits and the process answering was a day and a half old. From the
outside the site was fine, because a box serving stale code *is* fine until the
code matters.

So the app publishes the commit it is serving on `/health`, and the deploy asks
one question before it trusts anything else: **is the commit you are serving the
one I just merged?**

The three answers are not two, and the difference is the whole design:

* **it is** — carry on;
* **it is not** — a measurement exists and it contradicts the merge. Refuse and
  quarantine, because this release is not being served and no gate below would
  have noticed;
* **you did not answer** — no measurement, and no measurement is not evidence of a
  bad release. A box whose app cannot be asked is a box problem, and the health
  probe above already owns reachability.

Because a gunicorn reload is graceful, a request arriving during the transition can
still be answered by a retiring worker. One probe saying "the old commit" is
therefore not a contradiction — it is the transition. The gate asks several times
and refuses only when **no** probe reported the merged commit, which is the same
posture the perf gate takes with a divergence a second probe did not confirm.

The refuser's exit code is deliberately not `1`: python exits `1` on an uncaught
exception, and a gate that crashes must never be read as a gate that refused.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GATE_PY = ROOT / "deploy" / "served_commit_gate.py"
RUNNER = ROOT / "deploy" / "scangrade-deploy.sh"
APP_INIT = ROOT / "app" / "__init__.py"
BUILD_INFO = ROOT / "app" / "utils" / "build_info.py"
GIT = shutil.which("git")
SERVICE = ROOT / "app" / "services" / "deploy_status_service.py"
TEMPLATE = ROOT / "app" / "templates" / "super_admin" / "deploy_status.html"

MERGED = "a32585a1b2c3d4e5f60718293a4b5c6d7e8f9012"
PREVIOUS = "0b20b204f5e6d7c8b9a0123456789abcdef01234"


def _load_gate():
    """The gate module, imported from its path rather than from a package.

    `deploy/` is not a package — the gates beside it are run as scripts by the
    runner — so the import is explicit. A missing module is the first failure this
    suite has to produce.
    """
    spec = importlib.util.spec_from_file_location("served_commit_gate", GATE_PY)
    assert spec and spec.loader, f"{GATE_PY} cannot be imported"
    module = importlib.util.module_from_spec(spec)
    #: Registered before it is executed because the module's dataclass resolves its
    #: own annotations through `sys.modules`; without this, `Reading` fails to build
    #: with a `NoneType` error that says nothing about the module being absent there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


# ── the app publishes what it serves ─────────────────────────────────────────

def _health_json(app):
    resp = app.test_client().get("/health")
    assert resp.status_code == 200
    return resp.get_json()


def test_health_publishes_the_commit_this_process_is_serving(app, monkeypatch):
    """The reading is the process's own, not the checkout's.

    The deployment this gate protects had a checkout four commits ahead of the
    process answering it, so a `/health` that read the checkout would have said
    "current" for the exact state the gate exists to catch. The value has to come
    from `app/utils/build_info.py`, which reads where the code lives, once.
    """
    from app.utils import build_info

    reading = {"available": True, "reason_key": None, "detail": None,
               "repo": "/opt/scangrade", "commit": MERGED[:7], "full_commit": MERGED,
               "subject": "something", "committed_at": "2026-09-30T00:00:00+00:00",
               "loaded_at": "2026-09-30T01:00:00+00:00", "pid": 4242}
    monkeypatch.setattr(build_info, "snapshot", lambda: dict(reading))

    doc = _health_json(app)
    assert doc["status"] == "ok", "the existing payload must survive this addition"
    assert doc["commit"]["full_commit"] == MERGED, (
        "/health does not publish the commit this process is serving, so the deploy "
        "has nothing to compare the merged commit with")
    assert doc["commit"]["commit"] == MERGED[:7]


def test_health_publishes_only_what_a_machine_check_needs(app, monkeypatch):
    """A public endpoint says the sha, not the commit message.

    `/health` is reachable without a session (nginx proxies it), and the reading it
    now carries is on the one path a deploy asks. Every extra field is published
    for good, so the projection is asserted rather than left to the module's own
    shape: a field added to `build_info` from here on has to be a decision.
    """
    from app.utils import build_info

    monkeypatch.setattr(build_info, "snapshot", lambda: {
        "available": True, "reason_key": None, "detail": None, "repo": "/opt/scangrade",
        "commit": MERGED[:7], "full_commit": MERGED, "subject": "a message",
        "committed_at": "2026-09-30T00:00:00+00:00",
        "loaded_at": "2026-09-30T01:00:00+00:00", "pid": 4242})
    published = _health_json(app)["commit"]
    assert set(published) == {"available", "reason_key", "commit", "full_commit",
                              "loaded_at"}, (
        "the served-commit block publishes fields a machine check does not need: "
        f"{sorted(published)}")
    assert "a message" not in json.dumps(published)


def test_the_app_carries_the_marker_the_gate_asks_about(gate):
    """The gate's `--reporter` question is answered by the app's own source.

    The gate asks a file whether the merged release ships the reporter, because
    only then is "no probe could name a commit" a contradiction rather than an old
    release that predates the reading. The two spell the marker in one place: this
    test reads it out of the gate and finds it in the app.
    """
    marker = getattr(gate, "REPORTER_MARKER", None)
    assert marker, "the gate names no marker for the file it asks about"
    source = APP_INIT.read_text(encoding="utf-8")
    assert marker in source, (
        f"the gate asks {APP_INIT.name} for {marker!r} and it is not there, so the "
        "gate can never tell a release that ships the reading from one that predates it")


def test_the_health_route_is_wired_to_the_published_reading():
    """A fence around a helper the route does not call would lie."""
    source = APP_INIT.read_text(encoding="utf-8")
    fenced = re.search(r"# served-commit:start\n(.*?)# served-commit:end",
                       source, re.S)
    assert fenced, "no fenced served-commit block in app/__init__.py"
    body = fenced.group(1)
    name = re.search(r"def (\w+)\(", body)
    assert name, "the fenced block declares no function"
    assert re.search(rf'"[a-z_]*commit[a-z_]*":\s*{name.group(1)}\(', source), (
        "the /health payload no longer calls the fenced reading, so the marker and "
        "the endpoint have drifted apart")


# ── the reading has to be pinned to the load, not to the first question ──────
#
# This is the one place the whole check can be defeated, and it is not obvious.
# `build_info` resolves its reading the first time it is *called*, so a worker that
# was never asked before a release merged resolves it afterwards — reads the
# checkout's new `HEAD`, and reports the merged commit while running the old code.
# The gate would then pass the exact state it exists to refuse, and it would go on
# passing: the memo is populated once, so the wrong answer is fixed for the life of
# that worker.
#
# The anchor that makes the reading true is the *import*: python had loaded these
# files at that moment, so the commit the checkout held then is the commit whose
# content is in memory.

def _checkout_with_the_module(tmp_path: Path) -> Path:
    """A real checkout holding a copy of `build_info`, alone.

    Neither the rest of the app nor this repository is needed: the module reads a
    `.git` and nothing else, and copying it is what lets a test move the checkout
    *after* the import — which is the whole point.
    """
    repo = tmp_path / "repo"
    (repo / "app" / "utils").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(BUILD_INFO, repo / "app" / "utils" / "build_info.py")
    subprocess.run([GIT, "init", "-q", str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run([GIT, "-C", str(repo), "config", key, value], check=True,
                       capture_output=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-q", "--allow-empty",
                    "-m", "the release this process loaded"], check=True,
                   capture_output=True)
    return repo


@pytest.mark.skipif(GIT is None, reason="needs git to read a checkout")
def test_a_checkout_that_moves_after_load_does_not_move_the_reading(tmp_path):
    """The reload that silently failed, from the *other* side.

    Import the module from a checkout, move that checkout on (as the deploy's merge
    does, while the old process keeps serving), and ask the process what it is
    serving. A reading resolved at first call answers with the commit the checkout
    has *now* — the one that was just merged — which is the false negative that
    would let a stale process satisfy the gate forever.
    """
    repo = _checkout_with_the_module(tmp_path)
    #: `read_commit` on its own, deliberately: `snapshot()` is the thing under test,
    #: and calling it before the move would populate the memo and test nothing.
    program = (
        "import subprocess, sys\n"
        f"sys.path.insert(0, {str(repo)!r})\n"
        "from app.utils import build_info\n"
        "at_load = build_info.read_commit(build_info.CODE_ROOT)['full_commit']\n"
        "subprocess.run(['git', '-C', " + repr(str(repo)) + ", 'commit', '-q',\n"
        "                '--allow-empty', '-m', 'the next release'], check=True)\n"
        "now = build_info.read_commit(build_info.CODE_ROOT)['full_commit']\n"
        "print(at_load, now, build_info.snapshot()['full_commit'])\n")
    run = subprocess.run([sys.executable, "-c", program], capture_output=True,
                         text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    at_load, now, served = run.stdout.split()
    assert at_load != now, "the harness failed to move the checkout"
    assert served == at_load, (
        f"the process reports {served[:7]} while it is running the code it loaded at "
        f"{at_load[:7]} — the reading followed the checkout, so a reload that did "
        "nothing reads as the merged commit")


def test_the_reading_is_resolved_before_anyone_asks(app):
    """The same rule, in-process: nothing enters the checkout to answer.

    `read_commit` is made to fail, so a reading taken at call time cannot hide
    behind a fast path. The memo is cleared first, because otherwise this test would
    be asserting on a value some earlier test already populated.
    """
    from app.utils import build_info

    build_info.snapshot.cache_clear()
    real = build_info.read_commit
    try:
        build_info.read_commit = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("the reading was taken when it was asked for"))
        served = build_info.snapshot()
    finally:
        build_info.read_commit = real
        build_info.snapshot.cache_clear()
    assert served["full_commit"], (
        "the reading is only available by reading the checkout now, so it describes "
        "the box rather than the process")


# ── reading one answer ───────────────────────────────────────────────────────

def test_a_body_with_a_commit_is_a_reading(gate):
    body = json.dumps({"status": "ok",
                       "commit": {"available": True, "commit": MERGED[:7],
                                  "full_commit": MERGED}})
    reading = gate.reading_of(body)
    assert reading.state == gate.READING_OK
    assert reading.commit == MERGED


def test_a_body_that_predates_the_reading_is_absent_not_unreadable(gate):
    """An app from before this release answers `/health` without the block.

    That is a different fact from a response the gate could not parse, and the two
    lead to different verdicts, so they must not share a state.
    """
    reading = gate.reading_of(json.dumps({"status": "ok", "redis": {"ok": True}}))
    assert reading.state == gate.READING_ABSENT


def test_a_reporter_that_cannot_place_itself_is_not_a_mismatch(gate):
    """`available: false` means the reporter ran and could not read its checkout.

    The reporter answering is itself proof that the reload took; what it cannot do
    is name the commit. Reading that as "a different commit" would refuse a release
    on a box whose git is missing — a box property dressed as a release defect.
    """
    body = json.dumps({"commit": {"available": False, "reason_key": "no_git",
                                  "commit": None, "full_commit": None}})
    reading = gate.reading_of(body)
    assert reading.state == gate.READING_UNNAMED


@pytest.mark.parametrize("body", ["", "not json", "[]", "null", "<html>502</html>"])
def test_a_body_that_is_not_the_payload_is_unreadable(gate, body):
    assert gate.reading_of(body).state == gate.READING_UNREADABLE


def test_judging_a_reading_named_and_matching(gate):
    reading = gate.Reading(gate.READING_OK, MERGED, "the app named it")
    verdict, why = gate.judge([reading], MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_OK
    assert MERGED[:7] in why


def test_judging_a_reading_named_and_different(gate):
    reading = gate.Reading(gate.READING_OK, PREVIOUS, "the app named it")
    verdict, why = gate.judge([reading], MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_MISMATCH, (
        "a readable commit that is not the merged one is the failure this gate "
        "exists for")
    assert PREVIOUS[:7] in why and MERGED[:7] in why


def test_judging_a_later_probe_that_names_the_merged_commit(gate):
    """The graceful reload's window: a retiring worker answering first.

    One probe naming a different commit is not a contradiction once another names
    the merged one — gunicorn finishes in-flight requests before retiring workers,
    so the first answer after a reload can legitimately come from the old code.
    """
    readings = [gate.Reading(gate.READING_OK, PREVIOUS, "old worker"),
                gate.Reading(gate.READING_OK, MERGED, "new worker")]
    verdict, why = gate.judge(readings, MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_OK, why


def test_an_unnamed_reporter_is_not_a_mismatch_even_when_the_release_ships_it(gate):
    reading = gate.Reading(gate.READING_UNNAMED, None, "it answered but could not read git")
    verdict, _ = gate.judge([reading], MERGED, reporter_ships=True)
    assert verdict == gate.VERDICT_UNMEASURED


def test_an_absent_block_is_unmeasured_only_for_a_release_that_predates_it(gate):
    """The marker is what turns silence into evidence."""
    reading = gate.Reading(gate.READING_ABSENT, None, "no commit block")
    assert gate.judge([reading], MERGED, reporter_ships=True)[0] == \
        gate.VERDICT_MISMATCH, (
        "the merged release ships the reading and the app answering does not, so the "
        "code being served is not this release — that is the reload that did not take")
    assert gate.judge([reading], MERGED, reporter_ships=False)[0] == \
        gate.VERDICT_UNMEASURED, (
        "a release that predates the reading cannot be asked to provide it, and "
        "refusing it would quarantine the release that introduces the check")


# ── the gate against a real endpoint ─────────────────────────────────────────

class _Box(BaseHTTPRequestHandler):
    """A loopback box whose `/health` answers what the test sets."""

    flags: dict = {"status": 200, "commits": [], "absent": False, "body": None}

    def do_GET(self):  # noqa: N802 - http.server's interface
        flags = self.flags
        if flags.get("body") is not None:
            payload = flags["body"]
        elif flags.get("absent"):
            payload = json.dumps({"status": "ok"})
        else:
            queue = list(flags.get("commits") or [])
            #: One sha per request, the last one repeating: that is a probe asking
            #: several times about a box that is or is not on the new code.
            commit = queue.pop(0) if len(queue) > 1 else (queue[0] if queue else None)
            flags["commits"] = queue or flags["commits"]
            block = ({"available": False, "commit": None, "full_commit": None}
                     if commit is None else
                     {"available": True, "commit": commit[:7], "full_commit": commit})
            payload = json.dumps({"status": "ok", "commit": block})
        raw = payload.encode("utf-8")
        self.send_response(int(flags.get("status", 200)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def box():
    holder = {}

    def start(**flags):
        handler = type("H", (_Box,), {"flags": dict(flags)})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        holder["server"] = server
        holder["flags"] = handler.flags
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    holder.get("server") and holder["server"].shutdown()


def run_gate(gate, base, monkeypatch, *, merged=MERGED, ships=True):
    monkeypatch.setattr(sys, "argv", [
        "served_commit_gate.py", "--base", base, "--commit", merged,
        "--reporter", str(APP_INIT if ships else ROOT / "deploy" / "no_such_file.py"),
        "--attempts", "4", "--gap", "0.05"])
    return gate.main()


class TestTheGateEndToEnd:
    def test_the_merged_commit_is_being_served(self, gate, box, monkeypatch, capsys):
        base = box(commits=[MERGED])
        assert run_gate(gate, base, monkeypatch) == gate.EXIT_OK
        out = capsys.readouterr().out
        assert MERGED[:7] in out

    def test_the_previous_commit_still_serving_is_a_refusal(self, gate, box, monkeypatch,
                                                            capsys):
        base = box(commits=[PREVIOUS])
        rc = run_gate(gate, base, monkeypatch)
        assert rc == gate.EXIT_REFUSED, capsys.readouterr().out
        out = capsys.readouterr().out
        assert PREVIOUS[:7] in out and MERGED[:7] in out, (
            "the refusal has to name both commits: 'a different commit' is not "
            "something an operator can act on")

    def test_the_refusal_code_is_not_the_one_a_crash_produces(self, gate):
        assert gate.EXIT_REFUSED != 1, (
            "python exits 1 on an uncaught exception, so a gate that crashes would "
            "be read by the runner as a gate that refused")

    def test_a_transition_answered_by_a_retiring_worker_passes(self, gate, box,
                                                               monkeypatch, capsys):
        base = box(commits=[PREVIOUS, MERGED])
        assert run_gate(gate, base, monkeypatch) == gate.EXIT_OK, capsys.readouterr().out

    def test_an_app_from_before_the_reading_is_not_refused(self, gate, box, monkeypatch,
                                                           capsys):
        """The release that *introduces* the gate must be able to land.

        If silence were a refusal, the commit that ships this check would quarantine
        itself on every box — the first landing could never happen.
        """
        base = box(absent=True)
        assert run_gate(gate, base, monkeypatch, ships=False) == gate.EXIT_UNMEASURED
        assert "predate" in capsys.readouterr().out.lower()

    def test_silence_from_a_release_that_ships_the_reading_is_a_refusal(self, gate, box,
                                                                        monkeypatch,
                                                                        capsys):
        base = box(absent=True)
        assert run_gate(gate, base, monkeypatch, ships=True) == gate.EXIT_REFUSED, \
            capsys.readouterr().out

    def test_an_unreachable_app_is_not_a_refusal(self, gate, monkeypatch, capsys):
        """No answer is not evidence of a bad release."""
        rc = run_gate(gate, "http://127.0.0.1:1", monkeypatch)
        assert rc == gate.EXIT_UNMEASURED, capsys.readouterr().out
        assert "did not answer" in capsys.readouterr().out.lower(), (
            "the journal has to say the app was not asked successfully, not merely "
            "that something was not measured")

    def test_a_missing_commit_to_compare_against_is_not_a_refusal(self, gate, box,
                                                                  monkeypatch, capsys):
        base = box(commits=[MERGED])
        assert run_gate(gate, base, monkeypatch, merged="") == gate.EXIT_UNMEASURED
        assert "no commit to compare" in capsys.readouterr().out.lower(), (
            "a run with nothing to compare against must say so rather than pass")

    def test_the_gate_probes_more_than_once(self, gate, box, monkeypatch):
        """A single probe would refuse a healthy release on the reload's own window."""
        seen = []
        base = box(commits=[PREVIOUS, PREVIOUS, MERGED])
        original = _Box.do_GET

        def counting(self):
            seen.append(1)
            return original(self)

        monkeypatch.setattr(_Box, "do_GET", counting)
        assert run_gate(gate, base, monkeypatch) == gate.EXIT_OK
        assert len(seen) >= 2, "the gate asked once and took the first answer as final"


# ── the runner runs it, and acts on the answer ───────────────────────────────

def _gate_block() -> str:
    script = RUNNER.read_text(encoding="utf-8")
    assert "# served-commit-gate:start" in script and \
        "# served-commit-gate:end" in script, (
        "the runner's served-commit block has no delimiters, so it cannot be lifted "
        "out and tested on its own the way the other gates' are")
    return script.split("# served-commit-gate:start", 1)[1].split(
        "# served-commit-gate:end", 1)[0]


def test_the_runner_asks_the_app_what_it_is_serving():
    script = RUNNER.read_text(encoding="utf-8")
    assert "deploy/served_commit_gate.py" in script, (
        "nothing in the runner compares the served commit with the merged one")
    block = _gate_block()
    assert re.search(r'--commit\s+"\$?AFTER_FULL', block), (
        "the gate is not told which commit this run merged, so it cannot compare")
    assert re.search(r'--reporter\s+"\$?REPO/app/__init__\.py"', block), (
        "the gate is not told which file to ask whether the release ships the "
        "reading, so silence can never be evidence")
    assert "127.0.0.1:$APP_PORT" in block, (
        "the check must ask the box's own app, not a public URL: nginx is not the "
        "process whose code was reloaded")


def test_the_check_runs_after_the_reload_and_before_anything_it_protects():
    """Every later gate measures "the code now serving". This is the premise.

    Placed before the reload it would describe the previous release; placed after
    the smoke test it would let the first three gates report on code that is not
    the release being judged.
    """
    script = RUNNER.read_text(encoding="utf-8")
    at = script.index("# served-commit-gate:start")
    assert script.index("reload_app\n") < at, "the check runs before the reload"
    assert at < script.index('RUN_STEP="verify"'), (
        "the check runs after the smoke gate, so that gate can report on code the "
        "reload never replaced")
    assert script.index('HEALTHY=0\nif systemctl is-active') < at, (
        "the check must run only once the app is known to be up: an app that never "
        "came up is the health probe's finding, not this one's")


def test_a_mismatch_is_refused_and_quarantined():
    """The refusal has to reach the shared rollback path, or nothing happens.

    The block runs after the reload, so the shared path at the end of the script is
    what quarantines the commit, rolls the checkout back and reloads the previous
    release. That means the block's only job is to set `HEALTHY=0` and name the
    gate.
    """
    block = _gate_block()
    assert re.search(r"HEALTHY=0", block), (
        "a mismatch does not mark the release unhealthy, so the box keeps serving "
        "code it never loaded and every later gate measures the old release")
    reason = re.search(r'FAIL_REASON="([^"\n]*)"', block)
    assert reason, "the refusal names no gate, so the quarantine record says nothing"
    service = SERVICE.read_text(encoding="utf-8")
    keys = re.search(r"GATE_KEYS = frozenset\(\{(.*?)\}\)", service, re.S).group(1)
    slug = re.sub(r"[^a-z0-9]+", "_", reason.group(1).split("(", 1)[0].strip().lower())
    assert f'"{slug}"' in keys, (
        f"the runner records {reason.group(1)!r}, which the status page classifies "
        f"as {gate_key_of(service, reason.group(1))!r} — the gate has no sentence")


def gate_key_of(service_source: str, reason: str) -> str:
    """`gate_key`'s rule, applied here so the assertion above can name what it saw."""
    keys = set(re.findall(r'"([a-z0-9_]+)"',
                          re.search(r"GATE_KEYS = frozenset\(\{(.*?)\}\)",
                                    service_source, re.S).group(1)))
    slug = re.sub(r"[^a-z0-9]+", "_", reason.split("(", 1)[0].strip().lower()).strip("_")
    return slug if slug in keys else "unknown"


def test_the_page_can_say_which_gate_this_is():
    """A key with no sentence reads as "unknown" to the operator."""
    template = TEMPLATE.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    assert re.search(r'["\']served_commit["\']',
                     re.search(r"GATE_KEYS = frozenset\(\{(.*?)\}\)", service, re.S).group(1)), (
        "the new gate has no key, so `gate_key` classifies its refusal as unknown")
    assert "q.gate_key == 'served_commit'" in template, (
        "the deploy-status page has no sentence for the served-commit gate, so a "
        "refusal by it is shown as an unnamed gate")


# ── and the runner's own branching, run for real ─────────────────────────────
#
# The gate's verdicts are proven above; this proves the runner does the right thing
# with them. Reading the block would not: "the mismatch arm sets HEALTHY=0" is a
# claim about one branch out of three, and the branch that matters most is the one
# that must *not* — a cannot-measure answer that rolls a healthy release back is the
# false positive this whole gate has to avoid.

BASH = shutil.which("bash")

#: What the gate prints on a refusal, as the block captures it.
REFUSAL_LINES = ("served commit: REFUSED — none of 4 probes reported a32585a; "
                 "the app reports 0b20b20 (4 of 4)")


def _runner_harness(tmp_path: Path) -> Path:
    """The real block, lifted out of the script, with a fake gate behind it."""
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "deploy").mkdir()
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("# served-commit:start\n", encoding="utf-8")
    (repo / ".venv" / "bin" / "python").write_text(
        "#!/usr/bin/env bash\n"
        '# The gate is not run here: its verdict is the input. The flags the block\n'
        '# passes are written to a file rather than printed, because the block\n'
        '# captures this command\'s output — a test asserting on stdout would be\n'
        '# asserting on what the runner swallowed.\n'
        'printf "%s\\n" "$*" > "${SERVED_FAKE_ARGS}"\n'
        'cat "${SERVED_FAKE_OUT}"\n'
        'exit "${SERVED_FAKE_RC}"\n',
        encoding="utf-8", newline="\n")
    (repo / ".venv" / "bin" / "python").chmod(0o755)
    return repo


def _run_block(tmp_path: Path, repo: Path, rc: int, out: str):
    body = tmp_path / "gate-out.txt"
    body.write_text(out + ("\n" if out else ""), encoding="utf-8")
    program = (
        f'export SERVED_FAKE_ARGS="{tmp_path.as_posix()}/called-with.txt"\n'
        "set -uo pipefail\n"
        f'REPO="{repo.as_posix()}"\n'
        'APP_PORT=8000\n'
        # The block names the unit in the gate's `--unit`, so the record's remedy
        # can say which service to restart; the harness must define it as the
        # runner does, or `set -u` aborts the block.
        'SERVICE="scangrade"\n'
        f'AFTER_FULL="{MERGED}"\n'
        f'BEFORE="{PREVIOUS[:7]}"\n'
        'HEALTHY=1\nFAIL_REASON=""\nFAIL_DETAIL=""\nRUN_STEP=""\n'
        'log() { echo "LOG: $*"; }\n'
        'as_owner() { "$@"; }\n'
        f'export SERVED_FAKE_OUT="{body.as_posix()}"\n'
        f'export SERVED_FAKE_RC={rc}\n'
        + _gate_block()
        + '\nprintf "AFTER healthy=%s reason=[%s] detail=[%s] step=[%s]\\n" '
          '"$HEALTHY" "$FAIL_REASON" "$FAIL_DETAIL" "$RUN_STEP"\n')
    script = tmp_path / "served-block.sh"
    script.write_text(program, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True)


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the runner's block")
class TestTheRunnerActsOnTheVerdict:
    def test_a_pass_leaves_the_release_healthy(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 0,
                         "served commit: OK — the app is serving a32585a")
        assert run.returncode == 0, run.stderr
        assert "reason=[]" in run.stdout and "healthy=1" in run.stdout, run.stdout

    def test_a_mismatch_marks_the_release_unhealthy_and_names_the_gate(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 3, REFUSAL_LINES)
        assert run.returncode == 0, run.stderr
        assert "healthy=0" in run.stdout, (
            "a mismatch does not reach the shared rollback path, so the release is "
            "kept although the code serving is not the code that merged")
        assert "served commit (" in run.stdout, run.stdout
        assert "0b20b20" in run.stdout and "a32585a" in run.stdout, (
            "the refusal's own lines are not quoted into the record, so the "
            "operator is told a gate refused without being told what it measured")
        assert "step=[served]" in run.stdout, (
            "the refusal names no step, so the last-stop record cannot say where "
            "the run died")

    def test_the_call_it_makes_names_the_merged_commit_and_the_reporter(self, tmp_path):
        _run_block(tmp_path, _runner_harness(tmp_path), 0, "served commit: OK")
        called = (tmp_path / "called-with.txt").read_text(encoding="utf-8")
        assert f"--commit {MERGED}" in called, (
            "the gate is not told which commit this run merged: " + called)
        assert "--reporter" in called and "app/__init__.py" in called, (
            "the gate is not told which file to ask whether the release ships the "
            "reading: " + called)

    def test_a_could_not_measure_answer_keeps_the_release(self, tmp_path):
        run = _run_block(tmp_path, _runner_harness(tmp_path), 2,
                         "served commit: CANNOT MEASURE — the app could not be asked")
        assert run.returncode == 0, run.stderr
        assert "healthy=1" in run.stdout and "reason=[]" in run.stdout, (
            "a box whose app cannot be asked rolls the release back — a box property "
            "quarantining a commit: " + run.stdout)

    def test_a_gate_that_crashed_is_not_read_as_a_refusal(self, tmp_path):
        """python exits 1 on an uncaught exception; that must not refuse a release."""
        run = _run_block(tmp_path, _runner_harness(tmp_path), 1,
                         "Traceback (most recent call last):\nValueError: boom")
        assert run.returncode == 0, run.stderr
        assert "healthy=1" in run.stdout and "reason=[]" in run.stdout, (
            "the gate crashed and the runner read it as a refusal: " + run.stdout)
        assert "ValueError: boom" in run.stdout, (
            "the crash is not quoted, so the journal says nothing about why the "
            "check produced no verdict")
