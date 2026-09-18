"""Guards for the pull-based auto-deploy.

The VPS polls ``origin/main`` every two minutes and reloads itself, with nobody
watching. That makes a few properties load-bearing rather than cosmetic, and each
one is easy to lose in an innocent-looking edit:

* **The reload must be graceful.** ``systemctl reload`` sends SIGHUP, and gunicorn
  finishes in-flight requests (``graceful_timeout``) before retiring workers. Drop
  ``ExecReload`` and the script silently falls back to ``restart`` — which cuts
  off a student mid-exam, the exact thing an unattended deploy must not do.
* **The script must not take arguments.** It is the only thing root runs
  unattended; if it could be steered, it would be a general-purpose command
  runner with root.
* **It must be able to go backwards.** An unattended deploy that only moves
  forward turns a bad commit into an outage that lasts until someone notices.
* **Constructing the app must not delete anything.** The retention loop runs a
  purge pass the moment it starts, so any code path that merely builds an app —
  the test suite, the deploy's own smoke gate — would purge whatever Supabase the
  config points at.
"""
import os
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

import subprocess
import sys

BASH = shutil.which("bash")

DEPLOY_SH = DEPLOY / "scangrade-deploy.sh"
INSTALL_SH = DEPLOY / "install-auto-deploy.sh"
ENTRYPOINT_SH = DEPLOY / "entrypoint.sh"
SMOKE_PY = DEPLOY / "smoke_test.py"
APP_SERVICE = DEPLOY / "scangrade.service"
#: The background worker, which shares the checkout with the app but is a
#: separate process with its own copy of every imported module.
WORKER_UNIT = DEPLOY / "celery.service"
DEPLOY_SERVICE = DEPLOY / "scangrade-deploy.service"
DEPLOY_TIMER = DEPLOY / "scangrade-deploy.timer"


# ── the graceful reload ──────────────────────────────────────────

def test_app_unit_can_be_reloaded_gracefully():
    unit = APP_SERVICE.read_text(encoding="utf-8")

    assert re.search(r"^ExecReload=.*kill.*-s HUP.*\$MAINPID", unit, re.M), (
        "scangrade.service has no ExecReload: `systemctl reload` fails, so every "
        "auto-deploy becomes a hard restart that interrupts students mid-exam"
    )


def test_deploy_prefers_reload_and_keeps_a_restart_fallback():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    assert "systemctl reload" in script, "the graceful path is gone"
    assert "systemctl restart" in script, (
        "no fallback for a host whose unit cannot be reloaded — the deploy would "
        "either skip the reload or fail outright"
    )
    assert script.index("systemctl reload") < script.index("systemctl restart"), (
        "restart must be the fallback, not the default"
    )


# ── every process holding this checkout is put on the new code ───

def _celery_tasks():
    """Every task class the repository declares."""
    found = []
    for path in (ROOT / "app").rglob("*.py"):
        if "@celery_app.task" in path.read_text(encoding="utf-8", errors="replace"):
            found.append(path)
    return found


def _unit_the_installer_creates() -> str:
    """The systemd unit name an installer actually puts on the box.

    Read from the file that copies it, so the check is between two files rather
    than against a name this test happens to remember.
    """
    manual = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    found = re.search(r"/etc/systemd/system/([\w.-]*celery[\w.-]*)", manual)
    assert found, "no installer copies a celery unit into systemd"
    return found.group(1)


