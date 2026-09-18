"""The box reports its own per-process CPU, because this box has no shell here.

"Tens of milliseconds of CPU per request" is only useful once it can be
*attributed*. `vmstat` is box-wide. `/metrics` is the app's own view. The box's
loopback sampler (`docs/measurements/rung-500-vps-samples.txt`) records load and
memory. And `pidstat`/`top` need the SSH this machine is refused. So the split
has to come from the app, over HTTP, reading `/proc` for every process it can
see — which is what `app/services/process_sampler.py` and the
`/metrics/processes` route do.

What this guard is protecting, and why each one is here:

* **The reading is cumulative, not a percentage.** A single percentage is
  meaningless — `psutil` derives one only against that same object's previous
  call, so a fresh `Process` per request reports 0% forever — and the interval
  belongs to the reader. If this ever answers in percentages, every attribution
  computed from it is wrong.
* **Nothing blocks inside a request.** `psutil.cpu_percent(interval=...)` sleeps
  for the interval it is given, and a tenth of a second per sample would make the
  instrument part of what it measures. That one is asserted against the parsed
  syntax tree rather than the file text, because the module's own docstring
  explains the trap by naming the call — a text search would fail on the
  explanation and pass on nothing. (The same mistake, in reverse, is recorded in
  `AGENTS.md`: a guard whose needle also appeared in a docstring proved nothing.)
* **A process that cannot be read is skipped, never zeroed.** A zero reads as
  "this process used no CPU", and a reader dividing by requests would believe it.
* **The route is a super-admin read.** A process inventory is not something a
  signed-in student should be able to enumerate.
* **`psutil` is pinned.** `app/__init__.py` and the sampler both import it
  directly, but until this pin it arrived only as a `locust` dependency — so a
  venv built without locust would 500 both `/metrics` and `/metrics/processes`.

Mutation-checked, **12/12 injected defects caught**: an unescaped quote in a
label, a role total turned into a maximum, an unreadable process reported as a
zero rather than skipped, the route losing its super-admin guard, a
`cpu_percent(interval=…)` call, a `sleep`, the `psutil` pin deleted, the start
time dropped, the ordering reversed, the counter renamed to a percentage, the
role read from the name alone, and every role collapsed to `other`.
"""

from __future__ import annotations

import ast
import contextlib
import pathlib
import re
import types

import psutil
import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SAMPLER = REPO / "app" / "services" / "process_sampler.py"
INIT = REPO / "app" / "__init__.py"

import app.services.process_sampler as sampler  # noqa: E402


def reading(pid, comm, role, cpu, *, rss=0, threads=1, created=0.0) -> dict:
    return {"pid": pid, "comm": comm, "role": role, "cpu_seconds": cpu,
            "rss_bytes": rss, "threads": threads, "create_time": created}


# ── the reading is cumulative ────────────────────────────────────────────────

class TestTheReadingIsCumulative:
    def test_a_counter_is_carried_through_as_seconds(self):
        out = sampler.render([reading(11, "gunicorn", "gunicorn", 12.5)],
                            cpu_count=1, now=1000.0)
        assert "scangrade_process_cpu_seconds{" in out
        assert "} 12.500000" in out, "the counter must survive the render unchanged"

    def test_nothing_is_reported_as_a_percentage(self):
        """A percentage here would be a number with no interval behind it."""
        out = sampler.render([reading(11, "gunicorn", "gunicorn", 12.5)],
                            cpu_count=1, now=1000.0)
        assert "percent" not in out.lower(), (
            "the sampler is cumulative CPU seconds; a percent gauge has no interval "
            "and a fresh Process would report 0 forever")
        assert "%" not in out

    def test_the_start_time_travels_with_the_counter(self):
        """gunicorn's reload restarts workers; a reset counter must be visible."""
        out = sampler.render([reading(11, "gunicorn", "gunicorn", 1.0, created=99.0)],
                            cpu_count=1, now=1000.0)
        assert re.search(r'scangrade_process_create_time_seconds\{[^}]*pid="11"[^}]*\} 99\.000000', out), (
            "without the start time a restarted worker's smaller counter is "
            "indistinguishable from a process that went quiet")

    def test_the_box_time_and_cpu_count_are_reported(self):
        out = sampler.render([], cpu_count=1, now=1789388398.5)
        assert "scangrade_sample_epoch_seconds 1789388398.500000" in out
        assert "scangrade_box_cpu_count 1" in out


# ── the role the reader reasons about ────────────────────────────────────────

