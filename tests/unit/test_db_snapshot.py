"""Guards for the snapshot the deploy takes before a release that ships SQL.

Code can be rolled back from git; data cannot. ``deploy/db_snapshot.py`` is the
only thing that makes a migration-bearing release recoverable, so the properties
below are load-bearing rather than cosmetic:

* **Restore order must actually be a dependency order.** The first version of
  this gave up the moment it met a cycle and appended everything left in
  alphabetical order, which put ``profiles`` after thirteen tables that reference
  it. That restore fails on its first foreign key, and it fails *late* — after
  most of the database has already been written.
* **A cycle has to be deferred, not dropped.** ``profiles.class_id`` points at
  ``classes`` and ``classes.teacher_id`` points back, so no ordering satisfies
  both. Those columns go in as NULL and are re-linked afterwards.
* **Identity keys cannot be inserted, only reverted.** ``notifications`` and
  ``notification_recipients`` hold message data and their primary keys are
  ``GENERATED ALWAYS AS IDENTITY``. Nothing in the PostgREST spec distinguishes
  them from an ordinary integer key, so the refusal has to be read out of the API
  error — quoted, escaped, and inside JSON.
* **One refusal must not abandon the rest.** A restore that stops at the first
  table leaves the database half-written and says nothing useful.
* **It must never claim success while rows are missing.**
* **The deploy must not ship a migration without a recovery point.**
"""
import importlib.util
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
MIGRATIONS = ROOT / "supabase" / "migrations"
DEPLOY_SH = DEPLOY / "scangrade-deploy.sh"
INSTALL_SH = DEPLOY / "install-auto-deploy.sh"