def test_the_deploy_restarts_the_worker_that_shares_the_checkout():
    """Reloading gunicorn does not reload the worker, and the difference is fatal.

    The worker imports its task modules once and keeps them for the life of the
    process, so after a deploy it is running the *previous* release. `page_index`
    was added to `process_omr_scan`'s signature and to its caller in one commit;
    the deploy reloaded the app alone, and every scan then answered

        process_omr_scan() got an unexpected keyword argument 'page_index'

    with the caller new and the worker old. Nothing on the box said so, which is
    why this is a test and not a comment.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    tasks = _celery_tasks()
    assert tasks, (
        "no Celery tasks in the repository — if the queue was removed, this check "
        "and the worker unit should go with it"
    )

    declared = re.search(r'WORKER_UNIT="([^"]+)"', script)
    assert declared, (
        f"the deploy names no worker unit, but {tasks[0].relative_to(ROOT)} declares a "
        "Celery task: the worker keeps last release's modules, so a changed task "
        "signature fails in production while the site looks healthy"
    )
    # Restarting a name no installer creates is a no-op that reads as a fix.
    assert f"{declared.group(1)}.service" == _unit_the_installer_creates(), (
        f"the deploy restarts '{declared.group(1)}', but the installer creates "
        f"'{_unit_the_installer_creates()}' — the restart would do nothing"
    )
    assert re.search(r"systemctl restart \"\$WORKER_UNIT\"", script), (
        "the worker has to be restarted, not merely named: Celery has no SIGHUP "
        "reload, so a signal would leave the old modules in memory"
    )


def test_the_worker_is_restarted_on_the_forward_path_not_only_on_rollback():
    """The forward path is the one that fixes the reported bug.

    Written after noticing the first three checks all pass with the forward-path
    call deleted: the unit is still named, the definition still exists, and the
    last `reload_worker` is still after the last `reload_app` (both now in the
    rollback path only). This is the assertion that actually holds the fix.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert re.search(r"^reload_worker \|\| WORKER_STALE=1$", script, re.M), (
        "the forward path no longer reloads the worker. Reloading gunicorn alone "
        "leaves the worker on the previous release, which is the bug this guards."
    )
    # ...and the flag it sets has to be reported, or the half-deploy is silent.
    assert re.search(r"^WORKER_STALE=0$", script, re.M), (
        "WORKER_STALE is read before it is set; under `set -u` that aborts the "
        "deploy, and without it the warning is unreliable"
    )


def test_the_deploy_reloads_the_worker_on_the_way_back_too():
    """A rollback that leaves the worker ahead is the same mismatch, hidden.

    After a rollback the site looks healthy — the app is serving the old revision
    — while every background task calls into a newer module. That is the failure
    mode nobody sees, because the symptom (scans erroring) reads as a scan bug.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    app_reload = script.rindex('reload_app')
    worker_reload = script.rindex('reload_worker')
    assert worker_reload > app_reload, (
        "the last `reload_worker` call is not in the rollback path — the worker "
        "would keep the rejected release's code"
    )


def test_an_unrestarted_worker_is_reported_at_the_end_of_the_deploy():
    """The app is verified by a probe; the worker is verified by nothing."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert re.search(r'systemctl restart \"\$WORKER_UNIT\"', script)
    # The warning belongs in the success path, after 'DEPLOY OK' — that is the
    # line that could otherwise say everything is fine while scans fail.
    ok = script.index("DEPLOY OK")
    assert "WORKER_STALE" in script[ok:], (
        "a worker that failed to restart is never reported, so a half-deployed "
        "release reads as a clean one"
    )


def test_the_worker_unit_runs_this_app_from_the_checkout():
    """A worker pointed at the wrong app or the wrong directory proves nothing.

    It must import the same Celery app the tasks are registered on, and run from
    the directory the deploy updates — otherwise restarting it is a gesture.
    """
    worker = WORKER_UNIT.read_text(encoding="utf-8")
    assert "-A app.celery_app" in worker, (
        "the worker does not load app.celery_app, so it is not the worker the "
        "tasks are published to"
    )
    repo_dir = re.search(r'^REPO="([^"]+)"', DEPLOY_SH.read_text(encoding="utf-8"), re.M)
    assert repo_dir, "the deploy script no longer pins the checkout it updates"
    assert f"WorkingDirectory={repo_dir.group(1)}" in worker, (
        f"the worker runs from somewhere other than {repo_dir.group(1)} — the "
        "directory the deploy updates — so a restart would reload the wrong code"
    )
    assert re.search(r"^Restart=always", worker, re.M), (
        "the worker must come back on its own: the deploy restarts it, and a unit "
        "that stays down after a failed start takes async OMR with it"
    )


