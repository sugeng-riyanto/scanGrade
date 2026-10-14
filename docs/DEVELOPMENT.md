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

### A checkout with no `.env`

`manage.py` boots a real app at import time, and `create_app` refuses to boot without
`SUPABASE_URL` and `SUPABASE_SERVICE_KEY` — which a developer's box gets from the
checkout's `.env`, a **gitignored** file. So a fresh worktree (`git worktree add`, a
clone, `deploy/dirty_snapshot.py --run`, whose snapshot deliberately carries no
ignored files) has no environment to boot it with, and the 27 tests that read the
seed out of `manage.py` used to be reported as **ten errors and seventeen failures
raised inside the import** — a wall of "missing required env vars" that looks like a
broken suite and says nothing about the code. (The run showed *eighteen* failures.
The eighteenth was not the environment: `create_app(SomeConfig)` — a config
*class*, the shape `_resolve_config` documents — was stringified before it was
resolved, so its caller silently got `DevelopmentConfig` and the test passed on a
box with a `.env` while failing on a fresh worktree. `get_config` hands a class
straight back now.)

They are **skipped** now, with the reason naming the file and the variable
(`tests/conftest.py::require_bootable_manage_app`), because an absent `.env` is a
property of the checkout rather than a finding about the release. Everything around
them keeps running: most of those files read `manage.py` as *text*, which needs no
environment, and so do the rest of the ~9,100 tests. On a box with a `.env` — the
VPS, your own checkout — nothing is skipped, and `tests/unit/test_app_fixture.py`
fails if a test file ever executes `manage.py` without asking the guard first.

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

**A clean tree is not a fresh box**, and the difference matters when the subject *is*
the environment: the snapshot lives **inside** this checkout (`.freebuff/snapshot`), and
python-dotenv resolves a `.env` by walking **up** from the file that asks — so a run
there still finds this checkout's `.env` and boots like a box that has one (measured:
`find_dotenv()` from `.freebuff/snapshot` answers
`F:\opencode\ScanGrade\main-merge\.env`). To reproduce a worktree with no environment at
all, put the worktree **outside** the checkout (`git worktree add /tmp/sg-noenv HEAD`,
which is also what `git worktree add` in the paragraph above means for a real one).

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