def _load():
    spec = importlib.util.spec_from_file_location("db_snapshot", DEPLOY / "db_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["db_snapshot"] = module
    spec.loader.exec_module(module)
    return module


snap = _load()


def _graph(*edges):
    """edges are (table, column, referenced_table)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for table, column, ref in edges:
        out.setdefault(table, []).append((column, ref))
    return out


def _write_archive(tmp_path: Path, tables: dict[str, list[dict]], order=None,
                   deferred=None) -> Path:
    archive = tmp_path / "scangrade-db-test-label.tar.gz"
    manifest = {
        "created_at": "2026-09-12T00:00:00+00:00",
        "label": "test",
        "kind": "postgrest-logical-snapshot",
        "restore_order": order or sorted(tables),
        "deferred_columns": deferred or {},
        "tables": {name: {"rows": len(rows), "sha256": "", "order_by": "id"}
                   for name, rows in tables.items()},
        "total_rows": sum(len(rows) for rows in tables.values()),
        "read_only_skipped": [],
    }
    with tarfile.open(archive, "w:gz") as tar:
        for name, rows in tables.items():
            path = tmp_path / f"{name}.ndjson"
            path.write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            tar.add(path, arcname=f"tables/{name}.ndjson")
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        tar.add(manifest_path, arcname="manifest.json")
    return archive


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


class BrokenJsonResponse(FakeResponse):
    def json(self):
        raise ValueError("not json")


# ── restore order ────────────────────────────────────────────────────────────

# The shape of the real schema, minus the parts that do not matter here: a cycle
# (classes <-> profiles), and tables that depend on it from outside.
REAL_SHAPE = _graph(
    ("profiles", "class_id", "classes"),
    ("profiles", "school_id", "schools"),
    ("classes", "teacher_id", "profiles"),
    ("classes", "school_id", "schools"),
    ("exams", "class_id", "classes"),
    ("exams", "teacher_id", "profiles"),
    ("exams", "school_id", "schools"),
    ("submissions", "exam_id", "exams"),
    ("submissions", "student_id", "profiles"),
)
REAL_TABLES = ["schools", "classes", "profiles", "exams", "submissions"]


def test_tables_come_after_the_tables_they_reference():
    order, deferred = snap.dependency_plan(REAL_TABLES, REAL_SHAPE)
    position = {table: i for i, table in enumerate(order)}

    assert position["schools"] < position["classes"]
    assert position["schools"] < position["profiles"]
    assert position["exams"] > position["classes"]
    assert position["submissions"] > position["exams"]
    assert deferred == {"classes": ["teacher_id"],
                        "profiles": ["class_id"]}


def test_a_cycle_does_not_scramble_everything_after_it():
    """The regression: a cycle used to dump the whole remainder alphabetically.

    Alphabetically, ``exams`` precedes ``profiles`` — so the old code put a table
    before the table it references, purely because it gave up at the cycle.
    """
    order, _ = snap.dependency_plan(REAL_TABLES, REAL_SHAPE)
    position = {table: i for i, table in enumerate(order)}

    assert position["exams"] > position["profiles"], (
        "exams was emitted before profiles — the dependency order collapsed at "
        "the classes/profiles cycle, exactly as it did before"
    )
    assert position["submissions"] > position["profiles"]


def test_every_table_sits_after_its_parents_except_the_deferred_edges():
    order, deferred = snap.dependency_plan(REAL_TABLES, REAL_SHAPE)
    position = {table: i for i, table in enumerate(order)}
    deferred_pairs = {(t, c) for t, cols in deferred.items() for c in cols}

    violations = [
        (table, column, ref)
        for table in order
        for column, ref in REAL_SHAPE.get(table, [])
        if ref in position and ref != table and position[ref] > position[table]
        and (table, column) not in deferred_pairs
    ]
    assert violations == [], f"restore order violates {violations}"


def test_a_self_reference_is_deferred_rather_than_reordered():
    graph = _graph(("comments", "parent_id", "comments"),
                   ("comments", "post_id", "posts"))
    order, deferred = snap.dependency_plan(["posts", "comments"], graph)

    assert order == ["posts", "comments"]
    assert deferred == {"comments": ["parent_id"]}


def test_plan_is_total_and_terminates_on_a_long_cycle():
    # A 4-table ring: nothing is ever "ready", so a queue that only ever takes
    # ready nodes spins forever. The plan must still return every table once.
    graph = _graph(("a", "x", "b"), ("b", "x", "c"), ("c", "x", "d"), ("d", "x", "a"))
    order, deferred = snap.dependency_plan(["a", "b", "c", "d"], graph)

    assert sorted(order) == ["a", "b", "c", "d"]
    assert set(deferred) == {"a", "b", "c", "d"}


# ── reading the API's refusal ────────────────────────────────────────────────

def test_identity_column_is_found_in_an_escaped_json_body():
    """PostgREST escapes the quotes, which is how a regex over the raw body misses it."""
    body = ('{"code":"428C9","details":"Column \\"id\\" is an identity column '
            'defined as GENERATED ALWAYS.","message":"cannot insert a non-DEFAULT '
            'value into column \\"id\\""}')
    response = FakeResponse(payload={
        "code": "428C9",
        "details": 'Column "id" is an identity column defined as GENERATED ALWAYS.',
        "message": 'cannot insert a non-DEFAULT value into column "id"',
    }, status=400, text=body)

    assert snap.identity_column(response) == "id"


def test_identity_column_is_found_without_json():
    response = BrokenJsonResponse(
        status=400,
        text='{"message":"cannot insert a non-DEFAULT value into column \\"seq\\""}'
             ' -- Column \\"seq\\" is an identity column defined as GENERATED ALWAYS.')
    assert snap.identity_column(response) == "seq"


def test_ordinary_error_is_not_mistaken_for_an_identity_column():
    response = FakeResponse(payload={"code": "42703",
                                     "message": 'record "new" has no field "updated_at"'},
                            status=400)
    assert snap.identity_column(response) is None


# ── archive reading ──────────────────────────────────────────────────────────

def test_every_row_is_read_exactly_once(tmp_path):
    """Counting `first` twice made every table read as one row larger than it was."""
    rows = [{"id": i} for i in range(5)]
    archive = _write_archive(tmp_path, {"thing": rows})

    assert sum(1 for _ in snap.read_archive(archive, "tables/thing.ndjson")) == 5


def test_dry_run_counts_agree_with_the_archive(tmp_path, capsys, monkeypatch):
    tables = {"a": [{"id": 1}, {"id": 2}], "b": [{"id": 1}], "empty": []}
    archive = _write_archive(tmp_path, tables)
    monkeypatch.setattr(snap, "fetch_spec", lambda base, headers: {})

    assert snap.restore(archive, "http://x", "k", dry_run=True, pre_snapshot=False) == 0

    said = re.findall(r"would restore\s+(\d+)", capsys.readouterr().out)
    assert sum(int(n) for n in said) == 3, "the dry run disagrees with the archive"


# ── rotation ─────────────────────────────────────────────────────────────────

def test_prune_keeps_the_newest_and_spares_the_one_just_written(tmp_path):
    names = []
    for i in range(4):
        archive = tmp_path / f"scangrade-db-2026090{i}T000000Z-label.tar.gz"
        archive.write_bytes(b"x")
        names.append(archive.name)

    kept_newest = snap.prune(tmp_path, 2, keep_name=names[-1], quiet=True)

    surviving = sorted(p.name for p in tmp_path.iterdir())
    assert len(kept_newest) == 2
    assert surviving == sorted(names[-2:]), "rotation kept or dropped the wrong archive"


# ── restore behaviour under refusal ──────────────────────────────────────────

def _offline(monkeypatch, spec=None):
    """No spec fetch, so the tests never touch the network.

    ``spec=None`` stands in for the fetch having failed, which is the path where
    the primary keys are unknown.
    """
    monkeypatch.setattr(snap, "fetch_spec", lambda base, headers: spec or {})


def test_a_refused_table_does_not_abandon_the_rest(tmp_path, capsys, monkeypatch):
    tables = {"first": [{"id": 1}], "broken": [{"id": 2}], "last": [{"id": 3}]}
    archive = _write_archive(tmp_path, tables)
    _offline(monkeypatch)

    written = []

    def fake_write_table(base, headers, table, archive_path, clear, omit=None):
        if table == "broken":
            raise RuntimeError("400 42703 record new has no field updated_at")
        written.append(table)
        return 1

    monkeypatch.setattr(snap, "write_table", fake_write_table)

    code = snap.restore(archive, "http://x", "k", pre_snapshot=False)
    output = capsys.readouterr().out

    assert code == 3, "an incomplete restore must not report success"
    assert written == ["first", "last"], "the restore stopped at the failure"
    assert "INCOMPLETE" in output
    assert "broken" in output


def test_identity_keyed_table_is_reverted_in_place(tmp_path, capsys, monkeypatch):
    tables = {"notifications": [{"id": 7, "message": "hi"}]}
    archive = _write_archive(tmp_path, tables)
    _offline(monkeypatch, spec={"definitions": {"notifications": {}}})

    monkeypatch.setattr(
        snap, "write_table",
        lambda *a, **k: (_ for _ in ()).throw(
            snap.IdentityColumnRefused("id", "notifications")),
    )
    monkeypatch.setattr(snap, "primary_key", lambda spec, table: {"id"})
    monkeypatch.setattr(snap, "patch_rows", lambda *a, **k: (1, 0))

    code = snap.restore(archive, "http://x", "k", pre_snapshot=False)
    output = capsys.readouterr().out

    assert code == 0
    assert "reverted      1 in place" in output


def test_rows_that_no_longer_exist_are_reported_as_lost(tmp_path, capsys, monkeypatch):
    tables = {"notifications": [{"id": 7, "message": "hi"}]}
    archive = _write_archive(tmp_path, tables)
    _offline(monkeypatch, spec={"definitions": {"notifications": {}}})

    monkeypatch.setattr(
        snap, "write_table",
        lambda *a, **k: (_ for _ in ()).throw(
            snap.IdentityColumnRefused("id", "notifications")),
    )
    monkeypatch.setattr(snap, "primary_key", lambda spec, table: {"id"})
    monkeypatch.setattr(snap, "patch_rows", lambda *a, **k: (0, 3))

    code = snap.restore(archive, "http://x", "k", pre_snapshot=False)
    output = capsys.readouterr().out

    assert code == 3
    assert "3 row(s) gone" in output


def test_a_column_the_api_will_not_set_is_dropped_rather_than_fatal(
        tmp_path, capsys, monkeypatch):
    tables = {"thing": [{"id": 1, "seq": 4}]}
    archive = _write_archive(tmp_path, tables)
    _offline(monkeypatch)

    attempts = []

    def fake_write_table(base, headers, table, archive_path, clear, omit=None):
        attempts.append(set(omit or ()))
        if not omit:
            raise snap.IdentityColumnRefused("seq", table)
        return 1

    monkeypatch.setattr(snap, "write_table", fake_write_table)
    monkeypatch.setattr(snap, "primary_key", lambda spec, table: {"id"})

    assert snap.restore(archive, "http://x", "k", pre_snapshot=False) == 0
    assert attempts == [set(), {"seq"}], "the retry did not drop the column"


def test_patch_rows_counts_what_it_could_not_reach(tmp_path, monkeypatch):
    """A restore must address rows by key, never invent a second copy of them."""
    rows = [{"id": i, "message": f"m{i}"} for i in range(4)]
    archive = _write_archive(tmp_path, {"notifications": rows})

    calls = []

    def fake_patch(url, headers=None, params=None, data=None, timeout=None):
        calls.append(params["id"])
        return FakeResponse(payload=[{"id": 1}] if params["id"] in ("eq.0", "eq.2") else [])

    monkeypatch.setattr(snap.requests, "patch", fake_patch)

    reverted, missing = snap.patch_rows("http://x", {}, "notifications", archive, "id")

    assert reverted == 2 and missing == 2
    assert calls == ["eq.0", "eq.1", "eq.2", "eq.3"], "not addressed one row at a time"


# ── the deploy takes the snapshot, before it moves anything ──────────────────

def _deploy_script():
    return DEPLOY_SH.read_text(encoding="utf-8")


def test_the_snapshot_is_taken_before_the_merge():
    script = _deploy_script()

    snapshot_at = script.index("db_snapshot.py")
    merge_at = script.index("merge --ff-only")

    assert snapshot_at < merge_at, (
        "the snapshot runs after the checkout moves — a failure then leaves the "
        "release half-applied instead of simply not deployed"
    )


def test_a_migration_release_needs_a_snapshot():
    script = _deploy_script()

    assert re.search(r"grep -qE '\^supabase/migrations", script), (
        "the deploy no longer recognises a migration release, so it would ship a "
        "schema change with no recovery point"
    )
    assert re.search(r"SNAPSHOT FAILED[\s\S]{0,200}?exit 12", script), (
        "a failed snapshot no longer stops the deploy"
    )


def test_the_snapshot_is_taken_as_root_and_kept_root_only():
    script = _deploy_script()

    snapshot_block = script[script.index("Recovery point"):script.index("merge --ff-only")]

    assert '"$REPO/.venv/bin/python" "$SNAPSHOT_CMD"' in snapshot_block, (
        "the snapshot is not invoked directly")
    assert 'as_owner "$REPO/.venv/bin/python" "$SNAPSHOT_CMD"' not in script, (
        "the snapshot runs as the checkout owner, so the archive is not root-only "
        "— a backup of personal data readable by anyone is a breach of its own"
    )
    assert "chmod 0700" in snapshot_block


def test_the_rollback_path_names_the_recovery_point():
    script = _deploy_script()

    assert "--restore" in script, "the rollback path does not say how to put data back"
    assert "SNAPSHOT" in script


def test_installer_installs_the_manual_snapshot_command():
    installer = INSTALL_SH.read_text(encoding="utf-8")

    assert 'install -m 0755 -o root -g root "$REPO/deploy/scangrade-db-snapshot.sh"' \
        in installer, "the installer does not install the manual snapshot command"
    assert 'cat > "$SNAPSHOT_BIN" <<EOF' not in installer, (
        "the installer generates its own copy of the wrapper again. That is two "
        "versions of one script, and the installed one drifts silently — which is "
        "exactly what happened: /usr/local/bin kept a wrapper the repo had moved on "
        "from, and it was only noticed because the backup directory was missing"
    )


def test_the_snapshot_command_refuses_to_run_as_anyone_but_root():
    script = (DEPLOY / "scangrade-db-snapshot.sh").read_text(encoding="utf-8")

    # Migrations are pasted into the SQL editor by hand, which changes no file the
    # deploy can see — this command is the only cover for that path, and it writes
    # personal data and can overwrite live data.
    assert 'if [ "$(id -u)" -ne 0 ]' in script, "no root check"
    assert "exit 2" in script
    assert "db_snapshot.py" in script, "the wrapper does not call the tool"
    # The check has to be unconditional. Gating it on `--restore` would leave a
    # plain snapshot writable by any account, into a directory of personal data.
    # Only the condition line is inspected: the message below it mentions
    # --restore, and matching on that would pass a check that had been narrowed.
    condition = script[script.index('if [ "$(id -u)" -ne 0 ]'):].splitlines()[0]
    assert "--restore" not in condition, "the root check only covers --restore"
    assert "--list" in script, "no way to see what is already there"


def test_the_snapshot_command_works_from_wherever_the_checkout_is():
    script = (DEPLOY / "scangrade-db-snapshot.sh").read_text(encoding="utf-8")

    assert "BASH_SOURCE" in script, (
        "it does not locate itself, so it only works from one hard-coded path"
    )
    assert "/opt/scangrade" not in script, (
        "the path is baked in, so the helper cannot be run from any other checkout"
    )
    assert 'exec "$PYTHON" "$TOOL"' in script, "it reimplements the tool instead of calling it"


# ── the schema defect the restore surfaced ───────────────────────────────────

_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:public\.)?([a-z_][a-z0-9_]*)([\s\S]*?);", re.I)
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-z_][a-z0-9_]*)\s*\((.*?)\n\)\s*;",
    re.S | re.I)


def _tables_missing_updated_at(paths):
    """Tables carrying an update_updated_at trigger but never given the column."""
    triggers: dict[str, str] = {}
    columns: dict[str, set[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"EXECUTE\s+FUNCTION\s+update_updated_at\s*\(\s*\)", text):
            head = text[max(0, match.start() - 400):match.start()]
            found = re.findall(
                r"ON\s+(?:public\.)?([a-z_][a-z0-9_]*)\s*(?:FOR|WHEN|EXECUTE)", head)
            if found:
                triggers[found[-1]] = path.name
        for match in _CREATE_TABLE.finditer(text):
            columns.setdefault(match.group(1), set()).update(
                c for c, _ in re.findall(
                    r"^\s*([a-z_][a-z0-9_]*)\s+([A-Za-z]+.*?)(?:,|$)",
                    match.group(2), re.M))
        for match in _ADD_COLUMN.finditer(text):
            columns.setdefault(match.group(1), set()).update(
                re.findall(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
                           match.group(2), re.I))
    return {t: where for t, where in triggers.items()
            if "updated_at" not in columns.get(t, set())}


def test_no_table_gets_an_updated_at_trigger_without_the_column():
    """The bug the first real restore hit: migration 010 wired the trigger but
    ``teacher_assignments`` was created without ``updated_at``, so *every* UPDATE
    on it failed with 42703 — reversible in the table editor, an upsert that
    conflicts on id, and this restore's write-back all refused."""
    migrations = sorted(MIGRATIONS.glob("*.sql"))

    assert _tables_missing_updated_at(migrations) == {}


def test_that_check_would_have_caught_it():
    """A guard that cannot fail is not a guard: without migration 025 the same
    analysis has to flag teacher_assignments."""
    without_fix = [p for p in sorted(MIGRATIONS.glob("*.sql"))
                   if "025" not in p.name]

    assert "teacher_assignments" in _tables_missing_updated_at(without_fix)


def test_the_migration_that_fixes_it_exists_and_is_idempotent():
    fix = MIGRATIONS / "025_teacher_assignments_updated_at.sql"

    assert fix.exists(), "migration 025 is missing — the defect is still live"
    text = fix.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS updated_at" in text, (
        "unreachable on a second run, which is how half-applied migrations happen"
    )
    assert "teacher_assignments" in text
