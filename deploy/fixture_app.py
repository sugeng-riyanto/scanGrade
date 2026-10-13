#!/usr/bin/env python3
"""The shipped pages, served from this checkout with no database behind them.

Why this exists
---------------
Three measurements in this tree point a real browser at *this* app — the exam
builder's 44px finger floor (`deploy/touch_gate.py`), the pupil's exam page
(`deploy/exam_render_gate.py`) and the handshake page's own script (the client half
of `deploy/seb_door_gate.py`) — and until now all three ran **only on the deploy
box**, against the live database, after a release had already been fetched. A
regression in any of them was therefore found by the box on a release that was
already pulled, and a laptop could not run them at all: the gates sign in over
HTTPS as accounts that only the box has, on a database only the box can reach.

This module is the other half of that: the same app, built by its own
``create_app``, on ``127.0.0.1``, with a database that is a dict. The pages a CI
run measures are then the shipped pages — the shipped routes, the shipped
templates, the shipped static assets — reached the way a browser reaches them.

What is faked, and what is not
------------------------------
**Faked, and nothing else**: the Supabase service client
(``app.extensions["supabase"]``) and the GoTrue client
(``app.extensions["supabase_auth"]``). Both are in-memory objects with the small
surface this app uses (``table().select().eq().execute()``, ``insert``, ``update``,
``rpc``, ``auth.sign_in_with_password``, ``auth.get_user``).

**Not faked**: the app factory and its config, the blueprints, the decorators and
the session logic — including the real ``/auth/login-user`` POST, which is how the
gates sign in. A gate that could only measure a special "fixture mode" of the page
would be measuring something other than the release, so there is no fixture mode:
the app under this server is the app, and the sign-in is the app's own.

The one seeded paper is the demo fixture
----------------------------------------
``deploy/demo_exam_fixture.py`` is the spec the deploy writes its demo paper from
and the spec `deploy/exam_render_gate.py` reads to know which question it is
looking for. Seeding this fixture *from that spec* is what makes the CI run and the
box's run the same measurement: an exception question (``mcq_multi``) on the paper,
anti-cheat and fullscreen on, no window. A release that drops the exception from the
spec fails the render gate in CI for the same reason it fails on the box.

The accounts, and why they are constants here
---------------------------------------------
``guru@fixture.scangrade.test`` / ``murid@fixture.scangrade.test`` with one password,
seeded in this file. They exist nowhere else: the fake Auth client answers for them
and for nobody, so this module cannot sign anyone into anything real. The email
domain is reserved for exactly this (``.test`` never resolves).

Usage
-----
::

    from deploy import fixture_app

    server = fixture_app.serve()           # picks a free port
    try:
        ...                                # server.base_url is what a gate dials
    finally:
        server.stop()
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

#: The accounts this fixture serves, and the one password both of them take. The
#: gates are handed these as ``email:password`` — the same string shape the deploy
#: passes them on the box, so no gate needs a second sign-in path.
TEACHER_EMAIL = "guru@fixture.scangrade.test"
PUPIL_EMAIL = "murid@fixture.scangrade.test"
PASSWORD = "fixture-password"

#: Stable ids, readable in a failure: ``SCHOOL_ID[0]`` is always the same character,
#: so a finding that names one is a finding anyone can grep for in a seed.
SCHOOL_ID = "aaaaaaaa-0000-4000-8000-000000000001"
CLASS_ID = "aaaaaaaa-0000-4000-8000-000000000002"
SUBJECT_ID = "aaaaaaaa-0000-4000-8000-000000000003"
TEACHER_ID = "aaaaaaaa-0000-4000-8000-000000000004"
PUPIL_ID = "aaaaaaaa-0000-4000-8000-000000000005"
ADMIN_ID = "aaaaaaaa-0000-4000-8000-000000000006"
#: The demo fixture's own paper — ungated, so the render gate can open it — and a
#: second copy of it with the SEB door armed, which is the paper the handshake half
#: needs (a gated paper is the only one that sends a browser to the claim page).
EXAM_ID = "aaaaaaaa-0000-4000-8000-000000000007"
GATED_EXAM_ID = "aaaaaaaa-0000-4000-8000-000000000008"

#: A JWT-shaped access token with an expiry far enough away that the app's own
#: `_jwt_expired` never reads it as stale. The signature is never checked by
#: anything here *or* on the box (Supabase verifies it, and there is no Supabase) —
#: but the *payload* is read by this app, so its `exp` has to be real.
JWT_EXP = 4102444800  # 2100-01-01


def _token(user_id: str, email: str) -> str:
    """A JWT-shaped token carrying the two claims the app reads off it."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": user_id, "email": email, "role": "authenticated",
        "aud": "authenticated", "exp": JWT_EXP,
    }).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.fixture-signature"


