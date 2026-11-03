# Proving a backup is restorable - before 073, 074 and 075 reach production

**Status: HOLD.** Nothing on this page authorises applying a migration, merging to
`main`, or asking the VPS for a release. It is the evidence procedure that has to
pass *before* any of those are discussed.

This page exists because `docs/MAINTENANCE.md` asks for a monthly "confirm Supabase
backup ran", and a backup that ran is not a backup that restores. Migrations 073,
074 and 075 change the schema production is serving, and a recovery point nobody
has read back is a file, not a recovery point.

## What "proven" means, and the two tiers

Three facts, each needing an artefact. Any one missing means the archive is not
proven:

| fact | artefact |
| --- | --- |
| the archive is **complete and uncorrupted** | every table's bytes match the digest the manifest recorded (tier 1) |
| it came from the **production** project | the printed host of `SUPABASE_URL` when it was taken |
| its rows can be **written** somewhere and read back | a restore into an isolated project, with counts matching (tier 2) |

An archive that exists proves none of these. Neither does `--dry-run`, which
reports and writes nothing. Neither does a restore that "looked fine" without a
count comparison: a truncated export restores perfectly and is still incomplete.

**Tier 1 is cheap and can be done today. Tier 2 needs a second Supabase project and
is the one that actually exercises the restore path.** Do tier 1 first - it is
minutes, needs no credentials, and catches the failure that makes tier 2 pointless.

## The tools that already exist

Do not hand-roll any of this; each handles a failure mode a shell one-liner does
not.

| tool | what it is for |
| --- | --- |
| `deploy/db_snapshot.py` | takes a logical snapshot and restores one: `--restore ARCHIVE`, `--dry-run`, `--no-pre-snapshot`, `--pin` / `--unpin`, `--out`, `--keep`, `--label`, `--repo` |
| `deploy/scangrade-db-snapshot.sh` | root-only wrapper for the same tool, from the checkout: `--label`, `--list`, `--restore`, `--dry-run` |
| `deploy/apply_migration.py` | trials a migration in a rolled-back transaction; `--commit` takes a recovery point first. Also `--status`, `--verify`, `--reconcile` |
| `deploy/schema_contract.py` | asks the live schema and the API what they carry: `--live`, `--require-applied`, `--anon` |

Facts read out of those files, each of which changes how a step must be run:

* The archive is `manifest.json` plus `tables/<name>.ndjson` plus
  `auth_users.ndjson`, written root-only into `/var/backups/scangrade`. The manifest
  carries `created_at`, `label`, `total_rows`, `restore_order`,
  `deferred_columns`, and per table `rows`, `sha256`, `order_by`.
* A table's `sha256` is the digest **of its `tables/<name>.ndjson` bytes** - the same
  bytes written to the archive - which is what makes tier 1 possible offline.
* **The restore writes into whatever `SUPABASE_URL` names, and now prints that
  host as its first line after the archive's name** - before the pre-restore
  snapshot and before any write. Only the host and port are printed, never a
  `user:password@` a URL might carry. On a checkout older than that change, the
  manual command in step 1 is the only way to see it.
* `--restore` captures the current state first and **refuses to run** if it cannot.
  `--no-pre-snapshot` exists and means giving that up.
* Credentials come from the environment first, then `<repo>/.env`. That is how a
  restore is aimed at a scratch project without moving the production checkout.
* It restores **rows, not schema**: the target must already have the tables. It does
  not restore passwords.
* The archive holds names, phone numbers and exam answers. Anything that writes it
  somewhere else - including an extraction "just to check it" - is creating an
  unprotected copy, which UU PDP treats as a breach in its own right. Tier 1 below
  reads the archive in memory and writes nothing.

## Step 0 - Preconditions

Every line is PASS or FAIL with a timestamp and a name. One FAIL stops here.

* written authorisation for the window, from whoever owns the deployment
* zero active exams, from the operator's own reading
* both ScanMeet switches OFF
* no paper scheduled during the window
* root on the box (the archives are root-only; a restore overwrites data)
* a scratch Supabase project that is **not** production, with its own keys

## Step 1 - Say which database this shell is pointed at

The restore names it itself now, on the line after `restoring <archive>`:

```text
restoring scangrade-db-20261026T093000Z-pre-073-075.tar.gz
   target your-production-ref.supabase.co
```

Copy that `target` line into the record for every restore in step 5. It is printed
before the pre-restore snapshot and before any write, so a restore aimed at the
wrong project is visible on the first line rather than after the fact. Two things it
cannot tell you: whether `SUPABASE_URL` and `DIRECT_URL` agree (step 4 sets both), and
anything at all on a checkout older than the change that added the line - there, run
the fallback by hand:

