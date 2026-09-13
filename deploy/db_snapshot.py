#!/usr/bin/env python3
"""Logical database snapshot, so a rollback can restore data and not only code.

The deploy can put the *code* back on its own; it cannot put the *data* back.
This is what makes a release that ships SQL recoverable.

What it is
----------
A full read of every writable table in the ``public`` schema through the
PostgREST API, written to one root-only archive with a manifest:

    /var/backups/scangrade/scangrade-db-<stamp>-<label>.tar.gz
      manifest.json          counts, per-table sha256, restore order
      tables/<name>.ndjson   one JSON object per line, exactly as stored
      auth_users.ndjson      reference only — see "What it cannot do"

It needs no database password and no Supabase personal access token: the service
key the app already has is enough. That is the whole point — it has to work on
the day it is needed, not after someone finds a credential.

What it cannot do
-----------------
* **It is not point-in-time consistent.** Tables are read one after another, so a
  row written mid-export can land in one table and not another. For this app
  (a few thousand rows, low write rate) that is a fair trade; Supabase's own
  physical backups and PITR are the consistent ones, and they need a personal
  access token from the dashboard.
* **It is not the schema.** It restores rows into tables that already exist.
  A migration that drops a column cannot be undone by this — which is exactly
  why the deploy takes one of these *before* it ships a release that changes
  `supabase/migrations`, while the old schema is still the one being served.
* **It cannot be point-in-time consistent**, so it is not a substitute for
  Supabase's own PITR when the question is "what did row X look like at 14:03".
* **It does not restore passwords.** ``auth_users.ndjson`` carries ids, emails
  and metadata so rows can be matched back up, but GoTrue never exposes hashes:
  restoring a deleted account means creating it again and resetting the password.

It contains personal data, which is why the archive is written root-only
(mode 0600 in a 0700 directory) and rotated. UU PDP treats an unprotected copy as
a breach in its own right; docs/AUTO_DEPLOY.md records the retention.

Restoring a cycle
-----------------
`profiles.class_id` points at `classes`, and `classes.teacher_id` points back at
`profiles`. Postgres checks a foreign key as each statement runs, so there is no
insertion order that satisfies both. Those back-edges (four of them today, all
nullable) are therefore recorded in the manifest as ``deferred_columns``, written
as NULL on the way in, and patched back in a second pass once every row exists.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PAGE_SIZE = 1000
UPSERT_BATCH = 500
DEFAULT_OUT = "/var/backups/scangrade"
DEFAULT_KEEP = 5
REQUEST_TIMEOUT = 120
ARCHIVE_PREFIX = "scangrade-db-"

# `<fk table='profiles' column='id'/>` — how PostgREST documents a foreign key in
# its OpenAPI spec. It is the only description of the relationship graph
# reachable without a database connection, and restore order depends on it.
FK_RE = re.compile(r"<fk table='([^']+)' column='([^']+)'/>")
ENV_COMMENT_RE = re.compile(r"\s+#")

# `GENERATED ALWAYS AS IDENTITY` columns cannot be written through the API at all
# — Postgres wants OVERRIDING SYSTEM VALUE and PostgREST has no way to send it.
# Nothing in the spec distinguishes such a column from an ordinary integer key,
# so the only reliable way to find out is to try and read the refusal back.
IDENTITY_RE = re.compile(r'Column "([^"]+)" is an identity column')


def identity_column(response) -> str | None:
    """The column name out of a 428C9, in whichever way it was spelled.

    PostgREST escapes the quotes inside its JSON (`Column \"id\" is an identity
    column`), so a regex over the raw body does not match even though the text is
    right there — which is how the first version of this managed to see the
    refusal and still treat it as a generic error.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    for field in ("details", "message"):
        match = IDENTITY_RE.search(str(body.get(field) or ""))
        if match:
            return match.group(1)
    match = IDENTITY_RE.search(response.text.replace('\\"', '"'))
    return match.group(1) if match else None