# ── the database ─────────────────────────────────────────────────────────────
#
# One in-memory store, and the part of postgrest this app actually speaks. Rows are
# plain dicts, so an embedded resource (`subjects(id,name)`) is simply a dict inside
# the row — the shape PostgREST returns — rather than a join this would have to
# implement.

_MISSING = object()


def _loose(value):
    """A value comparable across the shapes the app writes.

    PostgREST compares strings against numbers by coercion and the app relies on it:
    a route asks ``.eq("id", "1")`` for an integer column, and a fake that compared
    strictly would answer "no row" for a row that is really there — the same silent
    absence the real thing never produces.
    """
    if isinstance(value, bool) or value is None:
        return value
    return str(value)


def _matches(row: dict, filters) -> bool:
    for column, want, op in filters:
        have = row.get(column, _MISSING)
        if have is _MISSING:
            return False
        if op == "eq":
            if _loose(have) != _loose(want):
                return False
        elif op == "neq":
            if _loose(have) == _loose(want):
                return False
        elif op == "in":
            if _loose(have) not in [_loose(v) for v in want]:
                return False
        elif op == "is":
            if want is None and have is not None:
                return False
            if want is not None and _loose(have) != _loose(want):
                return False
        elif op == "gt" and not (_loose(have) > _loose(want)):
            return False
        elif op == "gte" and not (_loose(have) >= _loose(want)):
            return False
        elif op == "lt" and not (_loose(have) < _loose(want)):
            return False
        elif op == "lte" and not (_loose(have) <= _loose(want)):
            return False
        elif op == "like" and str(want).strip("%").lower() not in str(have).lower():
            return False
    return True


class _Query:
    """Enough of a postgrest builder for this app's reads and writes."""

    def __init__(self, store: dict, name: str):
        self._store, self._name = store, name
        self._filters: list[tuple[str, object, str]] = []
        self._one = False
        self._pending: tuple[str, object] | None = None
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    # the builder methods the app chains, each returning self
    def select(self, *columns, **kwargs):
        return self

    def eq(self, column, value):
        self._filters.append((column, value, "eq"))
        return self

    def neq(self, column, value):
        self._filters.append((column, value, "neq"))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values or []), "in"))
        return self

    def is_(self, column, value):
        self._filters.append((column, value, "is"))
        return self

    def gt(self, column, value):
        self._filters.append((column, value, "gt"))
        return self

    def gte(self, column, value):
        self._filters.append((column, value, "gte"))
        return self

    def lt(self, column, value):
        self._filters.append((column, value, "lt"))
        return self

    def lte(self, column, value):
        self._filters.append((column, value, "lte"))
        return self

    def like(self, column, value):
        self._filters.append((column, value, "like"))
        return self

    def ilike(self, column, value):
        return self.like(column, value)

    def filter(self, column, operator, value):
        self._filters.append((column, value, {"ilike": "like"}.get(operator, operator)))
        return self

    def order(self, column, desc=False, **kwargs):
        self._order = (column, bool(desc))
        return self

    def limit(self, count):
        self._limit = int(count)
        return self

    def range(self, *_a, **_k):
        return self

    def single(self):
        self._one = True
        return self

    def maybe_single(self):
        # A miss is a row-shaped `None`, never an empty list, and `row_or_none()`
        # above it depends on that shape.
        self._one = True
        return self

    def insert(self, payload):
        self._pending = ("insert", payload)
        return self

    def upsert(self, payload, **kwargs):
        self._pending = ("upsert", payload)
        return self

    def update(self, patch):
        self._pending = ("update", patch)
        return self

    def delete(self):
        self._pending = ("delete", None)
        return self

    def _rows(self) -> list[dict]:
        return list(self._store.setdefault(self._name, []))

    def _hits(self) -> list[dict]:
        found = [row for row in self._rows() if _matches(row, self._filters)]
        if self._order:
            column, desc = self._order
            found.sort(key=lambda row: (row.get(column) is None,
                                        str(row.get(column, "")), ), reverse=desc)
        if self._limit is not None:
            found = found[: self._limit]
        return found

    def execute(self):
        if self._pending:
            kind, payload = self._pending
            self._pending = None
            if kind in ("insert", "upsert"):
                rows = payload if isinstance(payload, list) else [payload]
                written = []
                for item in rows:
                    row = dict(item)
                    row.setdefault("id", str(uuid.uuid4()))
                    row.setdefault("created_at", _now())
                    self._store.setdefault(self._name, []).append(row)
                    written.append(row)
                return SimpleNamespace(data=written, count=len(written))
            hits = self._hits()
            if kind == "update":
                for row in hits:
                    row.update(payload or {})
                return SimpleNamespace(data=hits, count=len(hits))
            for row in list(hits):
                self._store[self._name].remove(row)
            return SimpleNamespace(data=hits, count=len(hits))

        hits = self._hits()
        if self._one:
            return SimpleNamespace(data=hits[0] if hits else None, count=len(hits))
        return SimpleNamespace(data=hits, count=len(hits))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


