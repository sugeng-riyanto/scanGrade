# Development Guide

## Project Structure

```
scanGrade/
├── app/                    # Flask application
│   ├── __init__.py         # App factory (create_app)
│   ├── config.py           # Configuration classes
│   ├── errors.py           # Custom exceptions
│   ├── decorators/         # @require_school_access, @require_subscription
│   ├── handlers/           # Error handlers
│   ├── middlewares/        # Auth middleware aliases
│   ├── models/             # Supabase query helpers
│   ├── routes/             # Blueprints (auth, teacher, student, admin, api...)
│   ├── services/           # Business logic (omr, ai, midtrans, export...)
│   ├── templates/          # Jinja2 HTML templates
│   ├── static/             # JS (Alpine), images
│   └── utils/              # Auth, logger, rate limiter, CSRF
├── supabase/migrations/    # SQL migration files
├── docs/                   # Documentation
├── tests/                  # Pytest test files
├── deploy/                 # Systemd service + deploy script
├── manage.py               # Demo data management CLI
├── wsgi.py                 # Gunicorn entry point
└── requirements.txt        # Python dependencies
```

## Coding Conventions

- **Python**: PEP8, snake_case, type hints where helpful
- **JavaScript**: camelCase, Alpine.js reactive properties
- **HTML**: Jinja2 templates, Tailwind CSS utility classes
- **No comments in code** — keep it readable through clear naming
- **No emojis in code** (only in user-facing messages)

## How to Add a New Route

1. Create a new blueprint file in `app/routes/` or add to existing one
2. Register in `_register_blueprints()` in `app/__init__.py`
3. Create template in `app/templates/` (if rendering HTML)
4. Add `@require_school_access` decorator for school-scoped routes

Example:
```python
# app/routes/my_new_feature.py
from flask import Blueprint, render_template
from app.utils.auth import guru_required, get_supabase
from app.decorators.security import require_school_access

my_bp = Blueprint("my", __name__, url_prefix="/my")

@my_bp.route("/<resource_id>")
@guru_required
@require_school_access("exams", "resource_id")
def my_view(resource_id):
    return render_template("my/view.html")
```

## How to Add a New Service

1. Create file in `app/services/`
2. Import in route file
3. Services should be stateless functions, not classes

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_error_handling.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

### Carrying a change between checkouts (`deploy/port_change.py`)

This repository is worked in sibling checkouts, and a change made in one is often carried
to the others. That was a script per port, and the part that differed between them was
never the write — it was the assertion. `deploy/port_change.py` makes the assertion the
interface: the target must be **byte-identical to a declared base** or nothing is written.

```bash
python deploy/port_change.py app/services/foo.py \
    --from ../seb4 --to ../key-order --to ../school-switch \
    --base git:HEAD                 # or sha256:<hex>, or a file holding the "before" copy

python deploy/port_change.py app/services/foo.py --from ../seb4 --to ../key-order \
    --base before/foo.py --dry-run  # who would be refused, nothing written, nothing run

python deploy/port_change.py --restore ~/.scangrade-port/backups/20261017T101112-ab12
```

There is no default base. A base taken from the target's own content would make every
write legal, which is the one thing the tool exists to prevent. `git:<rev>` is read
**per checkout** (`HEAD` means "this target is unmodified since its own commit"), which
is what makes it usable across branches that share no revision; a *file* is the cross-
checkout form. A target that already holds exactly the new bytes is reported `identical`
and left alone, so re-running a port is safe.

After writing, the tool runs **that checkout's** checks: its own suite (`tests` by
default, `--suite` to narrow) and the mutation harnesses **whose `GUARDS` name the file**,
read from that checkout's `deploy/mutation_check.py --list`. A checkout without that
registry falls back to `.freebuff/mutate_<stem>.py`, by exact stem only. Exit codes are
the ones the rest of `deploy/` uses: `0` ported and every check passed, `1` ported but a
check found something (red suite, survivor, stale anchor), `2` refused or could not be
measured — never a quiet zero. A red suite, a suite that collected nothing and a registry
that will not answer are three different findings and all three are reported as what they
are.