# ── the bytes Gate 0 compares ────────────────────────────────────

#: The files that are read as *bytes* rather than as text. Gate 0 compares the
#: runner with `cmp -s`, and `test_a_line_ending_difference_is_a_difference`
#: exists precisely because `cmp` must not normalise what it compares.
BYTE_COMPARED = (DEPLOY_SH, ENTRYPOINT_SH, DEPLOY / "scangrade-db-snapshot.sh")


@pytest.mark.parametrize("path", BYTE_COMPARED, ids=lambda p: p.name)
def test_the_scripts_compared_as_bytes_are_lf_on_disk(path):
    """A CRLF copy of the runner makes Gate 0's byte comparison meaningless.

    `.gitattributes` declares `eol=lf` for `*.sh`, `*.py` and `*.md`, so a fresh
    checkout — and the Linux box — always gets LF. A Windows checkout does not,
    and the failure is silent twice over: any tool that reads a file with
    `read_text` and writes it back with `write_text` converts the whole file to
    CRLF (`write_text` translates `\n` to the platform separator), and
    `core.autocrlf=true` means those CRs never reach the index, so **`git status`
    shows nothing at all**. What it breaks is byte-level work: Gate 0's `cmp -s`,
    and the test that proves `cmp` does not normalise — a repo file that is
    already CRLF makes its CRLF "drift" copy byte-identical, so the guard that
    should catch drift reports a match. That is not hypothetical: a scratch
    harness rewrote this file, and the only thing that noticed was a test that
    happened to compare bytes.
    """
    if not path.exists():
        pytest.skip(f"{path.name} is not installed in this checkout")
    data = path.read_bytes()
    assert b"\r\n" not in data, (
        f"{path.name} has CRLF line endings on disk. Gate 0 compares this file "
        f"with `cmp -s` and .gitattributes declares it eol=lf, so the working "
        f"tree must be LF — and git will not tell you, because autocrlf "
        f"normalises on the way into the index. Rewrite the file with LF."
    )


# ── the script root runs unattended ──────────────────────────────