class _Rpc:
    """``rpc()`` — answered with nothing, which is what an empty fixture means.

    Every caller in this app treats a rowless answer as "no rows", and a fake that
    raised here would turn a page this fixture cannot feed into a 500, which reads
    as a finding in the gate rather than as the gap it is.
    """

    def __init__(self, name: str, params=None):
        self.name, self.params = name, params

    def execute(self):
        return SimpleNamespace(data=[], count=0)


class FixtureDb:
    """The stand-in service client: ``table()``, ``rpc()`` and a record of both."""

    def __init__(self, tables: dict | None = None):
        self.tables: dict[str, list[dict]] = {name: [dict(row) for row in rows]
                                              for name, rows in (tables or {}).items()}
        #: Every table this fixture was asked for, in order — the reading a test
        #: uses to prove the pages under measurement really were served from here.
        self.asked: list[str] = []

    def table(self, name: str):
        self.asked.append(name)
        return _Query(self.tables, name)

    def rpc(self, name: str, params=None):
        self.asked.append(f"rpc:{name}")
        return _Rpc(name, params)

    def rows(self, name: str) -> list[dict]:
        return self.tables.get(name, [])

    # The service client carries an `auth` interface too, and the admin helpers in
    # `app/utils/auth.py` reach for it. Nothing a page renders calls it; answering
    # with a rowless shape keeps a stray call from becoming a 500.
    @property
    def auth(self):
        return _FakeAdminAuth()


class _AuthFailure(Exception):
    """What the fake GoTrue raises for a credential it does not hold."""


class _FakeAdminAuth:
    def get_user_by_id(self, user_id):
        raise _AuthFailure(f"no such user: {user_id}")

    def list_users(self):
        return SimpleNamespace(users=[])

    def sign_out(self, *_a, **_k):
        return None