Every byte it replaces is copied under `~/.scangrade-port/backups/<utc-stamp>/` with a
`manifest.json` (before and after digests, original mode) before the write, so a run that
dies mid-port is still restorable; `--restore` refuses a file whose bytes changed after
the port unless `--force`. The write itself is atomic and keeps the mode bits — a ported
`deploy/*.sh` that lost its executable bit is a gate that cannot run. Side effects the
*checks* caused are listed per checkout (`git status` before against after), so a gate
that rewrites a baseline during verification is visible rather than becoming tomorrow's
unexplained diff.

### The numbers in a ported sentence are measured here, not there

A bullet carried between checkouts is the one kind of copy that can arrive true and be
false in the same commit: `` `tests/unit/<module>.py` (32 tests) `` and
`` `11/11 injected defects caught` `` were both measured in the checkout the
sentence was written in, and both move with the code around them. So the
lines a port **adds** are read before the write, for the two shapes this repository
writes a measurement in — a test module with its size (`(32 tests)`, `(32)` directly
after the path, or a bare basename like `foo.py` (32)), and a harness with its score
(`11/11 injected defects caught`, the Indonesian spelling, or `7 caught, 0 survived`).
The number is bound to the **nearest** subject on the line, so a bullet naming three
suites and their counts cannot have the second count read against the first path. The
subject of a count is a module pytest would collect (`tests/…`, or a `test_*.py`), never
a tool — `deploy/port_change.py` is not a suite, and a count bound to it refused a port
over the tool's own prose — and the count must **follow** its subject, because a count
written before every path is a sentence about a shape rather than a claim.

Each number is then measured in the target checkout itself: a test module is
**collected** (`--collect-only`, so no fixture runs) and a harness is **run**. Three
outcomes are reported apart, because they are three different facts — `ok`, `differs`
or `missing` (the target collects another number, or has no such file — a sentence naming
a file this checkout lacks is a port that landed somewhere it does not belong), and
`unmeasured` (the module will not import, the harness printed no score). The last two
refuse the write *before* it happens: `could not look` is not `matches`, and the one
transient case — a claim beyond `--claim-limit 25` — is reported as not checked rather
than as holding. `--claims auto` (the default) reads Markdown ports only, `on` reads
every file, and `off` is how a run says out loud that it wrote without looking.

The tool's own guards are `tests/unit/test_port_change.py`; the harness's two — every
mutation anchor still exists in the shipped tool, and a killed run's copy repairs it —
live in `tests/unit/test_port_change_harness.py`, because an anchor assertion inside the
file the harness runs once per mutation fails under *every* injection and turns all of
them into false catches.

### Measuring the pages in a browser

The pages a teacher and a pupil actually use are measured by a headless browser on
**every push** (`.github/workflows/browser-gate.yml`), and the same command runs on a
laptop with no database, no `.env` and no deploy box:

```bash
python deploy/browser_gate.py                 # all three parts, from this checkout
python deploy/browser_gate.py --only touch    # the exam builder's 44px finger floor
python deploy/browser_gate.py --only render   # the pupil's exam page
python deploy/browser_gate.py --only seb      # the SEB handshake page's own script
python deploy/browser_gate.py --json-out reading.json
```

`deploy/fixture_app.py` serves *this checkout's* app — the real `create_app`, the real
routes, templates and static assets — on `127.0.0.1`, with exactly two things replaced:
the Supabase service client and the GoTrue client, by in-memory stand-ins seeded from
deploy/demo_exam_fixture.py. Sign-in is the app's own `/auth/login-user` POST, so a
gate cannot measure a page it reached some other way. `SG_CHROME` points all of them at
one browser (see `deploy/touch_gate.py`).

Exit codes, the same three the gates use: **0** measured and clean, **1** a finding,
**2** nothing could be measured (no browser, a page that would not render, a server that
would not answer). A part whose subject this release does not ship — a branch with no
SEB door has no handshake page — is reported by name and left out of the verdict. The
workflow fails a job on exit 2 as well: on the box a release it could not measure is kept
loudly, but a CI run that cannot look must not go green for it.