```bash
. /opt/scangrade/.env
python3 -c 'import os, urllib.parse as u; print(u.urlsplit(os.environ["SUPABASE_URL"]).hostname)'
```

That host must be production's. If it names the scratch project, this shell is aimed
at the wrong place and every command below does the wrong thing. Note
`deploy/apply_migration.py` checks that the project ref in `DIRECT_URL` matches the
one in `SUPABASE_URL` before DDL; `deploy/db_snapshot.py` performs no such check, so
it is yours to make.

## Step 2 - Take the recovery point, and pin it

```bash
cd /opt/scangrade
sudo bash deploy/scangrade-db-snapshot.sh --label pre-073-075
sudo /opt/scangrade/.venv/bin/python /opt/scangrade/deploy/db_snapshot.py \
  --pin /var/backups/scangrade/<archive>
```

Pinning is not decoration: rotation keeps five snapshots by default, and the one
archive that could undo a migration must not be rotated away by the next four
releases. `--pin` runs without credentials on purpose, so it still works on a box
whose key has been rotated.

Record: archive name, byte size, mode `0600`, `total_rows`, number of tables.

**Stop if:** the snapshot is refused, the archive is not `0600`, or the label is not
the one you asked for. Nothing after this step means anything without it.

## Step 3 - Tier 1: prove the archive itself, offline

No credentials, no second project, no extraction. This compares every table's bytes
in the archive against the digest the manifest recorded for it, and the row count
against the manifest's own count. It catches a truncated export, a partial upload
and a corrupt archive - the failures that make a tier 2 restore a waste of a window.

```bash
sudo /opt/scangrade/.venv/bin/python - /var/backups/scangrade/<archive> <<'PY'
"""Every table's bytes must match the digest the manifest recorded for it."""
import hashlib, json, sys, tarfile

with tarfile.open(sys.argv[1]) as tf:
    manifest = json.load(tf.extractfile("manifest.json"))
    bad, total = [], 0
    for name, meta in sorted(manifest["tables"].items()):
        blob = tf.extractfile(f"tables/{name}.ndjson").read()
        total += blob.count(b"\n")
        if hashlib.sha256(blob).hexdigest() != meta["sha256"] or \
                blob.count(b"\n") != meta["rows"]:
            bad.append((name, meta["rows"], blob.count(b"\n")))
print(f"tables {len(manifest['tables'])}, manifest rows {manifest['total_rows']}, "
      f"archive rows {total}, mismatched tables {len(bad)}")
for name, want, got in bad:
    print(f"  MISMATCH {name}: manifest {want} rows, archive {got}")
sys.exit(1 if bad or total != manifest["total_rows"] else 0)
PY
```

Exit 0 requires `mismatched tables 0` **and** `manifest rows` equal to `archive rows`; this is
tier 1 passing. Record those four numbers.

Two honest limits: it reads members through `tarfile` in memory, so it creates no
copy on disk - do not "helpfully" untar the archive instead. And it proves the
archive matches **its own manifest**; it does not prove the manifest came from
production. That is step 1's host line plus the label from step 2.

## Step 4 - Tier 2: build the isolated target

Never prove a restore by restoring into production. A restore overwrites; the
pre-restore snapshot is a fallback, not a test.

The target needs the schema first, because the archive carries rows only. Give it
the schema with the repository's own migrations - which doubles as a rehearsal that
the chain builds what the archive expects.

Set **all three** credentials to the scratch project. This matters more than it
looks: the recovery point `--commit` takes follows `SUPABASE_URL`, while the DDL
runs over `DIRECT_URL`. Set only `DIRECT_URL` and each commit takes a production
snapshot while applying to the scratch project:

```bash
cd /opt/scangrade
export SUPABASE_URL=<scratch>
export SUPABASE_SERVICE_KEY=<scratch>
export DIRECT_URL=<scratch, the pooler>
export SCRATCH_LEDGER=/srv/restore-proof/migration-ledger.json

python3 -c 'import os, urllib.parse as u; print(u.urlsplit(os.environ["SUPABASE_URL"]).hostname)'

python deploy/apply_migration.py --status          # the list production already carries
for f in <that list, in filename order>; do
  python deploy/apply_migration.py "supabase/migrations/$f" \
    --commit --no-snapshot --ledger "$SCRATCH_LEDGER" || break
done
```

`--no-snapshot` is right here and wrong on production: the recovery point a scratch
replay would take guards nothing, because the target is disposable and the real
recovery point is step 2's pinned archive. `--ledger` keeps the scratch project's
records out of the production ledger at `/var/lib/scangrade-migrations`.