class FixtureAuth:
    """The stand-in GoTrue client, for the accounts this fixture seeds.

    Sign-in is *not* bypassed: the app's own ``/auth/login-user`` POST calls
    ``sign_in_with_password`` and then reads the profile through the service client,
    exactly as it does on the box. What is replaced is the network call and the
    user directory, and only for the accounts seeded here — an unknown address or a
    wrong password raises, so a gate cannot quietly measure a signed-out page.
    """

    def __init__(self, accounts: dict[str, dict]):
        #: email → {id, password, metadata}
        self.accounts = accounts
        #: access token → email, so `get_user` answers for the tokens it minted
        self.tokens: dict[str, str] = {}

    @property
    def auth(self):
        """The GoTrue namespace every caller reaches through (`client.auth.x`).

        `sign_in_with_password`, `get_user` and the rest live here rather than on the
        client itself because that is the shape supabase-py presents — and the app
        spells it the same way in every call site, so a stand-in that put them one
        level up would answer `AttributeError` where the real client answers.
        """
        return self

    def _user(self, email: str) -> SimpleNamespace:
        account = self.accounts[email]
        return SimpleNamespace(
            id=account["id"], email=email,
            user_metadata=dict(account.get("metadata") or {}),
            app_metadata={"provider": "email"},
            created_at=_now(),
        )

    # ── the interface the app uses ──
    def sign_in_with_password(self, credentials):
        email = str((credentials or {}).get("email") or "").strip().lower()
        password = str((credentials or {}).get("password") or "")
        account = self.accounts.get(email)
        if not account or account["password"] != password:
            raise _AuthFailure("Invalid login credentials")
        token = _token(account["id"], email)
        self.tokens[token] = email
        return SimpleNamespace(
            user=self._user(email),
            session=SimpleNamespace(access_token=token,
                                    refresh_token=f"refresh-{account['id']}",
                                    expires_at=JWT_EXP),
        )

    def get_user(self, token):
        email = self.tokens.get(token)
        if not email:
            raise _AuthFailure("invalid JWT")
        return SimpleNamespace(user=self._user(email))

    def refresh_session(self, refresh_token):
        raise _AuthFailure("no session to refresh")

    def sign_out(self, *_a, **_k):
        return None

    def set_session(self, *_a, **_k):
        return None

    def update_user(self, *_a, **_k):
        return None


# ── the seed ─────────────────────────────────────────────────────────────────