Aim it at a running app instead of the fixture with `--base-url`, `--teacher`,
`--student` and `--seb-exam` — useful against a dev server, and what the deploy box's own
gates already do against the served release. A green fixture is **not** evidence about
the box's database, accounts or TLS: `deploy/touch_gate.py`, `deploy/exam_render_gate.py`
and `deploy/seb_door_gate.py` still run in the deploy runner.

### When the checkout is too dirty to build

`app/utils/checkout_integrity.py` refuses to construct the app once a checkout holds
more than its scan cap (**64**) of modified *tracked* files. That is correct for a box
that serves, but it also stops the suite running on a laptop with several features in
flight. Two answers, and the first one is the usual one.

**In place.** Export the development permission and run the suite or the gate where
the work is:

```bash
SCANGRADE_ALLOW_DIRTY_CHECKOUT=1 pytest tests -q
SCANGRADE_ALLOW_DIRTY_CHECKOUT=1 bash deploy/theme_gate.sh
```

Without the variable the gate does not invent a verdict. Its checks are settled by one
app, so it sees this refusal as a wall of `error at setup` — a release finding it is not,
and a pass it is not either: it prints **exit 2, `NOT CHECKED`** and names the command
above. (Its exit 1 is for the failure it looks for, and a *measured* blob keeps that one:
nothing about the checkout's cap makes a path unreproducible.)

It waives the *cap* — "more than 64 modified files, so the rest could not be measured"
— and nothing else. A **measured** blob (a path whose stored bytes the path's own
`.gitattributes` filter could never write) still refuses whatever the variable says,
because that one is a defect in the repository rather than a limit of the scan; its
remedy is `deploy/scangrade-recover.sh` or a snapshot. The permission needs the
variable **and** a config that is not `ProductionConfig` (`IS_PRODUCTION` is a class
attribute, so no `.env` on a box can grant it) and not the deploy's probe — so it can
never answer the question for a release. Two things follow, and both are deliberate: a
run that used it prints `SCANGRADE-DIRTY-CHECKOUT-ALLOWED` to stderr, so it is never
mistaken for a run that measured; and a waived run is *not* evidence that the checkout
is reproducible, so the gate that matters — `deploy/theme_gate.sh` in the release and
the deploy runner before it merges — still asks and still refuses.

**A snapshot**, when you want a tree that is clean by construction (or you are in the
measured-blob case above). Snapshot the working tree into a committed scratch worktree
and run there:

```bash
python deploy/dirty_snapshot.py --run      # snapshot, then `pytest tests -q` inside it
python deploy/dirty_snapshot.py            # just snapshot; prints the path and commit
python deploy/dirty_snapshot.py --run --pytest "tests/unit/test_foo.py -q"
python deploy/dirty_snapshot.py --clean    # remove the scratch worktree
```

The snapshot is a linked worktree under `.freebuff/snapshot` (gitignored, so it never
becomes a dirty path in the tree it came from) holding your tracked edits, untracked
files and deletions as one commit — so the tree the suite measures is clean and the cap
is not reached. Your own checkout is only ever **read**: the edits are copied, never
stashed or applied. Ignored files (`.env`, `.venv`) stay behind, and `--run` uses this
checkout's own interpreter, so the dependencies under test are still the ones here.

`node_modules` is the one exception, and it is a **link** rather than a copy: without
it `deploy/theme_gate.sh` cannot run the Tailwind CLI and reports the stylesheet check
as *could not measure*, which is not a pass — so the snapshot would quietly stop
checking the half of the gate that catches a class no build ever emitted. The link is
a Windows junction (`mklink /J`) and is removed by unlinking the name, never by
`rmtree` or `git worktree remove`: both of those follow a reparse point and delete the
**target's** contents, which once emptied this checkout's own `node_modules`. If you
clear a snapshot by hand, use `--clean`, or `cmd /c rmdir` on the link first.

## Debugging

- Use `app.logger.info()` for debug messages (structured JSON)
- Browser: F12 → Console for JavaScript errors
- Flask debug mode: `flask run --debug` (auto-reloads on changes)