class TestTheRole:
    @pytest.mark.parametrize("comm,role", [
        ("nginx", "nginx"),
        ("gunicorn", "gunicorn"),
        ("redis-server", "redis"),
        ("celery", "celery"),
        ("sshd", "other"),
        ("systemd", "other"),
    ])
    def test_a_role_comes_from_the_name(self, comm, role):
        assert sampler.role_of(comm) == role

    def test_a_role_also_comes_from_the_command_line(self):
        """Celery sets its title through setproctitle, so its comm is sometimes python3."""
        assert sampler.role_of("python3", "/usr/local/bin/celery -A app worker") == "celery"
        assert sampler.role_of("python", "gunicorn -k gevent -w 3 app:app") == "gunicorn"

    def test_role_totals_are_the_sum_of_their_processes(self):
        out = sampler.render([
            reading(1, "gunicorn", "gunicorn", 1.0),
            reading(2, "gunicorn", "gunicorn", 2.5),
            reading(3, "nginx", "nginx", 0.25),
        ], cpu_count=1, now=0.0)
        assert 'scangrade_role_cpu_seconds{role="gunicorn"} 3.500000' in out
        assert 'scangrade_role_cpu_seconds{role="nginx"} 0.250000' in out

    def test_a_known_process_is_not_silently_reclassified(self):
        out = sampler.render([reading(1, "nginx", "nginx", 2.0),
                              reading(2, "sshd", "other", 9.0)], cpu_count=1, now=0.0)
        assert 'scangrade_role_cpu_seconds{role="other"} 9.000000' in out
        assert 'scangrade_role_cpu_seconds{role="nginx"} 2.000000' in out


# ── nothing is dropped, and nothing is invented ──────────────────────────────

class TestNothingIsDropped:
    def test_every_process_appears(self):
        rows = [reading(p, "gunicorn", "gunicorn", float(p)) for p in (1, 2, 3)]
        out = sampler.render(rows, cpu_count=1, now=0.0)
        for pid in (1, 2, 3):
            assert f'pid="{pid}"' in out, "a process that used CPU must be visible"

    def test_the_worst_consumer_is_first(self):
        out = sampler.render([reading(1, "nginx", "nginx", 0.1),
                              reading(2, "gunicorn", "gunicorn", 9.0)],
                             cpu_count=1, now=0.0)
        lines = [l for l in out.splitlines() if l.startswith("scangrade_process_cpu_seconds{")]
        assert 'pid="2"' in lines[0], "the report should lead with the biggest consumer"

    def test_a_label_cannot_break_the_format(self):
        out = sampler.render([reading(7, 'we"ird\\proc', "other", 1.0)],
                             cpu_count=1, now=0.0)
        assert 'comm="we\\"ird\\\\proc"' in out, (
            "a quote or backslash in a process name must be escaped, or every "
            "later metric on the line is unparseable")


# ── a process that cannot be read ────────────────────────────────────────────

class _Fake:
    """One process as `collect()` reads it, with a field it may refuse."""

    def __init__(self, pid, name, *, cmdline=(), cpu=(0.0, 0.0), rss=1024,
                 threads=1, created=1.0, refuses=None):
        self.info = {"pid": pid, "name": name}
        self._cmdline = list(cmdline)
        self._cpu = cpu
        self._rss = rss
        self._threads = threads
        self._created = created
        self.refuses = refuses

    def _refuse(self, field):
        if self.refuses == field:
            raise psutil.NoSuchProcess(pid=self.info["pid"])

    def cmdline(self):
        if self.refuses == "cmdline":
            raise psutil.AccessDenied(pid=self.info["pid"])
        return self._cmdline

    @contextlib.contextmanager
    def oneshot(self):
        yield self

    def cpu_times(self):
        self._refuse("cpu")
        return types.SimpleNamespace(user=self._cpu[0], system=self._cpu[1])

    def memory_info(self):
        self._refuse("rss")
        return types.SimpleNamespace(rss=self._rss)

    def num_threads(self):
        self._refuse("threads")
        return self._threads

    def create_time(self):
        self._refuse("created")
        return self._created


def as_enumerator(rows):
    def process_iter(attrs=None):        # noqa: ARG001 - mirrors psutil's signature
        return iter(rows)
    return process_iter


class TestAProcessThatCannotBeRead:
    def test_it_is_left_out_rather_than_reported_as_zero(self):
        rows = [_Fake(1, "gunicorn", cpu=(2.0, 0.5)),
                _Fake(2, "gunicorn", refuses="cpu"),
                _Fake(3, "nginx", cpu=(1.0, 0.0))]
        got = sampler.collect(process_iter=as_enumerator(rows))
        pids = {r["pid"] for r in got}
        assert pids == {1, 3}, (
            "a process whose CPU could not be read must be absent, not present "
            "with a zero — a zero reads as 'used nothing'")
        assert all(r["cpu_seconds"] > 0 for r in got)

    def test_the_counter_is_user_plus_system(self):
        got = sampler.collect(process_iter=as_enumerator([_Fake(1, "nginx", cpu=(2.0, 0.5))]))
        assert got[0]["cpu_seconds"] == pytest.approx(2.5)

    def test_a_command_line_that_is_refused_still_yields_a_reading(self):
        got = sampler.collect(process_iter=as_enumerator(
            [_Fake(4, "nginx", cpu=(1.0, 0.0), refuses="cmdline")]))
        assert len(got) == 1
        assert got[0]["role"] == "nginx", "the name alone still decides the role"

    def test_one_bad_process_does_not_lose_the_others(self):
        rows = [_Fake(1, "nginx", cpu=(1.0, 0.0)),
                _Fake(2, "nginx", refuses="created"),
                _Fake(3, "redis-server", cpu=(0.4, 0.0))]
        got = sampler.collect(process_iter=as_enumerator(rows))
        assert {r["pid"] for r in got} == {1, 3}