def demo_fixture():
    """``deploy/demo_exam_fixture.py``, loaded by path.

    Loaded rather than imported because ``deploy/`` is not a package, and loaded
    rather than restated because *this* paper has to be the one the render gate
    looks for: the gate reads the spec to decide which question to measure, so a
    fixture seeded from anything else would be a different paper than the one it
    judges.
    """
    path = ROOT / "deploy" / "demo_exam_fixture.py"
    spec = importlib.util.spec_from_file_location("sg_fixture_paper", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _exam_row(exam_id: str, spec, *, gate: bool, seb_key: str | None = None) -> dict:
    """One paper, from the fixture spec.

    ``gate`` arms the SEB door on the second copy. Two papers rather than one
    because the two halves of the browser measurement contradict each other on a
    single row: the render gate has to be able to *open* the paper, and the
    handshake half needs a paper that sends it away.
    """
    return {
        "id": exam_id,
        "title": spec.TITLE if not gate else f"{spec.TITLE} (SEB)",
        "subject": spec.SUBJECT,
        "subject_id": SUBJECT_ID,
        "school_id": SCHOOL_ID,
        "class_ids": [CLASS_ID],
        "teacher_id": TEACHER_ID,
        "is_published": True,
        "status": "active",
        "publish_mode": spec.PUBLISH_MODE,
        "duration_minutes": spec.DURATION_MINUTES,
        "total_questions": spec.TOTAL_QUESTIONS,
        "passing_score": spec.PASSING_SCORE,
        "max_attempts": spec.MAX_ATTEMPTS,
        "question_types": dict(spec.QUESTION_TYPES),
        "answer_key": dict(spec.ANSWER_KEY),
        "question_weights": dict(spec.QUESTION_WEIGHTS),
        "description": "fixture paper",
        "pdf_page_urls": [],
        "is_template": False,
        "require_seb": bool(gate),
        "seb_config_key": seb_key,
        "created_at": _now(),
        "updated_at": _now(),
        **spec.WINDOW,
        **spec.ANTI_CHEAT,
    }


def seb_config_key_for(base_url: str, exam_id: str) -> str | None:
    """The Config Key an exam's own ``.seb`` file would carry, or `None`.

    The door compares a header against the key stored on the row, and the key is
    derived from the **paper's** URL — which is a fact about the port this fixture
    happens to be on, so it can only be computed once the server knows its address.
    `None` is the honest answer on a release that does not ship the SEB service
    (`app/services/seb_service.py` arrived with the door): a gated paper still
    refuses a plain browser, which is what the handshake half measures, and the
    honest-claim path is not something this fixture can seed there.
    """
    try:
        from app.services import seb_service
        from app.services import seb_config_key as ck
        from app.services import seb_crypto
    except Exception:
        return None
    start = f"{base_url}/student/exams/{exam_id}"
    try:
        settings = seb_service.settings_for(
            None, start_url=start,
            quit_hash=seb_crypto.sha256_hex("fixture-quit"),
            admin_hash=seb_crypto.sha256_hex("fixture-admin"))
        return ck.config_key(settings)
    except Exception:
        return None


def seed(base_url: str = "http://127.0.0.1:0") -> dict:
    """The tables this fixture serves.

    Deliberately small: one school, one class, one subject, one teacher assigned to
    it, one pupil in that class, and the demo fixture's paper twice (ungated and
    gated). Every id is derived from what it is, so a finding that reads one can be
    traced back to this function.
    """
    spec = demo_fixture()
    tables: dict[str, list[dict]] = {
        "schools": [{
            "id": SCHOOL_ID, "name": "SMP Fixture ScanGrade", "npsn": "10000001",
            "address": "Jalan Fixture 1", "city": "Bandung", "province": "Jawa Barat",
            "phone": "0200000000", "email": "sekolah@fixture.scangrade.test",
            "subscription_tier": "premium", "is_active": True,
            "timezone_offset": 7, "logo_url": None,
            "feature_flags": {}, "created_at": _now(),
        }],
        "profiles": [
            {"id": ADMIN_ID, "full_name": "Admin Fixture", "role": "admin_sekolah",
             "email": "admin@fixture.scangrade.test", "phone": "081200000001",
             "school_id": SCHOOL_ID, "class_id": None, "status": "active",
             "preferences": {}, "created_at": _now(), "updated_at": _now()},
            {"id": TEACHER_ID, "full_name": "Guru Fixture", "role": "guru",
             "email": TEACHER_EMAIL, "phone": "081200000002",
             "school_id": SCHOOL_ID, "class_id": None, "status": "active",
             "preferences": {}, "created_at": _now(), "updated_at": _now()},
            {"id": PUPIL_ID, "full_name": "Murid Fixture", "role": "murid",
             "email": PUPIL_EMAIL, "phone": "081200000003", "nisn": "0000000001",
             "nis": "1001", "school_id": SCHOOL_ID, "class_id": CLASS_ID,
             "status": "active", "preferences": {}, "created_at": _now(),
             "updated_at": _now()},
        ],
        "classes": [{"id": CLASS_ID, "name": "7A", "grade_level": "7",
                     "school_id": SCHOOL_ID,
                     "academic_year": "2026/2027", "created_at": _now()}],
        "subjects": [{"id": SUBJECT_ID, "name": spec.SUBJECT, "grade_level": "7",
                      "school_id": SCHOOL_ID, "created_at": _now()}],
        "teacher_assignments": [{
            "id": "aaaaaaaa-0000-4000-8000-000000000009",
            "teacher_id": TEACHER_ID, "school_id": SCHOOL_ID,
            "subject_id": SUBJECT_ID, "class_id": CLASS_ID, "status": "active",
            "subjects": {"id": SUBJECT_ID, "name": spec.SUBJECT, "grade_level": "7"},
            "classes": {"id": CLASS_ID, "name": "7A", "grade_level": "7"},
            "created_at": _now(),
        }],
        "exams": [
            _exam_row(EXAM_ID, spec, gate=False),
            _exam_row(GATED_EXAM_ID, spec, gate=True,
                      seb_key=seb_config_key_for(base_url, GATED_EXAM_ID)),
        ],
        "submissions": [],
        "school_settings": [{"id": 1, "school_id": SCHOOL_ID,
                             "school_name": "SMP Fixture ScanGrade",
                             "created_at": _now()}],
    }
    return tables


def accounts() -> dict[str, dict]:
    """The two accounts the fake GoTrue answers for."""
    return {
        TEACHER_EMAIL: {"id": TEACHER_ID, "password": PASSWORD,
                        "metadata": {"role": "guru", "school_id": SCHOOL_ID}},
        PUPIL_EMAIL: {"id": PUPIL_ID, "password": PASSWORD,
                      "metadata": {"role": "murid", "school_id": SCHOOL_ID,
                                   "class_id": CLASS_ID}},
    }


# ── the server ───────────────────────────────────────────────────────────────


class FixtureServer:
    """A running fixture: the address to dial, the clients it serves from, and how
    to stop it.

    `base_url` is what the gates are pointed at, and it is the address a *browser*
    must be able to reach too — so the server binds loopback and reports the URL it
    actually bound to, rather than a name that might not resolve on a CI runner.
    """

    def __init__(self, httpd, thread, db: FixtureDb, auth: FixtureAuth, base_url: str):
        self._httpd, self._thread = httpd, thread
        self.db, self.auth, self.base_url = db, auth, base_url

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=10)
        self._httpd.server_close()

    def path(self, path: str) -> str:
        return f"{self.base_url}{path}"


