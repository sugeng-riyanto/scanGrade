"""A re-assignment has to collide on the pair, not on the primary key.

Assigning a teacher to a class+subject that already exists used to fail with
``23505 duplicate key``. supabase-py's ``upsert`` sends
``Prefer: resolution=merge-duplicates`` and targets the **primary key** unless
told otherwise, and this payload carries no ``id`` — so there was nothing to
conflict with, the insert was attempted anyway, and it tripped
``UNIQUE(teacher_id, class_id, subject_id)``. The row that should have been
updated was never reached and the admin got an error.

The fix is ``on_conflict="teacher_id,class_id,subject_id"``. These tests pin both
halves: that the request really carries it, and that no future call site forgets.
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from supabase import create_client

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
TABLE = "teacher_assignments"
CONFLICT_TARGET = "teacher_id,class_id,subject_id"


# ── what goes out on the wire ────────────────────────────────────────────────

class _Recorder(BaseHTTPRequestHandler):
    """A stand-in for PostgREST that just remembers what it was asked."""

    seen: list[dict] = []

    def do_POST(self):                                            # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        _Recorder.seen.append({"path": self.path,
                               "prefer": self.headers.get("Prefer") or ""})
        payload = json.dumps([{"id": "11111111-1111-1111-1111-111111111111"}]).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):                                 # noqa: D102
        pass


def _upsert_through_app(monkeypatch):
    """Call the model's function with the client pointed at a local recorder."""
    _Recorder.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import app.models.supabase_queries as queries
        # supabase-py rejects any key that is not shaped like a JWT, so this is a
        # dummy in that shape rather than the usual "test-key".
        client = create_client(f"http://127.0.0.1:{server.server_port}",
                               "dummy.jwt.value")
        monkeypatch.setattr(queries, "get_supabase", lambda: client)
        queries.create_teacher_assignment("t-1", "c-1", "s-1", "sc-1")
    finally:
        server.shutdown()
        server.server_close()
    return _Recorder.seen


def test_the_upsert_names_the_conflict_target(monkeypatch):
    seen = _upsert_through_app(monkeypatch)

    assert seen, "the call made no request at all"
    request = seen[0]
    assert f"/{TABLE}" in request["path"], request["path"]

    query = parse_qs(urlparse(request["path"]).query)
    assert query.get("on_conflict") == [CONFLICT_TARGET], (
        f"the upsert does not collide on {CONFLICT_TARGET} — without it supabase-py "
        "targets the primary key, the payload has no id, and a re-assignment dies "
        f"on the unique constraint instead of updating the row (query was {request['path']})"
    )
    assert "merge-duplicates" in request["prefer"], (
        "no merge-duplicates preference, so a conflict would be rejected rather "
        "than resolved into an update"
    )


# ── no call site may forget it ───────────────────────────────────────────────

TABLE_REF = re.compile(r"table\(\s*[\"']" + TABLE + r"[\"']\s*\)")


def test_no_teacher_assignments_upsert_forgets_the_conflict_target():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for upsert in re.finditer(r"\.upsert\(", text):
            head = text[max(0, upsert.start() - 400):upsert.start()]
            if not TABLE_REF.search(head):
                continue
            statement = text[upsert.start():text.find(".execute()", upsert.start())]
            if "on_conflict=" not in statement:
                line = text.count("\n", 0, upsert.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}")

    assert offenders == [], (
        "these upserts on teacher_assignments omit on_conflict, so a re-assignment "
        f"fails with a duplicate-key error: {offenders}"
    )


def test_the_guard_would_catch_a_regression():
    """A guard that cannot fail is not a guard."""
    original = (ROOT / "app" / "models" / "supabase_queries.py").read_text(encoding="utf-8")
    without = original.replace(', on_conflict="' + CONFLICT_TARGET + '"', "")

    assert without != original, "the fix is not where the test expects to find it"
    upsert = without.index(".upsert(")
    statement = without[upsert:without.find(".execute()", upsert)]
    assert "on_conflict=" not in statement