# ── nothing blocks inside a request ─────────────────────────────────────────

class TestNothingBlocks:
    """Read from the syntax tree, so the docstring may keep naming the trap."""

    @pytest.fixture(scope="class")
    def tree(self):
        return ast.parse(SAMPLER.read_text(encoding="utf-8"))

    def test_it_never_asks_for_a_percentage(self, tree):
        used = [n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and n.attr == "cpu_percent"]
        assert not used, (
            "cpu_percent(interval=...) sleeps inside the request and a fresh "
            "Process would report 0 anyway; the reader owns the interval")

    def test_it_never_sleeps(self, tree):
        sleeps = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "sleep"]
        assert not sleeps, "the instrument must not add load to what it measures"


# ── the route, and who may read it ───────────────────────────────────────────

class TestTheRoute:
    @pytest.fixture(scope="class")
    def app(self):
        from app import create_app
        return create_app("app.config.TestingConfig")

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @staticmethod
    def _sign_in(monkeypatch, role):
        def session_for(token):                       # noqa: ARG001
            return {"user_id": "u-1", "role": role, "status": "active",
                    "email": "someone@example.id", "name": "Someone",
                    "school_id": None, "class_id": None}
        monkeypatch.setattr("app.utils.auth._session_for", session_for)

    @staticmethod
    def _present_a_token(client):
        """Hand the client an `access_token`.

        Not a `Cookie` header: Werkzeug's test client strips a hand-set one, so a
        request built that way reaches the app with no cookie at all and every
        guard looks as though it refused for the wrong reason.
        """
        client.set_cookie("access_token", "a-token")

    def test_the_route_exists(self, app):
        rules = {str(r) for r in app.url_map.iter_rules()}
        assert "/metrics/processes" in rules

    def test_a_super_admin_gets_the_reading(self, client, monkeypatch):
        self._sign_in(monkeypatch, "super_admin")
        monkeypatch.setattr(sampler, "sample_text", lambda: "scangrade_box_cpu_count 1\n")
        self._present_a_token(client)
        r = client.get("/metrics/processes")
        assert r.status_code == 200
        assert r.data == b"scangrade_box_cpu_count 1\n"
        assert r.headers["Content-Type"].startswith("text/plain")

    def test_a_student_is_sent_away(self, client, monkeypatch):
        """The whole point of a separate route: this is not a student's reading."""
        self._sign_in(monkeypatch, "murid")
        monkeypatch.setattr(sampler, "sample_text",
                            lambda: pytest.fail("a student must not reach the sampler"))
        self._present_a_token(client)
        r = client.get("/metrics/processes")
        assert r.status_code == 302
        assert "/student/dashboard" in r.headers["Location"]

    def test_a_teacher_is_sent_away_too(self, client, monkeypatch):
        self._sign_in(monkeypatch, "guru")
        monkeypatch.setattr(sampler, "sample_text",
                            lambda: pytest.fail("a teacher must not reach the sampler"))
        self._present_a_token(client)
        r = client.get("/metrics/processes")
        assert r.status_code == 302
        assert "/teacher/dashboard" in r.headers["Location"]

    def test_no_session_is_sent_to_a_login_door(self, client):
        r = client.get("/metrics/processes")
        assert r.status_code == 302
        assert "/auth/login" in r.headers["Location"]

    def test_the_route_carries_the_super_admin_guard_and_not_a_lesser_one(self):
        src = INIT.read_text(encoding="utf-8")
        block = src.split('@app.route("/metrics/processes")', 1)[1]
        block = block.split("def metrics_processes", 1)[0]
        assert "@super_admin_required" in block, (
            "the guard must be the super-admin one; a bare @login_required would "
            "hand a process inventory to every signed-in student")


# ── the dependency the whole thing stands on ─────────────────────────────────

class TestTheDependencyIsPinned:
    def test_psutil_is_pinned_because_app_code_imports_it_directly(self):
        pinned = re.search(r"(?m)^psutil==",
                           (REPO / "requirements.txt").read_text(encoding="utf-8"))
        assert pinned, (
            "app/__init__.py and the sampler import psutil directly, so it must be "
            "a declared dependency rather than a transitive one that a venv built "
            "without locust would not have")

    def test_both_importers_are_still_there(self):
        """If the imports go, this pin should go with them — and not silently first."""
        assert "import psutil" in INIT.read_text(encoding="utf-8")
        assert "import psutil" in SAMPLER.read_text(encoding="utf-8")