Then confirm the scratch schema really is at that baseline rather than assuming it:

```bash
python deploy/schema_contract.py --live --require-applied
```

`--require-applied` fails when the live schema is behind the repository. A scratch
project that fails it is not a valid target, and a restore into it proves nothing.

## Step 5 - Tier 2: restore into the isolated target

Report first, then write. Both invocations keep the scratch credentials in the
environment:

```bash
SUPABASE_URL=<scratch> SUPABASE_SERVICE_KEY=<scratch> \
  python deploy/db_snapshot.py --restore /var/backups/scangrade/<archive> --dry-run

SUPABASE_URL=<scratch> SUPABASE_SERVICE_KEY=<scratch> \
  python deploy/db_snapshot.py --restore /var/backups/scangrade/<archive>
```

The dry run prints, per table, how many rows it would write and which columns it
holds back for a second pass. Read it before running the real thing: a table you
expected to be populated and is reported "empty in the snapshot" is the finding, and
here is where it is cheap.

The real run reports a failure map per table. Record the totals.

**The target must be empty.** The restore writes with merge-on-conflict, so a second
run into a populated project takes the UPDATE path - and several tables carry
`BEFORE UPDATE` triggers that set `updated_at` to now (classes, schools, subjects,
teachers, students among them). Their digests then differ for a reason that has
nothing to do with the archive. To redo a restore, recreate the scratch tables
first, or expect to explain every digest that moved.

## Step 6 - Verify the read-back

1. **Counts.** Snapshot the scratch project and compare it to the production
   manifest, table by table:

   ```bash
   SUPABASE_URL=<scratch> SUPABASE_SERVICE_KEY=<scratch> \
     python deploy/db_snapshot.py --label proof-readback --out /var/backups/scangrade
   ```

   Every table's `rows` must equal the production manifest's. This is the check that
   catches a truncated export, and it must never be skipped.

2. **Digests.** Compare `tables[*].sha256`. Equal is a strong result: the same rows
   in the same order digest the same. Not equal is not automatically a failure but
   it is not a pass either - name the cause before signing: a non-empty target (the
   UPDATE path above), a column default that differs, or a table whose `order_by` is
   not stable. An unexplained mismatch is a FAIL.

3. **Spot checks.** Take three rows you can name - one exam whose `answer_key` you
   know, one submission with a `started_at`, one school with an NPSN - and compare
   the restored copy with the production read. Counts prove volume; these prove the
   columns arrived, including the ones the restore held back and patched in its
   second pass.

4. **Confirm the target.** Print the host again. The evidence is about the scratch
   project only if the restore went to the scratch project.

## Step 7 - Record the proof, then prepare 073-075

The record is short and redacted: host names, archive name, the four tier 1
numbers, per-table counts, digests matching or the named reason they do not, and
PASS or FAIL per step. No keys, no connection strings, no row contents.

Only then, and still without applying anything from this page:

```bash
python deploy/apply_migration.py --status      # what the ledger recorded
python deploy/apply_migration.py --verify      # what the schema actually carries
python deploy/apply_migration.py supabase/migrations/073_scan_live_cleanup_reconcile.sql
```

The third command is a trial: it runs the file for real inside a transaction and
rolls it back, so a DDL error is found before production sees it. `--commit` takes
its own recovery point and is a separate decision, taken with the release request,
not with this evidence.

## What this page does not claim

* **It has not been executed against a real Supabase project.** Every command is
  read from the four tools. The tier 1 script was run against a synthetic archive
  built in the archive's own format - once intact, once with a row removed - and it
  passed the first and named the table in the second. Tier 2 has not been run at
  all. Treat this as a procedure to follow, not as evidence collected.
* **It is not point-in-time consistent.** Tables are read one after another, so a
  row written mid-export can land in one table and not another. Supabase's own
  physical backups and PITR are the consistent ones.
* **It proves data, not schema.** A migration that drops a column cannot be undone
  by this archive.
* **It does not restore passwords**, and it does not restore `auth` rows.

## Abort conditions

Stop, and say so in the record, when:

* the snapshot in step 2 is refused, or the archive is not root-only
* tier 1 reports a mismatched table, or the row totals disagree
* the host printed in step 1 is not production
* the scratch schema fails `--require-applied`
* any table's restored count is below the manifest
* a digest differs and the cause cannot be named
* the restore's own pre-restore snapshot could not be taken

In the last case the target's current state is the only copy of it, so the restore
does not run. That refusal is the tool protecting the data, not a malfunction to
work around.