def test_deploy_script_takes_no_arguments():
    """Nothing may be steered from outside.

    ``$@`` is allowed on a function-definition line: the helpers forward the
    script's *own* fixed argv into ``runuser``. What must never appear is a read
    of the script's invocation arguments, at any level.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")

    for placeholder in ("$1", "${1}", "getopts", "shift"):
        assert placeholder not in script, (
            f"the deploy script accepts input ({placeholder}) — it is the one "
            "thing root runs unattended and must not be steerable"
        )
    for line in script.splitlines():
        if re.search(r"\$(?:@|\*)", line):
            assert re.search(r"\w+\(\)\s*\{", line), (
                "`$@` outside a helper: the script would be forwarding arguments "
                f"it was called with — {line.strip()!r}"
            )
    assert "BRANCH=\"main\"" in script and "REPO=\"/opt/scangrade\"" in script, (
        "repo and branch must be fixed in the script, not passed in"
    )


def test_deploy_script_can_roll_back():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    assert "reset --hard" in script, "no way back to the previous commit"
    assert script.count("reset --hard") >= 3, (
        "a rollback is needed after each gate that can reject a release "
        "(pip install, compileall, app construction) plus the health check"
    )
    assert "BEFORE=$(as_owner git -C \"$REPO\" rev-parse --short HEAD)" in script, (
        "the pre-deploy commit must be captured before anything is pulled"
    )


def test_deploy_script_refuses_to_clobber_local_edits():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    assert "status --porcelain" in script, (
        "a dirty checkout must stop the deploy: --hard on top of hand edits "
        "loses them with no way back"
    )


@pytest.mark.parametrize("script", [DEPLOY_SH, INSTALL_SH])
def test_scripts_do_not_point_home_at_the_checkout(script):
    """A self-inflicted trap worth pinning.

    ``runuser`` leaves HOME as the caller's, so it was set to ``$REPO`` — which is
    wrong twice over: git then looks for credentials in a directory that is not
    the user's home, and pip drops its cache in ``$REPO/.cache``, which arrives as
    an untracked file and trips the script's own dirty-checkout guard on every
    later run.
    """
    text = script.read_text(encoding="utf-8")

    assert 'HOME="$REPO"' not in text
    assert "getent passwd" in text, "HOME should come from the owner's passwd entry"


def test_deploy_script_is_not_a_general_command_runner():
    """Every command it runs is fixed; nothing is taken from the environment."""
    script = DEPLOY_SH.read_text(encoding="utf-8")

    assert "eval " not in script
    assert "bash -c" not in script and "sh -c" not in script


def test_installer_refuses_to_run_as_non_root():
    install = INSTALL_SH.read_text(encoding="utf-8")

    assert 'id -u' in install and "ROOT" in install, (
        "the installer writes into /etc/systemd/system and restarts the app; a "
        "non-root run must say so instead of failing halfway"
    )
    assert "install -m 0755" in install


def test_installer_reloads_the_app_it_just_updated():
    """Otherwise the install leaves production on stale code indefinitely.

    Step 1 pulls origin/main, but gunicorn keeps serving the copy it started
    with. Nothing else would reload it: the next timer tick sees the checkout
    already at origin/main and exits without deploying anything.
    """
    install = INSTALL_SH.read_text(encoding="utf-8")

    assert "systemctl reload" in install
    assert install.index("merge --ff-only") < install.index("systemctl reload"), \
        "the reload must come after the pull"
    assert 'is-active "$SERVICE"' in install and "answered" in install, \
        "a reload that left the app down would otherwise go unnoticed"


def test_installer_keeps_a_backup_of_the_unit_it_replaces():
    install = INSTALL_SH.read_text(encoding="utf-8")

    assert ".bak-" in install, (
        "overwriting the unit that keeps the site up without a backup leaves no "
        "way back if the repo copy is wrong"
    )


# ── the timer ────────────────────────────────────────────────────

def test_timer_does_not_try_to_replay_missed_ticks():
    timer = DEPLOY_TIMER.read_text(encoding="utf-8")

    assert re.search(r"^Unit=scangrade-deploy\.service", timer, re.M)
    assert "WantedBy=timers.target" in timer
    assert not re.search(r"^Persistent=true", timer, re.M), (
        "one deploy brings the checkout to origin/main regardless of how many "
        "commits were missed; replaying ticks only means repeated restarts"
    )


def test_deploy_unit_points_at_the_installed_script():
    unit = DEPLOY_SERVICE.read_text(encoding="utf-8")
    install = INSTALL_SH.read_text(encoding="utf-8")

    assert "ExecStart=/usr/local/bin/scangrade-deploy" in unit
    assert "/usr/local/bin/scangrade-deploy" in install, (
        "the installer must put the script where the unit looks for it"
    )


# ── building the app must not purge anything ─────────────────────

def test_testing_config_does_not_start_background_schedulers():
    from app.config import TestingConfig

    assert TestingConfig.START_BACKGROUND_SCHEDULERS is False


def test_constructing_the_app_starts_no_schedulers():
    """The behavioural half of the rule above.

    ``purge_all()`` runs on the first loop iteration, so an app built purely to
    be inspected — as the deploy smoke gate does — used to trigger a real purge.
    """
    from app import create_app
    from app.services import cleanup_service, data_retention_service

    cleanup_service._cleanup_thread = None
    cleanup_service._running = False
    data_retention_service._retention_thread = None
    data_retention_service._running = False

    app = create_app("app.config.TestingConfig")

    assert app.config["START_BACKGROUND_SCHEDULERS"] is False
    assert not data_retention_service._running, "building the app started the purge loop"
    assert cleanup_service._cleanup_thread is None, "building the app started the cleanup loop"


def test_deploy_smoke_gate_runs_with_schedulers_off():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    gate = script[script.index("Gate 2"):]
    assert "START_BACKGROUND_SCHEDULERS=false" in gate, (
        "the deploy's own smoke test would purge data on every release"
    )
    assert 'create_app()' in gate, (
        "the gate must build the production configuration, so a broken .env or a "
        "missing key fails the deploy instead of the site"
    )


# ── the smoke test's page lists cannot silently rot ──────────────

def _smoke_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sg_smoke_test", SMOKE_PY)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: @dataclass resolves annotations through
    # sys.modules, and a module that is not registered makes it explode.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_smoke_page_is_a_real_route():
    """The failure this prevents is expensive and silent.

    A path in the smoke list that no longer exists returns 404, which counts as a
    failed check — so a typo or a renamed route would make the smoke test reject
    *every* release from then on, rolling back good code until someone reads the
    journal. Pinning the list against the app's own URL map makes that impossible.
    """
    smoke = _smoke_module()

    from app import create_app
    app = create_app("app.config.TestingConfig")
    registered = {rule.rule for rule in app.url_map.iter_rules()}

    missing = [p for paths in smoke.ROLE_PAGES.values() for p in paths if p not in registered]
    assert not missing, f"smoke test would 404 on {missing}"

    missing = [p for p in smoke.ROLE_AREAS.values() if p not in registered]
    assert not missing, f"isolation check targets routes that do not exist: {missing}"


def test_smoke_pages_are_namespaced_under_their_role():
    smoke = _smoke_module()

    prefixes = {"super_admin": "/super-admin/", "admin_sekolah": "/admin-sekolah/",
                "guru": "/teacher/", "murid": "/student/"}
    for role, paths in smoke.ROLE_PAGES.items():
        for path in paths:
            assert path.startswith(prefixes[role]), f"{path} is not a {role} page"


def test_smoke_roles_and_isolation_matrix_are_complete():
    smoke = _smoke_module()

    assert set(smoke.ROLE_PAGES) == set(smoke.ROLES)
    assert set(smoke.FORBIDDEN) == set(smoke.ROLES)
    assert set(smoke.LOGIN_PATHS) == set(smoke.ROLES)
    assert smoke.FORBIDDEN["super_admin"] == [], "super admin is allowed everywhere"
    # Every role is forbidden from every area above it, and from nothing else:
    # a role must never be listed as forbidden from its own area, and the matrix
    # must not accidentally leave a lower role free to reach a higher one.
    order = ["super_admin", "admin_sekolah", "guru", "murid"]
    for i, role in enumerate(order):
        assert role not in smoke.FORBIDDEN[role], f"{role} forbidden from itself"
        assert set(smoke.FORBIDDEN[role]) == set(order[:i]), (
            f"{role} should be refused {order[:i]}, got {smoke.FORBIDDEN[role]}"
        )


def test_smoke_test_only_reads():
    """It runs against production on every release: it must not change state."""
    src = SMOKE_PY.read_text(encoding="utf-8")

    for verb in (".put(", ".delete(", ".patch(", ".post("):
        if verb == ".post(":
            continue        # exactly one login POST, asserted below
        assert verb not in src, f"smoke test performs {verb.strip('.(')} — it must be read-only"
    assert src.count("session.post(") == 1, "the only write may be the login POST"


@pytest.mark.parametrize("env,expected", [
    ({}, []),
    ({"SMOKE_GURU": "a@b.c:pw"}, [("guru", "a@b.c", "pw")]),
    # A password containing ':' must survive: split on the FIRST colon only.
    ({"SMOKE_MURID": "a@b.c:p:w"}, [("murid", "a@b.c", "p:w")]),
    ({"SMOKE_GURU": "no-colon"}, []),
    ({"SMOKE_GURU": ":nopass"}, []),
    ({"SMOKE_NONSENSE": "a@b.c:pw"}, []),
])
def test_smoke_credential_parsing(env, expected, monkeypatch):
    smoke = _smoke_module()

    for key in ("SMOKE_SUPER_ADMIN", "SMOKE_ADMIN_SEKOLAH", "SMOKE_GURU", "SMOKE_MURID"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert [(a.role, a.email, a.password) for a in smoke.creds_from_env()] == expected


def test_smoke_test_reports_skip_when_unconfigured(tmp_path):
    """Exit 2 — not a failure. The deploy script relies on that distinction."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMOKE_")}
    result = subprocess.run(
        [sys.executable, str(SMOKE_PY)], env=env,
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "nothing to verify" in result.stdout


# ── how the deploy reacts to the smoke test ──────────────────────

def test_deploy_rolls_back_on_smoke_failure_only_when_armed():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    gate = script[script.index("Gate 3"):]
    assert "smoke_test.py" in gate, "the gate never runs the smoke test"
    assert 'SMOKE_ENFORCE' in gate, "nothing decides whether a failure rolls back"
    assert 'HEALTHY=0' in gate, "a failed smoke test must feed the rollback path"
    # Exit 2 is "nothing testable" and must not roll anything back.
    assert "smoke test skipped" in gate
    assert re.search(r"\b2\)", gate), "exit code 2 is not handled separately"


def test_installer_arms_the_gate_only_after_proving_the_credentials():
    install = INSTALL_SH.read_text(encoding="utf-8")

    assert "--check-credentials" in install
    arming = install.index("SMOKE_ENFORCE=.*/SMOKE_ENFORCE")  # the sed that arms it
    check = install.index("--check-credentials")
    assert check < arming, "the gate is armed before the credentials are proven"
    assert "SMOKE_ENFORCE stays false" in install, "no path leaves it unarmed on failure"
    assert "chmod 0600" in install, "the credential file holds passwords"


def test_smoke_config_is_not_in_the_repo():
    """It holds passwords; it must stay out of git forever."""
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "scangrade-smoke.conf"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tracked.returncode != 0
    for line in subprocess.run(["git", "ls-files"], cwd=ROOT,
                               capture_output=True, text=True).stdout.splitlines():
        assert "smoke" not in line or line.endswith("smoke_test.py") or line.endswith("test_auto_deploy.py"), (
            f"{line} looks like a tracked smoke credential file"
        )


# ── the installed entry point is a launcher, never a copy ────────
#
# /usr/local/bin/scangrade-deploy used to be a *copy* of the script in the
# checkout, installed by install-auto-deploy.sh. A copy stops receiving fixes the
# day it lands: the gates below could grow, correct themselves or change what
# they refuse, and root would go on running the version of that afternoon — with
# nothing comparing the two. The fix was to ship a launcher instead, and these
# guards hold the arrangement in place.

def _render_launcher(directory: Path, repo: Path, name: str) -> Path:
    """What install-auto-deploy.sh does: substitute the checkout into the
    launcher and install it under one of the two names it answers to."""
    rendered = ENTRYPOINT_SH.read_text(encoding="utf-8").replace("@REPO@", str(repo))
    path = directory / name
    path.write_text(rendered, encoding="utf-8")
    return path


def test_the_installer_installs_a_launcher_rather_than_a_copy():
    install = INSTALL_SH.read_text(encoding="utf-8")

    for copied in ("scangrade-deploy.sh", "scangrade-db-snapshot.sh"):
        assert not re.search(rf"^install[^\n]*\b{copied}\b", install, re.M), (
            f"{copied} is installed as a *copy* again. A copy stops receiving "
            "fixes the moment it is installed, and nothing compares it to the "
            "checkout, so the drift is silent. Install the launcher instead."
        )

    assert "deploy/entrypoint.sh" in install, "the installer does not know the launcher"
    for bin_path in ('install_launcher "$DEPLOY_BIN"', 'install_launcher "$SNAPSHOT_BIN"'):
        assert bin_path in install, f"{bin_path} is missing — that entry point stays a copy"


def test_the_launcher_is_rendered_and_never_installed_unrendered():
    """The placeholder is the only per-host thing in the launcher, and a launcher
    installed with it left in would exec `@REPO@/deploy/...` forever."""
    entry = ENTRYPOINT_SH.read_text(encoding="utf-8")
    install = INSTALL_SH.read_text(encoding="utf-8")

    code = "\n".join(ln for ln in entry.splitlines() if not ln.strip().startswith("#"))
    assert code.count("@REPO@") == 1, "the placeholder must appear exactly once in code"
    assert 'REPO="@REPO@"' in code
    assert re.search(r"sed\s+\"s\|@REPO@\|", install), (
        "the installer no longer renders the placeholder the launcher is written with"
    )
    guard = install.index("grep -q '@REPO@'")
    assert "exit" in install[guard:guard + 300], (
        "an unrendered launcher must fail the install, not reach root's PATH"
    )


def test_the_launcher_holds_no_deploy_logic_of_its_own():
    """The launcher must stay a launcher. The moment it grows a gate, a retry or
    a rollback, there are two runners in the world again — which is the problem."""
    entry = ENTRYPOINT_SH.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in entry.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))

    assert len(code.splitlines()) < 40, \
        f"the launcher has grown to {len(code.splitlines())} lines of code"
    # `snapshot` is not in the list on purpose: naming the two entry points is
    # this file's whole job. What may not appear is deploy *work*.
    for borrowed in ("git ", "systemctl", "runuser", "flock", "claims", "smoke"):
        assert borrowed not in code, (
            f"the launcher runs {borrowed!r} — that is the deploy script's job, "
            "and doing it here is how the two versions start to drift"
        )