def install(app, db: FixtureDb, auth: FixtureAuth) -> None:
    """Replace exactly the two clients, on an app this fixture did not build.

    Two names and nothing else — `app.extensions["supabase"]` and
    `["supabase_auth"]` — which is the whole of what is faked here, and the reason a
    gate measured against this server is measuring the release's own pages. Named as
    its own function so a guard can call it and *check the two objects*, rather than
    reading this file's source and hoping: a wiring that quietly became `= None` (an
    app with no auth, signing nobody in) reads exactly like the real one in a grep.
    """
    app.extensions["supabase"] = db
    app.extensions["supabase_auth"] = auth
    # The schedulers are off in this config; the quiet part matters too — a page that
    # logs a Supabase failure per request would bury a gate's own lines.
    app.config["PROPAGATE_EXCEPTIONS"] = False


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(port: int | None = None, app=None) -> FixtureServer:
    """Build the app, install the two stand-in clients, and serve it.

    The config is the suite's own `TestingConfig`, which is what keeps this offline:
    the schedulers are off, the rate limiter is in memory, no Redis is dialled and
    no email leaves the process. Nothing about the *pages* is test-specific — that
    is the point.
    """
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("START_BACKGROUND_SCHEDULERS", "false")

    if port is None:
        port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    if app is None:
        from app import create_app

        app = create_app("app.config.TestingConfig")

    db, auth = FixtureDb(seed(base_url)), FixtureAuth(accounts())
    install(app, db, auth)

    from werkzeug.serving import make_server

    httpd = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, name="sg-fixture", daemon=True)
    thread.start()

    server = FixtureServer(httpd, thread, db, auth, base_url)
    # A gate dials this address the moment `serve()` returns; waiting for the first
    # answer here is what keeps "could not measure" from meaning "was not up yet".
    _wait_until_answering(server, app)
    return server


def _wait_until_answering(server: FixtureServer, app, budget: float = 20.0) -> None:
    """Poll the sign-in page until the server answers, or say why it did not."""
    import requests

    deadline = time.time() + budget
    last = ""
    while time.time() < deadline:
        try:
            answer = requests.get(server.path("/auth/login-user"), timeout=5)
            if answer.status_code == 200:
                return
            last = f"HTTP {answer.status_code}"
        except Exception as exc:  # noqa: BLE001 - the server is still coming up
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    server.stop()
    raise RuntimeError(f"the fixture server did not answer on {server.base_url} ({last})")


def credentials(role: str) -> str:
    """``email:password`` for the role a gate signs in as — the same string shape
    the deploy passes its gates, so no gate needs a second sign-in path."""
    email = TEACHER_EMAIL if role in ("guru", "teacher") else PUPIL_EMAIL
    return f"{email}:{PASSWORD}"