class IdentityColumnRefused(RuntimeError):
    """The API refused a column because the database generates it itself."""

    def __init__(self, column: str, table: str):
        super().__init__(f"{table}.{column} is GENERATED ALWAYS AS IDENTITY")
        self.column = column
        self.table = table


# ── configuration ────────────────────────────────────────────────────────────

def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env reader, matching app.config's tolerance for real-world files.

    Public because ``apply_migration.py`` needs the same tolerance for the same
    file, and two readers is how two answers to one question start.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        value = ENV_COMMENT_RE.split(raw, 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def load_credentials(repo: Path) -> tuple[str, str]:
    """Supabase URL and service key, from the environment or the app's .env."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"):
        return os.environ["SUPABASE_URL"].strip(), os.environ["SUPABASE_SERVICE_KEY"].strip()

    env = read_env_file(repo / ".env")
    url = env.get("SUPABASE_URL", "")
    # A key shaped like a key: everything from a glued-on '#' is garbage and must
    # go, the same rule app.config.env_key applies. A real key never contains '#'.
    key = env.get("SUPABASE_SERVICE_KEY", "").split("#", 1)[0].strip()
    return url.rstrip("/"), key


def headers_for(key: str) -> dict[str, str]:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


# ── schema discovery ─────────────────────────────────────────────────────────

def fetch_spec(base: str, headers: dict[str, str]) -> dict:
    response = requests.get(f"{base}/rest/v1/", headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def writable_tables(spec: dict) -> list[str]:
    """Tables, not views.

    PostgREST generates a POST path only for something it can insert into, which
    is exactly the distinction restore needs — a view would fail on upsert.
    """
    paths = spec.get("paths", {})
    return sorted(
        name for name in spec.get("definitions", {})
        if "post" in paths.get(f"/{name}", {})
    )


def read_only_objects(spec: dict) -> list[str]:
    writable = set(writable_tables(spec))
    return sorted(name for name in spec.get("definitions", {}) if name not in writable)


def table_columns(spec: dict, table: str) -> list[str]:
    return sorted(spec.get("definitions", {}).get(table, {}).get("properties", {}))


def primary_key(spec: dict, table: str) -> set[str]:
    """Columns the spec marks `<pk/>` — what an upsert has to be addressed by."""
    properties = spec.get("definitions", {}).get(table, {}).get("properties") or {}
    return {
        column for column, prop in properties.items()
        if "<pk/>" in str(prop.get("description", ""))
    }


def order_key(spec: dict, table: str) -> tuple[str, str]:
    """A stable ordering, required for correct Range pagination.

    Without it PostgreSQL may return rows in any order between pages and the
    export would silently skip and duplicate rows — a backup that looks complete
    and is not. `id` is used when present; otherwise the leading columns are
    ordered on, which is unique enough because the row is, and only tables
    without an `id` (none today) ever take that path.
    """
    columns = table_columns(spec, table)
    if "id" in columns:
        return "id", "id"
    if not columns:
        return "", "unordered"
    return ",".join(columns[:8]), "leading 8 columns (no id)"


def foreign_keys(spec: dict) -> dict[str, list[tuple[str, str]]]:
    """{table: [(column, referenced_table)]} parsed from the spec descriptions."""
    graph: dict[str, list[tuple[str, str]]] = {}
    for table, definition in spec.get("definitions", {}).items():
        edges = []
        for column, prop in (definition.get("properties") or {}).items():
            match = FK_RE.search(str(prop.get("description", "")))
            if match:
                edges.append((column, match.group(1)))
        if edges:
            graph[table] = edges
    return graph


def strongly_connected_components(tables: list[str],
                                  graph: dict[str, list[tuple[str, str]]]) -> list[list[str]]:
    """Tarjan, iteratively — a recursive version would be a stack-overflow risk here.

    A component is a cycle: several tables that cannot be inserted in any order
    without one of them referring to a row that does not exist yet.
    """
    known = set(tables)
    neighbours = {
        table: sorted({ref for _, ref in graph.get(table, []) if ref in known})
        for table in tables
    }

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in tables:
        if root in index:
            continue
        # (node, how many of its neighbours have been visited, next neighbour)
        work = [(root, 0, 0)]
        while work:
            node, child_i, depth = work.pop()
            if depth == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)

            descend = False
            while child_i < len(neighbours[node]):
                child = neighbours[node][child_i]
                child_i += 1
                if child not in index:
                    work.append((node, child_i, depth + 1))
                    work.append((child, 0, 0))
                    descend = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if descend:
                continue

            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))

            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    return components


def dependency_plan(tables: list[str],
                    graph: dict[str, list[tuple[str, str]]]) -> tuple[list[str], dict[str, list[str]]]:
    """Insertion order, plus the columns that cannot be filled on the way in.

    Returns ``(order, deferred)``. ``order`` puts every table after the tables it
    references. ``deferred[table]`` lists foreign keys pointing *inside that
    table's own cycle* — `profiles.class_id` while `classes.teacher_id` points
    back at `profiles`. Postgres checks each statement immediately, so no single
    ordering can satisfy those: the rows are inserted with the reference cleared
    and it is restored by a second pass.

    Getting this wrong is not theoretical. An earlier version of this module gave
    up at the first cycle and appended everything left in alphabetical order,
    which put `profiles` (position 29) after 13 tables that reference it — a
    restore that would have failed on the first foreign key.
    """
    if not tables:
        return [], {}

    components = strongly_connected_components(tables, graph)
    component_of = {table: i for i, comp in enumerate(components) for table in comp}

    needs: dict[int, set[int]] = {}
    for i, comp in enumerate(components):
        deps: set[int] = set()
        for table in comp:
            for _, ref in graph.get(table, []):
                if ref in component_of and component_of[ref] != i:
                    deps.add(component_of[ref])
        needs[i] = deps

    sequence: list[int] = []
    remaining = dict(needs)
    while remaining:
        ready = sorted(i for i, deps in remaining.items() if not deps)
        if not ready:                       # impossible in a condensation, but never hang
            ready = [min(remaining)]
        for i in ready:
            sequence.append(i)
            remaining.pop(i)
        for deps in remaining.values():
            deps.difference_update(ready)

    order = [table for i in sequence for table in components[i]]

    deferred: dict[str, list[str]] = {}
    for comp in components:
        members = set(comp)
        for table in comp:
            columns = sorted(col for col, ref in graph.get(table, []) if ref in members)
            if columns:
                deferred[table] = columns
    return order, deferred


# ── snapshot ─────────────────────────────────────────────────────────────────

def export_table(base: str, headers: dict[str, str], table: str, order: str,
                 sink) -> tuple[int, str]:
    """Stream one table to `sink` as NDJSON; return (rows, sha256)."""
    digest = hashlib.sha256()
    rows = 0
    offset = 0

    while True:
        url = f"{base}/rest/v1/{table}?select=*"
        if order:
            url += f"&order={order}"
        response = requests.get(
            url, headers={**headers, "Range": f"{offset}-{offset + PAGE_SIZE - 1}"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            break

        for row in page:
            line = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            sink.write(line)
            digest.update(line)
        rows += len(page)

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return rows, digest.hexdigest()


def export_auth_users(base: str, headers: dict[str, str], sink) -> int:
    """Ids, emails and metadata for reference — GoTrue never exposes hashes."""
    written = 0
    page = 1
    while True:
        response = requests.get(
            f"{base}/auth/v1/admin/users",
            headers=headers, params={"page": page, "per_page": 1000},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            break
        users = response.json().get("users", [])
        if not users:
            break
        for user in users:
            record = {
                "id": user.get("id"),
                "email": user.get("email"),
                "created_at": user.get("created_at"),
                "user_metadata": user.get("user_metadata") or {},
            }
            sink.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            written += 1
        if len(users) < 1000:
            break
        page += 1
    return written


def make_snapshot(base: str, key: str, out_dir: Path, label: str, keep: int,
                  quiet: bool = False) -> Path:
    headers = headers_for(key)
    spec = fetch_spec(base, headers)
    tables = writable_tables(spec)
    skipped = read_only_objects(spec)
    graph = foreign_keys(spec)
    order, deferred = dependency_plan(tables, graph)

    if not tables:
        raise RuntimeError("no writable tables found — is the service key right?")

    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^A-Za-z0-9._-]", "", label) or "manual"
    archive = out_dir / f"{ARCHIVE_PREFIX}{stamp}-{safe_label}.tar.gz"

    manifest: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "supabase_url": base,
        "kind": "postgrest-logical-snapshot",
        "point_in_time_consistent": False,
        "restore_order": order,
        "deferred_columns": deferred,
        "tables": {},
        "read_only_skipped": skipped,
    }
    total = 0
    started = time.time()

    # Written straight into the archive so nothing large ever sits in memory.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive, "w:gz") as tar:
            for table in order:
                order_col, order_note = order_key(spec, table)
                path = tmp_path / f"{table}.ndjson"
                with open(path, "wb") as sink:
                    rows, sha = export_table(base, headers, table, order_col, sink)
                manifest["tables"][table] = {
                    "rows": rows, "sha256": sha, "order_by": order_note,
                }
                tar.add(path, arcname=f"tables/{table}.ndjson")
                total += rows
                if not quiet:
                    print(f"   {table:34} {rows:>7}")

            users_path = tmp_path / "auth_users.ndjson"
            with open(users_path, "wb") as sink:
                users = export_auth_users(base, headers, sink)
            manifest["auth_users"] = {"rows": users, "restorable": False}
            tar.add(users_path, arcname="auth_users.ndjson")

            manifest["total_rows"] = total
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            tar.add(manifest_path, arcname="manifest.json")

    os.chmod(archive, 0o600)

    if not quiet:
        size = archive.stat().st_size / 1024
        print(f"   {'TOTAL':34} {total:>7}  ({size:.0f} KB in {time.time() - started:.1f}s)")

    prune(out_dir, keep, keep_name=archive.name, quiet=quiet)
    return archive


def prune(out_dir: Path, keep: int, keep_name: str = "", quiet: bool = False) -> list[Path]:
    """Keep the newest `keep` archives. A backup that fills the disk is its own outage.

    Sorted by write time *and then by name*: two snapshots taken inside the same
    second — which is what a failed deploy followed by a manual retry looks like —
    have the same mtime, and ordering those by mtime alone is left to the
    filesystem. The name carries the UTC stamp, so it makes the choice repeatable.
    """
    if keep <= 0:
        return []
    archives = sorted(out_dir.glob(f"{ARCHIVE_PREFIX}*.tar.gz"),
                      key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    removed = []
    for stale in archives[keep:]:
        if stale.name == keep_name:
            continue
        try:
            stale.unlink()
            removed.append(stale)
        except OSError:
            pass
    if removed and not quiet:
        print(f"   rotated out {len(removed)} old snapshot(s)")
    return removed


# ── restore ──────────────────────────────────────────────────────────────────

def read_archive(archive: Path, member: str):
    with tarfile.open(archive, "r:gz") as tar:
        handle = tar.extractfile(member)
        if handle is None:
            return
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def describe(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        handle = tar.extractfile("manifest.json")
        if handle is None:
            raise RuntimeError(f"{archive} has no manifest.json")
        return json.loads(handle.read().decode("utf-8"))


def write_table(base: str, headers: dict[str, str], table: str, archive: Path,
                clear: set[str], omit: set[str] | None = None) -> int:
    """POST one table in batches. ``clear`` columns go in as NULL; ``omit`` drops them.

    It re-reads the archive on every call rather than accepting an iterator, so a
    second attempt after a refusal starts from the beginning: batches that got
    through are harmless to send again (they are plain upserts of the same values),
    whereas a half-consumed iterator would quietly skip the batch that failed.
    """
    omit = omit or set()
    written = 0
    for batch in iter_batches(read_archive(archive, f"tables/{table}.ndjson"), UPSERT_BATCH):
        if omit:
            batch = [{k: v for k, v in row.items() if k not in omit} for row in batch]
        if clear:
            batch = [{**row, **{c: None for c in clear}} for row in batch]
        response = requests.post(f"{base}/rest/v1/{table}", headers=headers,
                                 data=json.dumps(batch, default=str),
                                 timeout=REQUEST_TIMEOUT)
        if response.status_code >= 300:
            refused = identity_column(response)
            if refused:
                raise IdentityColumnRefused(refused, table)
            raise RuntimeError(
                f"{table} rows {written}-{written + len(batch)} refused: "
                f"{response.status_code} {response.text[:300]}"
            )
        written += len(batch)
    return written


def iter_batches(rows, size: int):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def patch_rows(base: str, headers: dict[str, str], table: str, archive: Path,
               key: str) -> tuple[int, int]:
    """Re-write existing rows in place, addressed by a key that cannot be inserted.

    A table whose primary key is `GENERATED ALWAYS AS IDENTITY` cannot be inserted
    into with an explicit id, so a row that no longer exists cannot be brought back
    through the API at all. A row that is *still there* can have every other value
    put back — and for the failure this tool exists for (a bad release, not a
    dropped table) that is almost all of them.

    Returns ``(reverted, missing)``.
    """
    patch_headers = {**headers, "Prefer": "return=representation"}
    reverted = missing = 0
    for row in read_archive(archive, f"tables/{table}.ndjson"):
        value = row.get(key)
        if value is None:
            missing += 1
            continue
        body = {k: v for k, v in row.items() if k != key}
        response = requests.patch(
            f"{base}/rest/v1/{table}", headers=patch_headers, params={key: f"eq.{value}"},
            data=json.dumps(body, default=str), timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"{table} could not be reverted: "
                               f"{response.status_code} {response.text[:300]}")
        if response.json():
            reverted += 1
        else:
            missing += 1
    return reverted, missing


def restore(archive: Path, base: str, key: str, dry_run: bool = False,
            pre_snapshot: bool = True) -> int:
    manifest = describe(archive)
    headers = {**headers_for(key), "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    order = manifest.get("restore_order") or sorted(manifest.get("tables", {}))
    deferred: dict[str, list[str]] = manifest.get("deferred_columns") or {}

    print(f"restoring {archive.name}")
    print(f"   snapshot taken {manifest.get('created_at')} "
          f"(label {manifest.get('label')}, {manifest.get('total_rows')} rows)")
    if not manifest.get("restore_order"):
        print("   WARNING: archive has no restore order — tables went in alphabetically")
    if dry_run:
        print("   dry run — nothing will be written")

    # A restore that goes wrong is otherwise unrecoverable: the state being
    # overwritten is the only copy of it. So the current state is captured first,
    # and if that capture fails the restore does not run — an operator who really
    # wants to skip it has to say so with --no-pre-snapshot.
    if pre_snapshot and not dry_run:
        print("   capturing the current state first, so this restore is itself undoable...")
        try:
            safety = make_snapshot(base, key, archive.parent, "pre-restore",
                                   DEFAULT_KEEP, quiet=True)
        except Exception as exc:                                  # noqa: BLE001
            print(f"   REFUSING: could not snapshot the current state ({exc})")
            print("   pass --no-pre-snapshot to restore anyway and lose the ability "
                  "to undo this")
            return 1
        print(f"   current state saved as {safety.name}")

    # What the API will refuse is only discoverable by trying, so the primary keys
    # of the schema being written into are fetched once up front.
    try:
        live_spec = fetch_spec(base, headers)
    except requests.RequestException:
        live_spec: dict = {}

    restored = 0
    failures: dict[str, str] = {}
    in_place: dict[str, tuple[int, int]] = {}
    not_restorable: dict[str, list[str]] = {}
    pending: list[tuple[str, list[str]]] = []      # (table, columns) to re-attach

    for table in order:
        member = f"tables/{table}.ndjson"
        rows = read_archive(archive, member)
        first = next(rows, None)
        if first is None:
            if dry_run:
                print(f"   {table:34} empty in the snapshot")
            continue
        columns = [c for c in deferred.get(table, []) if c in first]
        if columns:
            pending.append((table, columns))

        if dry_run:
            # `first` has been taken off the iterator, so it is added back in —
            # exactly once. It was counted twice at one point, which made every
            # table read as one row larger than the archive really was.
            count = sum(1 for _ in itertools.chain([first], rows))
            note = f"  (holding {', '.join(columns)} for a second pass)" if columns else ""
            print(f"   {table:34} would restore {count:>6}{note}")
            restored += count
            continue

        # A table can refuse more than one generated column, so this retries rather
        # than trying once — and a refusal arriving *during* the retry still lands
        # in this same handler instead of escaping the restore entirely.
        keys = primary_key(live_spec, table) if live_spec else set()
        omitted: set[str] = set()
        handled = False          # accounted for without a plain insert
        for _ in range(4):
            try:
                written = write_table(base, headers, table, archive, set(columns),
                                      omit=omitted or None)
                break
            except IdentityColumnRefused as refusal:
                if refusal.column in keys:
                    # The row's identity IS the key, so it cannot be re-inserted.
                    # Rows that are still there can still be put back as they were.
                    try:
                        reverted, missing = patch_rows(base, headers, table, archive,
                                                       refusal.column)
                    except RuntimeError as exc:
                        failures[table] = str(exc)
                        print(f"   {table:34} FAILED — {str(exc)[:150]}")
                        handled = True
                        break
                    in_place[table] = (reverted, missing)
                    gone = f", {missing} row(s) gone and unrecreatable" if missing else ""
                    print(f"   {table:34} reverted {reverted:>6} in place{gone}")
                    restored += reverted
                    handled = True
                    break
                omitted.add(refusal.column)
                print(f"   {table:34} retrying without {refusal.column} (not settable)")
            except RuntimeError as exc:
                # One table refusing is not a reason to abandon the other 46, and the
                # whole restore is idempotent, so anything reported here can be
                # retried on its own once its cause is dealt with.
                failures[table] = str(exc)
                print(f"   {table:34} FAILED — {str(exc)[:150]}")
                handled = True
                break
        else:
            failures[table] = f"still refused after dropping {sorted(omitted)}"
            print(f"   {table:34} FAILED — {failures[table]}")
            handled = True

        if handled:
            continue
        restored += written
        note = f"   (without {', '.join(sorted(omitted))})" if omitted else ""
        print(f"   {table:34} restored {written:>6}{note}")
        if omitted:
            not_restorable[table] = sorted(omitted)

    print(f"   {'TOTAL':34} restored {restored:>6}")

    if pending and not dry_run:
        # Second pass: the rows are all in place now, so the references that could
        # not be satisfied on the way in can be put back. Rows are grouped by the
        # values they share, so this is a handful of statements rather than one
        # per row.
        print("   re-attaching the references held back for the cycle:")
        for table, columns in pending:
            rows = read_archive(archive, f"tables/{table}.ndjson")
            groups: dict[tuple, list] = {}
            for row in rows:
                values = tuple(row.get(c) for c in columns)
                if all(v is None for v in values):
                    continue                     # never had a value; nothing to restore
                if row.get("id") is None:
                    raise RuntimeError(
                        f"{table} has deferred columns {columns} but no id to address "
                        f"the rows by — restore the table by hand"
                    )
                groups.setdefault(values, []).append(row["id"])

            patched = 0
            try:
                for values, row_ids in sorted(groups.items(), key=lambda kv: str(kv[0])):
                    body = {c: v for c, v in zip(columns, values)}
                    for chunk in iter_batches(row_ids, 100):
                        response = requests.patch(
                            f"{base}/rest/v1/{table}", headers=headers,
                            params={"id": f"in.({','.join(str(i) for i in chunk)})"},
                            data=json.dumps(body, default=str), timeout=REQUEST_TIMEOUT,
                        )
                        if response.status_code >= 300:
                            refused = identity_column(response)
                            if refused:
                                raise IdentityColumnRefused(refused, table)
                            raise RuntimeError(
                                f"{table} could not re-attach {columns}: "
                                f"{response.status_code} {response.text[:300]}"
                            )
                        patched += len(chunk)
            except IdentityColumnRefused as refusal:
                # Every row is in place, but this reference cannot be put back.
                # That is not a reason to abandon the rest of the restore.
                print(f"   {table:34}   cannot re-link {refusal.column}: {refusal}")
                not_restorable.setdefault(table, []).append(refusal.column)
                continue
            except RuntimeError as exc:
                failures[table] = str(exc)
                print(f"   {table:34}   FAILED to re-link {columns}: {str(exc)[:150]}")
                continue
            print(f"   {table:34}   {patched:>4} row(s) re-linked")

    print()
    print("   auth users are NOT restored: GoTrue does not expose password hashes.")
    print("   auth_users.ndjson lists ids/emails if an account has to be recreated.")

    if not_restorable:
        for table, columns in sorted(not_restorable.items()):
            print(f"   {table}: {', '.join(columns)} left at whatever the database "
                  f"assigned — the API cannot write it")

    lost = {t: m for t, (_, m) in in_place.items() if m}
    if not (failures or lost):
        return 0

    # Something did not go back, and saying so is the whole point: a restore that
    # reports success while rows are missing is worse than one that fails.
    print()
    print("   INCOMPLETE — these did not come back:")
    for table, (reverted, missing) in sorted(in_place.items()):
        if missing:
            print(f"     {table:32} {reverted} reverted, {missing} row(s) gone.")
            print(f"     {'':32} `id` is GENERATED ALWAYS there, so the API cannot")
            print(f"     {'':32} accept one and a deleted row cannot be recreated.")
    for table, why in sorted(failures.items()):
        print(f"     {table:32} refused: {why[:120]}")
    print()
    print("   Their rows are in tables/<table>.ndjson inside the archive. Whatever the")
    print("   API will not take can go in from the SQL editor, where OVERRIDING SYSTEM")
    print("   VALUE is available; anything that references a missing row will keep")
    print("   refusing until it is back.")
    return 3


# ── cli ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default="/opt/scangrade",
                        help="checkout holding .env (default: /opt/scangrade)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"snapshot directory (default: {DEFAULT_OUT})")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"snapshots to retain (default: {DEFAULT_KEEP}, 0 = all)")
    parser.add_argument("--label", default="manual",
                        help="what this snapshot belongs to, e.g. the commit")
    parser.add_argument("--restore", metavar="ARCHIVE",
                        help="restore an archive instead of taking one")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --restore: report only")
    parser.add_argument("--no-pre-snapshot", action="store_true",
                        help="with --restore: skip capturing the current state first")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo)
    base, key = load_credentials(repo)
    if not base or not key:
        print("no SUPABASE_URL / SUPABASE_SERVICE_KEY — nothing to do "
              f"(looked in the environment and {repo / '.env'})")
        return 2

    try:
        if args.restore:
            return restore(Path(args.restore), base, key, dry_run=args.dry_run,
                           pre_snapshot=not args.no_pre_snapshot)

        print(f"snapshotting {base} (label {args.label})")
        archive = make_snapshot(base, key, Path(args.out), args.label, args.keep,
                                quiet=args.quiet)
        print(f"   written to {archive} (mode 0600)")
        return 0
    except requests.RequestException as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    except Exception as exc:                                   # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