def test_the_deploy_refuses_a_copy_before_it_touches_anything():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    assert "runner-identity:start" in script and "runner-identity:end" in script, (
        "Gate 0's delimiters also let it be tested on its own; keep them"
    )
    assert re.search(r'if \[ "\$SELF" != "\$REPO_RUNNER" \] && ! cmp -s', script), (
        "the refusal no longer compares the running file to the checkout's — a "
        "condition that stopped being evaluated (or was changed to `true`) would "
        "still leave the log line, and the message, in place"
    )
    refusal = script.index("REFUSING")
    for later in ('fetch --quiet origin', "merge --ff-only"):
        assert refusal < script.index(later), (
            f"the copy check runs after {later!r} — by then the checkout has moved "
            "and a stale runner has already deployed with old logic"
        )
    assert "install-auto-deploy.sh" in script[refusal - 400:refusal + 900], \
        "the refusal has to name the one command that fixes it"


IDENTITY_START = "# runner-identity:start"
IDENTITY_END = "# runner-identity:end"


def _identity_harness(repo: Path) -> str:
    """Gate 0, lifted out of the script and given the two things it needs: the
    checkout path and a log(). It is run with $0 set by the caller."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    block = script.split(IDENTITY_START, 1)[1].split(IDENTITY_END, 1)[0]
    return f'set -uo pipefail\nREPO="{repo}"\nlog() {{ echo "$*"; }}\n{block}\necho REACHED_END\n'


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the guard")
def test_gate_0_refuses_a_stale_copy_and_allows_the_checkout(tmp_path):
    """Three cases, because only the middle one is a bug:

    * a copy that differs from the checkout — a deploy about to run yesterday's
      logic, refused with exit 14;
    * the checkout's own file — the normal case, and it must pass;
    * a copy that still matches byte-for-byte — the same code, so it passes. A
      host installed before this change keeps deploying until its runner is
      actually out of date, instead of stopping on the first tick.
    """
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    checkout = repo / "deploy" / "scangrade-deploy.sh"
    checkout.write_text("#!/usr/bin/env bash\n# the checkout's runner\n", encoding="utf-8")

    installed = tmp_path / "scangrade-deploy"
    installed.write_text("#!/usr/bin/env bash\n# a copy installed months ago\n", encoding="utf-8")

    stale = subprocess.run([BASH, "-c", _identity_harness(repo), str(installed)],
                           capture_output=True, text=True)
    assert stale.returncode == 14, stale
    assert "REFUSING" in stale.stdout
    assert "install-auto-deploy.sh" in stale.stdout
    assert "REACHED_END" not in stale.stdout, "it refused but carried on"

    from_checkout = subprocess.run([BASH, "-c", _identity_harness(repo), str(checkout)],
                                   capture_output=True, text=True)
    assert from_checkout.returncode == 0 and "REACHED_END" in from_checkout.stdout
    assert "REFUSING" not in from_checkout.stdout

    same = tmp_path / "scangrade-deploy-identical"
    same.write_text(checkout.read_text(encoding="utf-8"), encoding="utf-8")
    matching = subprocess.run([BASH, "-c", _identity_harness(repo), str(same)],
                              capture_output=True, text=True)
    assert matching.returncode == 0 and "REACHED_END" in matching.stdout, matching


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the launcher")
def test_the_launcher_cannot_lag_behind_the_checkout(tmp_path):
    """The property the whole arrangement exists for: no matter when a fix lands
    in the repo, the installed entry point runs it — because there is nothing
    installed to fall out of date."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    target = repo / "deploy" / "scangrade-deploy.sh"
    # Deliberately not executable: the scripts are committed 0644, so the
    # launcher has to work without the bit.
    target.write_text('#!/usr/bin/env bash\necho "v1 ran with $# argument(s)"\n', encoding="utf-8")

    bins = tmp_path / "bin"
    bins.mkdir()
    launcher = _render_launcher(bins, repo, "scangrade-deploy")

    first = subprocess.run([BASH, str(launcher)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "v1 ran with 0 argument(s)"

    target.write_text('#!/usr/bin/env bash\necho "v2 ran with $# argument(s)"\n', encoding="utf-8")
    second = subprocess.run([BASH, str(launcher)], capture_output=True, text=True)
    assert second.stdout.strip() == "v2 ran with 0 argument(s)", (
        "the installed path kept running the old script — it is a copy again"
    )

    # Steerable? No: the deploy takes no arguments, and the launcher drops them
    # rather than passing them along.
    steered = subprocess.run([BASH, str(launcher), "--branch", "evil"],
                             capture_output=True, text=True)
    assert steered.stdout.strip() == "v2 ran with 0 argument(s)", steered.stdout


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the launcher")
def test_the_snapshot_entry_point_passes_its_own_flags_through(tmp_path):
    """Unlike the deploy, this one *is* a command — --list, --label, --restore —
    so the launcher has to forward, or the snapshot becomes unusable."""
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "scangrade-db-snapshot.sh").write_text(
        '#!/usr/bin/env bash\necho "snapshot called with: $*"\n', encoding="utf-8")

    bins = tmp_path / "bin"
    bins.mkdir()
    launcher = _render_launcher(bins, repo, "scangrade-db-snapshot")

    run = subprocess.run([BASH, str(launcher), "--list"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "snapshot called with: --list"


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the launcher")
def test_the_launcher_fails_loudly_when_it_cannot_do_its_job(tmp_path):
    """Two silent-failure modes to close: an installed name it does not know, and
    a checkout that is not there. Either one, quiet, is a deploy that never runs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    bins = tmp_path / "bin"
    bins.mkdir()

    unknown = _render_launcher(bins, repo, "scangrade-something-else")
    run = subprocess.run([BASH, str(unknown)], capture_output=True, text=True)
    assert run.returncode == 64 and "unknown launcher name" in run.stderr

    missing = _render_launcher(bins, repo, "scangrade-deploy")
    run = subprocess.run([BASH, str(missing)], capture_output=True, text=True)
    assert run.returncode == 3 and "is missing" in run.stderr


@pytest.mark.parametrize("name", ["env_bool"])
def test_env_bool_reads_the_usual_spellings(name):
    from app.config import env_bool
    import os

    for raw, expected in (("true", True), ("1", True), ("yes", True), ("on", True),
                          ("TRUE", True), ("false", False), ("0", False), ("no", False)):
        os.environ["SG_TEST_BOOL"] = raw
        assert env_bool("SG_TEST_BOOL", not expected) is expected, raw
    os.environ.pop("SG_TEST_BOOL", None)
    assert env_bool("SG_TEST_BOOL_UNSET", True) is True
