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
    from tests.conftest import build_app
    from app.services import cleanup_service, data_retention_service

    cleanup_service._cleanup_thread = None
    cleanup_service._running = False
    data_retention_service._retention_thread = None
    data_retention_service._running = False

    app = build_app("app.config.TestingConfig")

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

    from tests.conftest import build_app
    app = build_app("app.config.TestingConfig")
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

    gate = script[script.index("Gate 4"):]
    assert "smoke_test.py" in gate, "the gate never runs the smoke test"
    assert 'SMOKE_ENFORCE' in gate, "nothing decides whether a failure rolls back"
    assert 'HEALTHY=0' in gate, "a failed smoke test must feed the rollback path"
    # Exit 2 is "nothing testable": no account it recognises, a conf it cannot
    # parse, or a base URL this host cannot reach. The last is a property of the
    # box and never the release's fault — and the release still was not signed in
    # against, so it is not kept either. It keeps its own arm because the journal
    # has to say which of the two happened.
    assert re.search(r"\b2\)", gate), "exit code 2 is not handled separately"
    after_two = gate[gate.index("    2)"):]
    assert "HEALTHY=0" in after_two[:after_two.index("    *)")], (
        "a release nobody could sign in against is kept")
    # The one arm that keeps a bad release: a real failure from a box that has not
    # armed the gate. install-auto-deploy.sh arms it only after proving that the
    # accounts actually sign in, so an unarmed gate means a stale password —
    # evidence about the conf, never about the release.
    assert "keeping the release" in gate


def test_a_smoke_test_that_cannot_run_at_all_is_not_kept():
    """A gate that could not run is a gate that did not run.

    Each of these used to be one sentence in the journal and nothing else, which
    made "the gates ran" quietly false: an unarmed box deployed every commit while
    no release was ever signed in against.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    gate = script[script.index("SMOKE_CONF="):]
    for branch in ("does not parse", "no $SMOKE_CONF"):
        at = gate.index(branch)
        # Up to the reason: a branch that marks the release unhealthy before it
        # names the gate, and never the other way round.
        body = gate[at:gate.index("FAIL_REASON=", at)]
        assert "HEALTHY=0" in body, (
            f"the smoke gate's {branch!r} path is a skip, not a rollback: a release "
            "nobody signed in against would ship")


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


#: Gate 0's delimiters. A launcher only runs a deploy script that carries them,
#: so every stand-in for the checkout's script has to carry them too — see
#: `test_the_launcher_refuses_a_checkout_that_lost_gate_0` for the other half.
GATE_0 = "# runner-identity:start\n# runner-identity:end\n"


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
    target.write_text(GATE_0 + '#!/usr/bin/env bash\necho "v1 ran with $# argument(s)"\n',
                      encoding="utf-8")

    bins = tmp_path / "bin"
    bins.mkdir()
    launcher = _render_launcher(bins, repo, "scangrade-deploy")

    first = subprocess.run([BASH, str(launcher)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "v1 ran with 0 argument(s)"

    target.write_text(GATE_0 + '#!/usr/bin/env bash\necho "v2 ran with $# argument(s)"\n',
                      encoding="utf-8")
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
def test_the_launcher_refuses_a_checkout_that_lost_gate_0(tmp_path):
    """A deployed commit cannot quietly retire the gate that keeps copies out.

    Gate 0 is what refuses a runner that is not the checkout's. A checkout
    without it turns every future release into a deploy nobody can audit — and
    that state is reachable without anyone deciding to: roll back past the commit
    that added the block, or delete it while debugging. The launcher is the last
    place that can notice, and it refuses rather than run it.
    """
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    target = repo / "deploy" / "scangrade-deploy.sh"
    target.write_text('#!/usr/bin/env bash\necho "ran without Gate 0"\n', encoding="utf-8")

    bins = tmp_path / "bin"
    bins.mkdir()
    launcher = _render_launcher(bins, repo, "scangrade-deploy")

    run = subprocess.run([BASH, str(launcher)], capture_output=True, text=True)
    assert run.returncode == 15, run.stdout + run.stderr
    assert "does not carry Gate 0" in run.stderr
    assert "runner-identity" in run.stderr, (
        "the refusal does not name what is missing, so it cannot be acted on")
    assert "ran without Gate 0" not in run.stdout, "the launcher ran it anyway"

    # Half a block is not a block: a lone `start` is what a half-edited file
    # looks like, and it must not read as Gate 0 being present.
    target.write_text('# runner-identity:start\n#!/usr/bin/env bash\necho "half"\n',
                      encoding="utf-8")
    half = subprocess.run([BASH, str(launcher)], capture_output=True, text=True)
    assert half.returncode == 15
    assert "half" not in half.stdout

    # And the snapshot command is unaffected: it execs a different file, which is
    # not a deploy script and carries no gates.
    (repo / "deploy" / "scangrade-db-snapshot.sh").write_text(
        '#!/usr/bin/env bash\necho "snapshot ok"\n', encoding="utf-8")
    snapshot = _render_launcher(bins, repo, "scangrade-db-snapshot")
    ok = subprocess.run([BASH, str(snapshot)], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == "snapshot ok", ok.stderr


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


# ── a box that cannot check a release does not deploy one ────────
#
# Every gate below the preflight can be *skipped*, and each skip used to be a
# sentence in the journal and nothing else: the readability gate without pytest,
# the smoke gate without its conf, the claims and performance gates without a
# roster. A box in that state deploys every commit while checking almost none of
# them — the site stays green and "the gates ran" quietly stops being true. So the
# armament is judged once, before anything is fetched, and a missing check refuses
# the run outright. These tests hold the two ends of that: it runs *first*, and it
# is a refusal to deploy rather than a rollback of a commit.

PREFLIGHT_START = "armament_preflight() {"
PREFLIGHT_END = "# Armed. The record of a previous refusal"


def _preflight_block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    return script[script.index(PREFLIGHT_START):script.index(PREFLIGHT_END)]


def _preflight_harness(repo: Path, state_dir: Path) -> str:
    """The preflight and its call, lifted out with the four things it needs.

    `--check` is not run here: `$REPO/deploy/arm-auto-deploy.sh` is a stub, so the
    question under test is what the deploy *does* with each answer, which is the
    half the checker's own tests cannot reach.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    block = _preflight_block()
    tail = script[script.index(PREFLIGHT_END):script.index('\n[ -d "$REPO/.git" ]')]
    return (
        "set -uo pipefail\n"
        f'REPO="{repo}"\n'
        f'STATE_DIR="{state_dir}"\n'
        f'UNARMED_FILE="{state_dir}/unarmed"\n'
        'log() { echo "$*"; }\n'
        + block + tail + "\necho REACHED\n"
    )


def _stub_checker(repo: Path, code: int, text: str) -> None:
    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "arm-auto-deploy.sh").write_text(
        f'#!/usr/bin/env bash\necho "{text}"\nexit {code}\n', encoding="utf-8")


def test_the_armament_is_judged_before_anything_is_fetched():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    judged = script.index("if ! armament_preflight; then")
    for later in ("fetch --quiet origin", "merge --ff-only", "CONSTRUCT_OUT="):
        assert judged < script.index(later), (
            f"the armament is judged after {later!r}, so a release is already in "
            "flight by the time the box says it cannot check one")
    # And the pause check stays in front of it: a frozen box deploys nothing, and
    # a fault in a file nobody is running is not worth a journal line every tick.
    assert script.index('if [ -e "$PAUSE_FILE" ]') < judged


def test_a_refusal_to_deploy_is_not_recorded_as_a_commit():
    """The two records answer different questions and must not be mixed.

    A quarantine is a fact about a *commit* — this one was refused, do not pull
    it again. A box-side refusal has no commit to name: nothing was fetched, so
    quarantining it would freeze the release nobody has looked at yet.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    block = _preflight_block()
    assert "quarantine_write" not in block, (
        "the preflight quarantines a commit it never looked at")
    assert 'exit 15' in block and 'exit 16' not in block, (
        "the refusal has no exit code of its own, so the journal cannot tell it "
        "apart from a gate that judged a release")


def test_the_armed_answer_erases_a_stale_record():
    """A record must not outlive the state it describes.

    The box can be armed again without a new commit — the installer runs, the
    roster arrives — and a card that kept saying "every release is refused" would
    be the page lying in the direction that stops people deploying.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    after = script[script.index(PREFLIGHT_END):]
    assert 'rm -f "$UNARMED_FILE"' in after[:400], (
        "the record of a refusal survives the box being armed again")


def _gate0_survival_harness(repo: Path) -> str:
    """The survival check and its refusal, lifted out with what it needs.

    `as_owner` and `quarantine_write` are stubbed, so the question under test is
    the one this block exists to answer: does the checkout still carry the check
    that refuses a runner which is not the checkout's? Always against a `repo`
    under `tmp_path`, never the real checkout — the refusal path runs `git reset
    --hard`, and a guard that could touch the working tree is a guard nobody runs.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    start = script.index("# ── Gate 0 survives the release")
    block = script[start:script.index("\nfi\n", start) + len("\nfi\n")]
    return (
        "set -uo pipefail\n"
        f'REPO="{repo}"\n'
        "BEFORE=deadbeef\n"
        'FAIL_REASON=""\n'
        'log() { echo "$*"; }\n'
        'quarantine_write() { echo QUARANTINE; }\n'
        'as_owner() { "$@"; }\n'
        + block + "echo REACHED\n"
    )


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the check")
def test_a_release_that_removes_gate_0_is_refused_and_recorded(tmp_path):
    """The one path by which "lacks Gate 0" is reachable on purpose.

    Gate 0 refuses a runner that is not the checkout's, and the launcher refuses a
    checkout that lost the block — but a release *removing* the block is the case
    this run is the last one that could notice: the file that would notice next is
    no longer in the checkout. So the release is refused instead of deployed, and
    recorded, because otherwise the next tick pulls it straight back.
    """
    real = DEPLOY_SH.read_text(encoding="utf-8")

    def run_case(text: str):
        repo = tmp_path / "repo"
        (repo / "deploy").mkdir(parents=True, exist_ok=True)
        # Bytes, so bash sees the same LF the checkout has and not a Windows
        # newline it would read as part of the last word on every line.
        (repo / "deploy" / "scangrade-deploy.sh").write_bytes(text.encode("utf-8"))
        return subprocess.run([BASH, "-c", _gate0_survival_harness(repo)],
                              capture_output=True, text=True)

    kept = run_case(real)
    assert kept.returncode == 0 and "REACHED" in kept.stdout, kept.stdout + kept.stderr
    assert "QUARANTINE" not in kept.stdout, (
        "a checkout that carries Gate 0 is refused, so every release would be told "
        "its own runner is missing the check")

    stripped = real.split(IDENTITY_START, 1)[0] + real.split(IDENTITY_END, 1)[1]
    assert IDENTITY_START not in stripped
    refused = run_case(stripped)
    assert refused.returncode == 16, refused.stdout + refused.stderr
    assert "REACHED" not in refused.stdout, "it refused the release and deployed anyway"
    assert "QUARANTINE" in refused.stdout, (
        "the refusal is not recorded, so the next tick re-pulls the same commit")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the preflight")
def test_an_unarmed_box_is_refused_and_the_record_says_why(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_checker(repo, 1, "NOT ARMED — the runner is a copy")
    state = tmp_path / "state"

    run = subprocess.run([BASH, "-c", _preflight_harness(repo, state)],
                         capture_output=True, text=True)
    assert run.returncode == 15, run.stdout + run.stderr
    assert "REACHED" not in run.stdout, "it refused and carried on anyway"
    assert "UNARMED" in run.stdout
    assert "arm-auto-deploy.sh" in run.stdout, (
        "the refusal does not name the command that arms the box")
    record = state / "unarmed"
    assert record.exists(), "nothing is left for the status page to read"
    body = record.read_text(encoding="utf-8")
    assert "NOT ARMED — the runner is a copy" in body, (
        "the record does not carry the checker's own report, so the page has "
        "nothing to show but a timestamp")
    assert body.splitlines()[0].startswith("20"), (
        "the first line is the time it refused, which is what the card ages")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the preflight")
def test_an_armed_box_carries_on_and_clears_the_record(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_checker(repo, 0, "ARMED — every gate has what it needs.")
    state = tmp_path / "state"
    state.mkdir()
    (state / "unarmed").write_text("2026-09-20T00:00:00+00:00\nold refusal\n", encoding="utf-8")

    run = subprocess.run([BASH, "-c", _preflight_harness(repo, state)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "REACHED" in run.stdout
    assert not (state / "unarmed").exists(), (
        "the box is armed and the page still reports every release refused")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the preflight")
def test_a_checker_that_is_not_there_is_a_refusal_not_a_crash(tmp_path):
    """The checker arrives with the installer, so an older box has none.

    "We could not tell" is not "it is fine": the preflight fails closed, with the
    same exit code and the same record, because a box that cannot say whether it
    is armed is a box that has not armed.
    """
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    state = tmp_path / "state"

    run = subprocess.run([BASH, "-c", _preflight_harness(repo, state)],
                         capture_output=True, text=True)
    assert run.returncode == 15, run.stdout + run.stderr
    assert "armament checker is missing" in run.stdout, run.stdout
    assert (state / "unarmed").exists()


# ── a refused release is quarantined, not retried forever ────────
#
# Every gate rejects a release by resetting the *checkout*. `origin/main` does not
# move, so without a record of which commit was refused the next tick fetches it
# again, pulls it, and walks into the same gate — rejected, rolled back, re-pulled,
# every two minutes, reloading gunicorn and the Celery worker on each lap, for as
# long as the bad commit sits on the branch. The previous release serves the whole
# time, so nothing about the loop is visible on a page.
#
# The quarantine is that record. These tests hold both halves: that it is written
# where a gate refuses a release, and that the next tick honours it instead of
# trying again.

QUARANTINE_START = "# quarantine-logic:start"
QUARANTINE_END = "# quarantine-logic:end"

#: A *call* — `quarantine_write() {` is the definition and must not match, which
#: is why the line has to end there: the call inside the shared failure block sits
#: at column 0, and requiring an indent found three of the four call sites.
QUARANTINE_CALL = re.compile(r"^[ \t]*quarantine_write$", re.M)

SHA_A = "a" * 40
SHA_B = "b" * 40


def _quarantine_block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    return script.split(QUARANTINE_START, 1)[1].split(QUARANTINE_END, 1)[0]


def test_the_quarantine_lives_between_its_delimiters():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert QUARANTINE_START in script and QUARANTINE_END in script, (
        "the quarantine block's delimiters also let it be tested on its own, the "
        "way Gate 0's do; keep them")
    for fn in ("quarantine_write() {", "quarantine_gate() {",
               "quarantine_honour_release() {"):
        assert fn in _quarantine_block(), f"{fn} left the delimited block"


def test_a_gate_that_refuses_a_release_records_it():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    calls = QUARANTINE_CALL.findall(script)
    assert len(calls) == 5, (
        f"{len(calls)} quarantine_write call(s). Every gate that rolls a release "
        "back has to record it — the check that Gate 0 survives the release, "
        "compileall, app construction, the theme gate and the shared post-reload "
        "verification — or that gate goes on re-pulling the same commit every two "
        "minutes")


def test_every_refusal_names_its_gate_and_records_the_commit():
    """The count above can be satisfied by a sixth call in the wrong place.

    So each refusal is checked by name: the reason is what the operator reads on
    the status page, and the record has to precede the rollback, because the
    rollback is what hides the commit from the next tick's `BEFORE`.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    for reason in (
        "Gate 0 (the release removed the runner-identity block)",
        "python compileall (exit 8)",
        "app did not construct (exit 9)",
        "runner not armed (the app refused to be deployed by it)",
        "theme gate (exit $THEME_RC)",
    ):
        assert reason in script, f"no FAIL_REASON for {reason!r} — the runner changed shape"
        at = script.index(reason)
        window = script[at:script.index("reset --hard", at)]
        assert QUARANTINE_CALL.search(window), (
            f"the refusal {reason!r} names the gate but never quarantines the commit, "
            "so the next tick pulls it straight back")


def test_the_next_tick_consults_the_quarantine_and_can_be_released():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert re.search(r"^if ! quarantine_gate; then$", script, re.M), (
        "the runner never consults the quarantine, so it would retry the refused "
        "commit on the next tick — the loop this exists to stop")
    assert re.search(r"^quarantine_honour_release$", script, re.M), (
        "the one-shot release file is never read, so there is no way to retry a "
        "release a gate refused for a box-side reason")
    assert "QUARANTINED" in script, "nothing ever says out loud that it is skipping"


def test_nothing_is_quarantined_before_the_release_has_been_merged():
    """A transient failure must never freeze a good commit.

    The fetch and a failed snapshot are properties of the box — nothing has been
    merged when they happen — and the script already retries them next tick. So
    no quarantine may be written anywhere before the merge, and the one failure
    after it that is still the network's fault (`pip install`) must not quarantine
    either.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    merge = script.index("merge --ff-only")
    early = [m.start() for m in QUARANTINE_CALL.finditer(script) if m.start() < merge]
    assert not early, (
        "a quarantine_write call runs before the release is merged, so a failed "
        "fetch or snapshot would freeze a commit no gate has judged")

    pip = script.index("requirements.txt changed")
    pip = script[pip:pip + script[pip:].index("exit 7")]
    assert "quarantine" not in pip, (
        "a failed pip install quarantines the release. That is usually the "
        "network, and retrying it is the correct behaviour")


def test_the_record_can_only_ever_name_the_candidate_release():
    """The write reads `$AFTER_FULL` rather than taking an argument.

    It is the script's only release under judgement, and reading it removes any
    chance of quarantining a commit other than the one a gate actually refused —
    as well as the positional parameters `test_deploy_script_takes_no_arguments`
    forbids.
    """
    block = _quarantine_block()
    assert re.search(r'printf .*"\$AFTER_FULL"', block), (
        "the recorded sha is not the candidate release's — "
        "the quarantine could name a commit no gate looked at")
    for positional in ("$1", "${1"):
        assert positional not in block, (
            "the quarantine block takes a positional parameter; this script takes "
            "none at all")


# ── and it behaves ───────────────────────────────────────────────


def _quarantine_harness(tmp_path: Path) -> str:
    """The real block, lifted out and given the paths it reads.

    `requests/` is created here because the block reads the request file's parent
    as freely as the block itself does: a harness that left it out failed on
    `set -u` rather than on anything the quarantine does.
    """
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    requests = state / "requests"
    requests.mkdir(exist_ok=True)
    return (
        "set -uo pipefail\n"
        f'STATE_DIR="{state}"\n'
        f'QUARANTINE_FILE="{state}/quarantined"\n'
        f'RELEASE_FILE="{tmp_path}/scangrade-deploy.release"\n'
        f'REQUEST_DIR="{requests}"\n'
        f'RELEASE_REQUEST="{requests}/release"\n'
        'BRANCH="main"\n'
        'AFTER="abcdef1"\n'
        'log() { echo "$*"; }\n'
        + _quarantine_block()
    )


def _run_quarantine(tmp_path: Path, body: str, *, after_full: str = SHA_A):
    program = (_quarantine_harness(tmp_path)
               + f'\nAFTER_FULL="{after_full}"\nFAIL_REASON="theme gate (exit 13)"\n'
               + body)
    return subprocess.run([BASH, "-c", program], capture_output=True, text=True)


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the quarantine block")
def test_a_commit_nobody_refused_deploys(tmp_path):
    run = _run_quarantine(tmp_path, 'quarantine_gate; echo "rc=$?"')
    assert run.returncode == 0, run.stderr
    assert "rc=0" in run.stdout, run.stdout
    assert "QUARANTINED" not in run.stdout


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the quarantine block")
def test_a_refused_commit_is_skipped_and_the_reason_is_written_down(tmp_path):
    run = _run_quarantine(tmp_path, 'quarantine_write\nquarantine_gate; echo "rc=$?"')
    assert "QUARANTINED" in run.stdout, run.stdout
    assert "rc=1" in run.stdout, (
        "the tick after a refusal went ahead anyway — that is the re-pull loop")
    assert "scangrade-deploy.release" in run.stdout, (
        "the skip says nothing about how to retry, so the only escape reads as a "
        "mystery")

    record = (tmp_path / "state" / "quarantined").read_text(encoding="utf-8").splitlines()
    assert record[0] == SHA_A, (
        "the record must hold the full sha, not a 7-character prefix — a prefix is "
        "a search key, not an identity")
    assert record[1], "the record must say when it was refused"
    assert "theme gate" in record[2], (
        "the record must name the gate that refused it, so 'why did nothing "
        "deploy' is answered by one cat")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the quarantine block")
def test_a_fix_lifts_the_quarantine_by_itself(tmp_path):
    body = (
        'quarantine_write\n'
        f'AFTER_FULL="{SHA_B}"\n'
        'quarantine_gate; echo "rc=$?"'
    )
    run = _run_quarantine(tmp_path, body)
    assert "quarantine lifted" in run.stdout, run.stdout
    assert "rc=0" in run.stdout, run.stdout
    assert not (tmp_path / "state" / "quarantined").exists(), (
        "a moved branch must clear the record, or a commit somebody has since "
        "fixed stays frozen")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the quarantine block")
def test_the_release_file_buys_one_attempt_not_a_standing_override(tmp_path):
    """The escape hatch has to be single-use.

    A file that stayed put would quietly pin a known-bad commit in place: every
    tick would try it, every tick would roll it back, and the quarantine would
    have bought nothing.
    """
    release = tmp_path / "scangrade-deploy.release"
    release.write_text("", encoding="utf-8")

    # Tick 1 — a refusal was recorded, then somebody released it, so it is tried.
    first = _run_quarantine(
        tmp_path,
        'quarantine_write\nquarantine_honour_release\nquarantine_gate; echo "rc=$?"')
    assert "rc=0" in first.stdout, first.stdout
    assert not release.exists(), "the release file was not consumed by the attempt"
    assert "explicit release requested" in first.stdout, (
        "clearing a quarantine silently loses the fact that somebody asked for it")

    # Tick 2 — refused again, so it is quarantined again.
    second = _run_quarantine(tmp_path, 'quarantine_write\nquarantine_gate; echo "rc=$?"')
    assert "rc=1" in second.stdout, second.stdout

    # Tick 3 — with the file gone, the next tick skips rather than retrying.
    third = _run_quarantine(tmp_path, 'quarantine_gate; echo "rc=$?"')
    assert "rc=1" in third.stdout, third.stdout


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the quarantine block")
def test_the_app_can_buy_the_same_single_attempt(tmp_path):
    """The page's request and the operator's file mean exactly the same thing.

    The app runs as the service user and cannot write /etc, so a request file in
    the state directory is its only channel to a script that runs as root. It buys
    one attempt and is consumed by it, the same as the root-owned file — otherwise
    the button would be a standing override for a commit a gate keeps refusing.
    """
    request = tmp_path / "state" / "requests" / "release"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text("… anything at all …", encoding="utf-8")

    first = _run_quarantine(
        tmp_path,
        'quarantine_write\nquarantine_honour_release\nquarantine_gate; echo "rc=$?"')
    assert "rc=0" in first.stdout, first.stdout
    assert not request.exists(), (
        "the request survived the attempt, so the next tick would release another "
        "commit nobody asked about")
    assert "explicit release requested" in first.stdout, first.stdout

    # Refused again: quarantined again, and the next tick skips. One click, one try.
    second = _run_quarantine(tmp_path, 'quarantine_write\nquarantine_gate; echo "rc=$?"')
    assert "rc=1" in second.stdout, second.stdout


# ── a successful release reheals the installed launcher ───────────
#
# Gate 0 refuses an installed *copy* that has drifted, which is right — and it
# also leaves the box unable to deploy until somebody runs the installer as root,
# the manual step this automation exists to remove. So a release that gets all the
# way to the end re-renders the launcher from the checkout it just deployed, and
# from that release on what the timer runs *is* the checkout's launcher.
#
# Three properties make that safe rather than a new way to break a box, and each
# is checked twice: as the shape of the code, and by running it.
#
#   * it never installs a launcher it has not parsed (a half-written launcher is a
#     box that cannot deploy *at all*, which is strictly worse than a stale one);
#   * the replacement is atomic (staged beside the target, so `mv` is a rename);
#   * it is skipped when the bytes already match (Gate 0 compares *content*, so a
#     timestamp that moved while the content did not would be a signal that lies).
#
# The last class is what the others exist for: a stale copy refuses to run, the
# refresh replaces it, and the launcher that lands reaches the checkout's own
# script and satisfies the real Gate 0.

REFRESH_START = "# refresh-launcher-logic:start"
REFRESH_END = "# refresh-launcher-logic:end"


def _refresh_block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    return script.split(REFRESH_START, 1)[1].split(REFRESH_END, 1)[0]


def _refresh_code() -> str:
    """The block with its comments removed, which is what the guards below read.

    A guard that reads prose fires on a sentence: this block's own header explains
    that a *drifted* copy still refuses with `exit 14`, and "exit 1" is a substring
    of that — so the forbidden-words check lit up on a comment and invited a
    relaxed guard instead of a correct one. Comments explain the code; the guards
    judge the code.
    """
    return "\n".join(ln for ln in _refresh_block().splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))


def _stale_installed_runner() -> bytes:
    """The shape production was actually in: a *copy* of the runner taken at some
    past commit, which stops matching the checkout the moment either one changes."""
    return DEPLOY_SH.read_bytes() + b"\n# the logic of an older commit\n"


def _posix(path: Path) -> str:
    """A path for the shell.

    Git Bash understands ``/c/...``, and handing it a Windows path would put
    backslashes through sed's *replacement*, where GNU sed reads ``\\U`` as
    "uppercase until \\E" — a rendered launcher that legal-looking would then not
    match what the checkout says. The shell and the assertions compare the same
    string by using this on both sides.
    """
    text = str(path)
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


def _refresh_harness(tmp_path: Path, repo: str) -> str:
    """The real refresh block, with the globals it reads."""
    bins = _posix(tmp_path / "installed")
    (tmp_path / "installed").mkdir(exist_ok=True)
    return (
        "set -uo pipefail\n"
        f'REPO="{repo}"\n'
        f'INSTALLED_BIN_DIR="{bins}"\n'
        f'INSTALLED_RUNNER="{bins}/scangrade-deploy"\n'
        f'INSTALLED_SNAPSHOT="{bins}/scangrade-db-snapshot"\n'
        'log() { echo "$*"; }\n'
        + _refresh_block()
    )


def _run_refresh(tmp_path: Path, repo: str, body: str = "refresh_installed_launchers"):
    program = _refresh_harness(tmp_path, repo) + "\n" + body + "\n"
    return subprocess.run([BASH, "-c", program], capture_output=True, text=True)


def _fake_checkout(tmp_path: Path, name: str = "repo") -> Path:
    """A checkout holding the two files the refresh and Gate 0 read."""
    repo = tmp_path / name
    (repo / "deploy").mkdir(parents=True, exist_ok=True)
    (repo / "deploy" / "entrypoint.sh").write_bytes(ENTRYPOINT_SH.read_bytes())
    (repo / "deploy" / "scangrade-deploy.sh").write_bytes(DEPLOY_SH.read_bytes())
    return repo


def _rendered_launcher(repo: str, template: Path | None = None) -> bytes:
    """What the refresh must produce: a template with the checkout rendered into it.

    `template` defaults to the repository's `deploy/entrypoint.sh`; the stale-launcher
    case renders the *checkout's* copy, which is what lets the two be told apart.
    """
    return (template or ENTRYPOINT_SH).read_bytes().replace(b"@REPO@", repo.encode())


def _installed_runner(tmp_path: Path) -> Path:
    return tmp_path / "installed" / "scangrade-deploy"


def _install_stale_copy(tmp_path: Path) -> Path:
    path = _installed_runner(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_stale_installed_runner())
    return path


def test_the_refresh_block_lives_between_its_delimiters():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert REFRESH_START in script and REFRESH_END in script, (
        "the delimiters are what let this block be run on its own, the way the "
        "quarantine and Gate 0 are; keep them"
    )
    block = _refresh_code()
    assert "refresh_launcher() {" in block
    assert "refresh_installed_launchers() {" in block
    # The helper takes its target through a global, on purpose: `$1` anywhere in
    # this script reads root's own argv, which `test_deploy_script_takes_no_
    # arguments` forbids. Keep the two in step.
    assert "LAUNCHER_TARGET" in block


def test_the_refresh_parses_before_it_installs():
    """Order, not presence: a `bash -n` after the move would check the new file on
    a box the move had already broken."""
    block = _refresh_code()
    move = block.index('mv -f "$staged" "$target"')
    assert block.index('bash -n "$staged"') < move, (
        "the launcher is installed before it is parsed")
    assert block.index("grep -q '@REPO@' \"$staged\"") < move, (
        "an unrendered launcher could be installed, so it would exec "
        "@REPO@/deploy/scangrade-deploy.sh forever")


def test_the_refresh_stages_beside_the_target_so_the_install_is_atomic():
    block = _refresh_code()
    assert 'staged="$(dirname "$target")' in block, (
        "staging anywhere but the target's own directory makes the install a copy "
        "across filesystems, which a tick can observe half-done"
    )


def test_the_refresh_does_nothing_when_the_bytes_already_match():
    block = _refresh_code()
    assert block.index('cmp -s "$staged" "$target"') < \
        block.index('mv -f "$staged" "$target"'), (
        "an unconditional write moves the mtime on every release while the content "
        "stays put — and Gate 0 compares content, so that signal would lie"
    )


def test_the_refresh_cannot_take_a_working_release_down_with_it():
    """The app is verified and serving; a bookkeeping failure must not undo that,
    nor look like a refused release — which would quarantine a good commit."""
    block = _refresh_code()
    for forbidden in ("HEALTHY=0", "FAIL_REASON=", "quarantine_write", "exit 1"):
        assert forbidden not in block, (
            f"the refresh block contains {forbidden!r}: a launcher refresh would "
            "then roll back a release that is working"
        )


def test_the_refresh_rewrites_exactly_what_the_installer_installs():
    """The relation that keeps the heal pointed at the right files.

    The installer decides the two paths; the runner has to refresh exactly those,
    or the box heals a path nothing runs.
    """
    installer = INSTALL_SH.read_text(encoding="utf-8")
    installed = re.search(r'^DEPLOY_BIN="([^"]+)"', installer, re.M).group(1)
    snapshot = re.search(r'^SNAPSHOT_BIN="([^"]+)"', installer, re.M).group(1)
    runner = DEPLOY_SH.read_text(encoding="utf-8")
    # Compared as POSIX strings: the paths describe a Linux box, and a test on
    # Windows must not fail on the separator the local `pathlib` renders.
    assert re.search(
        rf'^INSTALLED_BIN_DIR="{re.escape(installed.rsplit("/", 1)[0])}"', runner, re.M), (
        "the runner refreshes a different directory than the installer installs into")
    assert re.search(
        rf'^INSTALLED_RUNNER="\$INSTALLED_BIN_DIR/{re.escape(installed.rsplit("/", 1)[1])}"',
        runner, re.M), "the runner refreshes a different deploy path than the installer installs"
    assert re.search(
        rf'^INSTALLED_SNAPSHOT="\$INSTALLED_BIN_DIR/{re.escape(snapshot.rsplit("/", 1)[1])}"',
        runner, re.M), "the runner refreshes a different snapshot path than the installer installs"


def test_the_refresh_runs_on_the_success_path_only():
    """A refresh on the way *into* a release installs a launcher for a commit that
    then gets rolled back."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    healthy = script.index('if [ "$HEALTHY" = "1" ]; then')
    assert healthy < script.index("refresh_installed_launchers\n", healthy), (
        "the refresh runs before the release is verified")
    assert script.count("refresh_installed_launchers\n") == 1, (
        "refresh_installed_launchers is called from somewhere other than the "
        "success path")


def test_the_rollback_path_does_not_refresh():
    """A refused release must not install a launcher on its way out: the checkout
    is about to move back, so the launcher would point at logic that no longer
    runs."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    tail = script[script.index("did not pass verification — rolling back"):]
    assert "refresh_installed_launchers" not in tail, "the rollback refreshes the launcher"


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_a_drifted_install_is_replaced_by_the_checkout_launcher(tmp_path):
    repo = _fake_checkout(tmp_path)
    installed = _install_stale_copy(tmp_path)

    run = _run_refresh(tmp_path, _posix(repo))
    assert run.returncode == 0, run.stderr
    assert "launcher refreshed" in run.stdout, run.stdout
    assert installed.read_bytes() == _rendered_launcher(_posix(repo)), (
        "the installed file is not what the checkout renders")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_the_second_release_changes_nothing(tmp_path):
    """Idempotence, measured: the mtime is the tell. Gate 0 compares content, so a
    file rewritten on every release reports "the launcher moved" when it did not."""
    repo = _fake_checkout(tmp_path)
    installed = _install_stale_copy(tmp_path)

    assert _run_refresh(tmp_path, _posix(repo)).returncode == 0
    before = installed.stat().st_mtime_ns
    again = _run_refresh(tmp_path, _posix(repo))
    assert again.returncode == 0
    assert "launcher refreshed" not in again.stdout, (
        "an identical file was rewritten, so the mtime now moves on every release "
        "while the content does not")
    assert installed.stat().st_mtime_ns == before


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_a_missing_snapshot_launcher_is_created(tmp_path):
    """The snapshot command is the one that was silently broken by being a copy —
    it derived the checkout from its own location — so a box missing it heals here
    too."""
    repo = _fake_checkout(tmp_path)
    run = _run_refresh(tmp_path, _posix(repo))
    assert run.returncode == 0, run.stderr
    snapshot = tmp_path / "installed" / "scangrade-db-snapshot"
    assert snapshot.exists(), "a missing snapshot launcher was not restored"
    assert snapshot.read_bytes() == _rendered_launcher(_posix(repo))


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_a_launcher_that_does_not_parse_never_replaces_a_working_one(tmp_path):
    """The failure that would be worse than staleness: a launcher that cannot run
    means nothing can deploy at all."""
    repo = _fake_checkout(tmp_path)
    (repo / "deploy" / "entrypoint.sh").write_text(
        '#!/bin/sh\nif [ -z "$REPO" ]; then\n', encoding="utf-8")
    installed = _install_stale_copy(tmp_path)
    good = installed.read_bytes()

    run = _run_refresh(tmp_path, _posix(repo))
    assert run.returncode == 0, "a broken render must not fail the release"
    assert "does not parse" in run.stdout, run.stdout
    assert installed.read_bytes() == good, "a broken render was installed anyway"


#: `|` is sed's delimiter in the render, so a checkout path holding one cannot be
#: rendered — the branch that has to leave the installed file exactly as it found
#: it. The trigger needs a `|` in a filename, which Windows does not allow, so this
#: one runs where deploys actually happen.
@pytest.mark.skipif(os.name == "nt", reason="a filename cannot hold `|` on Windows")
@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_a_render_that_fails_outright_leaves_everything_alone(tmp_path):
    repo = _fake_checkout(tmp_path, name="re|po")
    installed = _install_stale_copy(tmp_path)
    good = installed.read_bytes()

    run = _run_refresh(tmp_path, _posix(repo))
    assert run.returncode == 0, run.stderr
    assert "could not render" in run.stdout, run.stdout
    assert installed.read_bytes() == good, (
        "a failed render left the installed launcher changed — that is the one "
        "outcome worse than staleness")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh block")
def test_an_install_that_cannot_happen_is_reported_not_fatal(tmp_path):
    """The `mv` is the step that actually replaces the file, so it is made to fail
    here rather than relying on a filesystem's habits about overwriting."""
    repo = _fake_checkout(tmp_path)
    installed = _install_stale_copy(tmp_path)
    good = installed.read_bytes()

    run = _run_refresh(tmp_path, _posix(repo),
                       body='mv() { return 1; }\nrefresh_installed_launchers')
    assert run.returncode == 0, "a failed install must not fail the release"
    assert "could not replace" in run.stdout, run.stdout
    assert installed.read_bytes() == good, "the installed launcher changed anyway"
    leftovers = [p.name for p in (tmp_path / "installed").iterdir()
                 if p.name.startswith(".")]
    assert not leftovers, f"the staged render was left behind: {leftovers}"


def _identity_block() -> str:
    """Gate 0's block **including its delimiters**.

    The delimiters are part of the contract now, not decoration: the launcher
    refuses to run a checkout whose deploy script does not carry them, and the
    deploy's own self-preservation check looks for the same two lines. A helper
    that stripped them would build a stand-in checkout that no real one could be,
    and the refusal would then look like the launcher being wrong.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    # The trailing newline matters: the end marker has to be a line of its own, or
    # the next statement the fixtures append lands on the same line as the marker
    # and a line-anchored check reads the block as absent.
    return (IDENTITY_START
            + script.split(IDENTITY_START, 1)[1].split(IDENTITY_END, 1)[0]
            + IDENTITY_END + "\n")


def _stale_launcher(tmp_path: Path, repo: Path) -> Path:
    """A launcher rendered from an `entrypoint.sh` that has since changed.

    This is the state the deploy-status page calls `launcher_stale`, and the one this
    step can genuinely heal: the launcher execs the checkout's script, so Gate 0
    passes it and the release runs to the end, where the re-render happens.
    """
    path = tmp_path / "installed" / "scangrade-deploy"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_rendered_launcher(_posix(repo)))
    # As bytes on purpose: `write_text` on Windows rewrites every line ending in the
    # template, and the render is compared byte-for-byte, so a text-mode fixture would
    # compare two different files and then call the refresh wrong (the line-ending
    # lesson this repository keeps re-learning through a different door).
    (repo / "deploy" / "entrypoint.sh").write_bytes(
        ENTRYPOINT_SH.read_bytes() + b"\n# a fix landed after the install\n")
    return path


def _gate0_in_the_checkout(repo: Path) -> None:
    """The checkout's runner, carrying Gate 0's real block.

    Stubbing it with an echo would prove the launcher reaches *a* file; this proves
    the arrangement gets *through* Gate 0 — which is the refusal a stale launcher
    must not trip, because tripping it is what makes the refresh unreachable.
    """
    (repo / "deploy" / "scangrade-deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f'REPO="{_posix(repo)}"\n'
        'log() { echo "$*"; }\n'
        + _identity_block()
        + 'echo "REACHED-END $0"\necho "ARGS $#"\n',
        encoding="utf-8")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run the refresh and Gate 0")
def test_a_stale_launcher_is_re_rendered_by_the_next_release(tmp_path):
    """The reachable half of the claim, end to end and on the real files.

    Before: the installed launcher was rendered from an older `entrypoint.sh`, and
    running it still reaches the checkout's script — which is why the staleness has
    no symptom and no gate stops it. The release gets all the way to the end, the
    launcher is re-rendered, and what is installed is now what this checkout renders:
    no installer, no root, no shell edit.
    """
    repo = _fake_checkout(tmp_path)
    installed = _stale_launcher(tmp_path, repo)
    changed = repo / "deploy" / "entrypoint.sh"
    stale = installed.read_bytes()
    assert stale != _rendered_launcher(_posix(repo), changed), "the fixture is not stale"

    _gate0_in_the_checkout(repo)
    before = subprocess.run([BASH, str(installed)], capture_output=True, text=True)
    assert before.returncode == 0, before.stderr
    assert "REFUSING" not in before.stdout, (
        "a stale launcher tripped the copy refusal, so the refresh would never be "
        "reached:\n" + before.stdout)
    assert f"REACHED-END {_posix(repo)}/deploy/scangrade-deploy.sh" in before.stdout, (
        "the launcher did not reach the checkout's runner:\n" + before.stdout)
    assert "ARGS 0" in before.stdout, (
        "arguments got through the launcher, which is the one thing it must never "
        "forward")

    refreshed = _run_refresh(tmp_path, _posix(repo))
    assert refreshed.returncode == 0, refreshed.stderr
    assert "launcher refreshed" in refreshed.stdout, refreshed.stdout
    assert installed.read_bytes() == _rendered_launcher(_posix(repo), changed), (
        "the installed launcher is still the older rendering")
    assert b"a fix landed after the install" in installed.read_bytes(), (
        "the new template's content is not what got installed")


@pytest.mark.skipif(BASH is None, reason="needs a bash to run Gate 0")
def test_a_drifted_copy_is_refused_and_no_release_can_reach_the_refresh(tmp_path):
    """The half this step cannot heal, measured rather than hoped.

    A copy that has drifted refuses with exit 14 *before* any gate can pass, and the
    refresh is the last step of a release that passed — so from that state the heal
    is unreachable, and the one root command (`install-auto-deploy.sh`) is the only
    way out. Nothing inside a file that is not the checkout's can apply the
    checkout's logic; that is the step a file cannot take for itself, and it is why
    `docs/AUTO_DEPLOY.md` names this case instead of claiming the arrangement always
    heals itself.
    """
    repo = _fake_checkout(tmp_path)
    installed = _install_stale_copy(tmp_path)

    refused = subprocess.run([BASH, "-c", _identity_harness(repo), str(installed)],
                             capture_output=True, text=True)
    assert refused.returncode == 14, (
        "a drifted copy did not refuse, so this proof is not measuring the state it "
        f"claims to (rc={refused.returncode})\n{refused.stdout}")
    assert "REFUSING" in refused.stdout

    # The refusal is an `exit`, so the run stops there and the success path — with the
    # refresh on it — is never reached. Pinned structurally below, because a
    # behavioural test cannot show a call that never happens.
    script = DEPLOY_SH.read_text(encoding="utf-8")
    call = script.index("refresh_installed_launchers\n")
    assert "exit 14" in script[:call], (
        "the refusal no longer precedes the refresh, so the claim that it cannot be "
        "reached needs re-deriving")


# ── the app can reach its own state directory ─────────────────────
#
# `/var/lib/scangrade-deploy` is the one directory both sides of the deploy need:
# root writes the quarantine record into it and the *app* reads it back to render
# /super-admin/deploy-status and the gates' history on /capacity. The installer
# owns the arrangement, and it got it wrong in a way no gate could see: it set
# the mode to 0750 and never set the group, so the directory belonged to
# root:root and the service user could not traverse it. Every symptom appeared
# somewhere else:
#
#   * the quarantine card could not say what was held — which is the one question
#     that card exists to answer, for an operator with no shell;
#   * the release request directory looked absent, so the page named a remedy
#     ("run the installer") that had already been applied;
#   * /capacity said "no gate record on this server" on a box whose gates were
#     recording on every release, because `Path.is_file()` turns a permission
#     error into "no file".
#
# So the property is: the state directory is reachable by the service user's group
# and not writable by it. Owner root, group read from the *unit* (the identity the
# app actually runs as, not the checkout's owner), mode 0750 — group read and
# traverse, no group write.


def _state_dir_block() -> str:
    """Just the installer's statements about the state directory itself.

    The children (`claims/`, `perf/`, `requests/`) are chowned to the service user
    on purpose and must not be confused with it, so the block ends at `requests`.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("mkdir -p /var/lib/scangrade-deploy\n")
    return text[start:text.index("mkdir -p /var/lib/scangrade-deploy/requests")]


class TestTheServiceUserCanReachItsStateDirectory:
    def test_the_group_comes_from_the_unit_that_runs_the_app(self):
        """Not a guess and not a hardcoded `scangrade`: the unit is the authority.

        Inferring it from the checkout's owner would look right on the box this was
        written against and produce the same unreadable directory on a checkout
        owned by anybody else — an install that reports success and is still blind.
        """
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert "sed -n 's/^Group=//p' \"$REPO/deploy/scangrade.service\"" in text, (
            "the app's group is not read from the unit that runs the app")
        assert "sed -n 's/^User=//p' \"$REPO/deploy/scangrade.service\"" in text

    def test_the_state_directory_is_chowned_to_that_group(self):
        block = _state_dir_block()
        assert 'chown root:"$SERVICE_GROUP" /var/lib/scangrade-deploy' in block, (
            "the state directory has no group chown, so a 0750 mode leaves it "
            "unreachable by the app process — the failure this guards against")
        assert re.search(r"^chmod 0750 /var/lib/scangrade-deploy$", block, re.M), (
            "the mode no longer grants the group read and traverse")

    def test_the_group_cannot_write_the_refusal_record_away(self):
        """Read access is the requirement; write access would be a defect.

        0755 would let any local user read the record; 0770/0755 with a group chown
        would let the app rewrite a refusal. 0750 is the one that says "the app may
        look, root decides".
        """
        block = _state_dir_block()
        assert "chmod 0755" not in block and "chmod 077" not in block, (
            "the state directory is writable by more than root, so a refusal record "
            "can be edited by the process it constrains")

    def test_each_child_goes_to_the_identity_that_needs_it(self):
        """Two writers, two arrangements, and neither is the other's.

        The gates write their history as the checkout's owner (`as_owner` in the
        deploy), so those directories are the owner's — with the unit's group, which
        is how the app reads them back on /capacity. `requests/` is different: it is
        *written by the app*, so the app's own user owns it. A group-only chown on
        the parent would have been the smaller edit and the wrong one, because the
        0750 parent deliberately has no group write.
        """
        text = INSTALL_SH.read_text(encoding="utf-8")
        for child in ("claims", "perf"):
            assert re.search(rf'chown "\$OWNER":"\$SERVICE_GROUP" '
                             rf'/var/lib/scangrade-deploy/{child}\n', text), (
                f"/var/lib/scangrade-deploy/{child} no longer reaches the app's "
                f"group, so the gate history is unreadable to the page that reports it")
        assert re.search(r'chown "\$SERVICE_USER":"\$SERVICE_GROUP" '
                         r'/var/lib/scangrade-deploy/requests\n', text), (
            "the one-click release directory is no longer the app user's, so the "
            "button cannot write the request it exists for")

    def test_the_staleness_alert_has_a_directory_only_the_app_writes(self):
        """The alert's record cannot live in the checkout, and a box without one
        mails on every tick.

        This is the second directory that is the app's alone, and its absence is
        quiet in the worst way: the deploy pulls into `$REPO` **as $OWNER**, so the
        service user cannot write anywhere inside it, and the alert service reads a
        *missing* record as "nothing was sent" — one mail per interval, forever,
        about the same stale runner instead of one a week.
        """
        text = INSTALL_SH.read_text(encoding="utf-8")
        assert re.search(r"mkdir -p /var/lib/scangrade-deploy/alerts\n", text), (
            "the installer no longer creates the alert's state directory")
        assert re.search(r'chown "\$SERVICE_USER":"\$SERVICE_GROUP" '
                         r'/var/lib/scangrade-deploy/alerts\n', text), (
            "the alert's state directory is not the app user's, so the record "
            "cannot be written and every tick re-notifies")
        assert re.search(r"chmod 0750 /var/lib/scangrade-deploy/alerts\n", text)

    def test_the_installer_and_the_service_name_the_same_directory(self):
        """Two copies of one path, so they are compared rather than trusted.

        The installer creates the directory and the service probes it; a rename on
        either side alone is an alert that stops recording on a box that looks
        installed.
        """
        from app.services import deploy_alert_service as alerts

        assert re.search(rf"mkdir -p {re.escape(alerts.DEPLOY_STATE_DIR)}\n",
                         INSTALL_SH.read_text(encoding="utf-8")), (
            f"the installer does not create {alerts.DEPLOY_STATE_DIR}, which is the "
            f"directory the alert service probes first")


# ── the one page a page-list cannot check ────────────────────────
#
# The smoke test's only content assertion lives here: it opens a real exam as a
# student and reads the two anti-cheat panels out of the served page. Everything
# it demands is read back out of the template it is talking about, so a renamed
# flag or a dropped sentence cannot leave the check asserting nothing quietly.

SMOKE_FIXTURE = DEPLOY / "demo_exam_fixture.py"
TAKE_EXAM = ROOT / "app" / "templates" / "student" / "take_exam.html"
MANAGE_PY = ROOT / "manage.py"

#: The id in the fake pages: 36 characters of hex and dashes, which is what the
#: check's link pattern accepts. A made-up short id would test the regex rather
#: than the check.
EXAM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _fixture_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sg_demo_exam_fixture_under_test",
                                                  SMOKE_FIXTURE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _markers() -> dict:
    """The page's own markers, taken from the template that has to carry them."""
    smoke = _smoke_module()
    template = TAKE_EXAM.read_text(encoding="utf-8")
    markers = {
        "FULLSCREEN_PANEL": smoke.FULLSCREEN_PANEL,
        "FULLSCREEN_WORDS": smoke.FULLSCREEN_WORDS,
        "AWAY_PANEL": smoke.AWAY_PANEL,
        "AWAY_WORDS": smoke.AWAY_WORDS,
        "FULLSCREEN_WATCH": smoke.FULLSCREEN_WATCH,
        "AWAY_WATCH": smoke.AWAY_WATCH,
    }
    missing = [name for name, marker in markers.items() if marker not in template]
    assert not missing, (
        f"take_exam.html no longer carries {missing}, so the smoke test would fail "
        f"every release over a page that is fine"
    )
    return markers


def _listing(*titles: str) -> str:
    """An exam list, one card per title, shaped the way the template renders it."""
    if not titles:
        titles = (_fixture_module().TITLE,)
    cards = []
    for index, title in enumerate(titles):
        exam_id = EXAM_ID if index == 0 else EXAM_ID[:-1] + str(index)
        cards.append(f'<div class="card"><h3 class="font-bold">{title}</h3>'
                     f'<a href="/student/exams/{exam_id}">Mulai Ujian</a></div>')
    return "<html><body>" + "".join(cards) + "</body></html>"


def _sitting(exam_id: str = EXAM_ID, **overrides) -> str:
    """A sitting page carrying the template's own markers and an armed config."""
    parts = {
        "open": f'<div x-data="examApp(60, 5)" data-exam-id="{exam_id}">',
        "config": 'antiCheat: {"enabled": true, "penalty_per_violation": 5, '
                  '"fullscreen_required": true},',
        "grace": "graceSeconds: 10,",
        **_markers(),
    }
    parts.update(overrides)
    order = ("open", "config", "grace", "FULLSCREEN_PANEL", "FULLSCREEN_WORDS",
             "AWAY_PANEL", "AWAY_WORDS", "FULLSCREEN_WATCH", "AWAY_WATCH")
    return ("<html><body>" + "".join(parts[name] for name in order)
            + "</body></html>")


class _Response:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


class _Session:
    """Serves the two pages the check asks for, and remembers what was asked."""

    def __init__(self, listing, page, status_code=200, location=""):
        self.listing = listing
        self.page = page
        self.status_code = status_code
        self.location = location
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        if url.endswith("/student/exams"):
            return _Response(self.listing)
        return _Response(self.page, self.status_code,
                         {"Location": self.location} if self.location else None)


def _run_check(listing=None, page=None, **kwargs):
    smoke = _smoke_module()
    res = smoke.Result()
    session = _Session(_listing() if listing is None else listing,
                       _sitting() if page is None else page, **kwargs)
    smoke.check_exam_sitting(session, "https://example.test", res)
    return res, session


def test_smoke_opens_the_demo_exam_and_reads_both_panels():
    res, session = _run_check()

    assert not res.failures, res.failures
    assert not res.warnings, res.warnings
    assert res.checked == 7, res.checked
    assert session.asked[0].endswith("/student/exams")
    assert session.asked[1].endswith(f"/student/exams/{EXAM_ID}")


@pytest.mark.parametrize("case", [
    "FULLSCREEN_PANEL", "FULLSCREEN_WORDS", "AWAY_PANEL", "AWAY_WORDS",
    "FULLSCREEN_WATCH", "AWAY_WATCH",
])
def test_the_exam_check_bites_when_the_page_loses_a_panel(case):
    """Each marker is one way the supervision can silently disappear."""
    res, _ = _run_check(page=_sitting(**{case: ""}))

    assert res.failures, f"a page with no {case} was accepted"


@pytest.mark.parametrize("needle,replacement", [
    # Anti-cheat off: the whole ladder is gated on it, so neither panel would ever
    # be revealed however intact the markup is.
    ('"enabled": true', '"enabled": false'),
    # Fullscreen not required: the blocker is gated on this one.
    ('"fullscreen_required": true', '"fullscreen_required": false'),
    # No countdown: the away blur would be an instant fine, which is not what the
    # page promises a student.
    ("graceSeconds: 10", "graceSeconds: 0"),
])
def test_the_exam_check_bites_when_the_panels_are_not_armed(needle, replacement):
    res, _ = _run_check(page=_sitting().replace(needle, replacement))

    assert res.failures, f"{needle} -> {replacement} was accepted"


def test_the_exam_check_warns_and_opens_nothing_without_the_fixture():
    """A box whose demo data was cleared must not roll a healthy release back.

    It must also not open anything: a real paper's anti-cheat setting is the
    teacher's business, and failing over one would be this check lying about what
    it measured.
    """
    res, session = _run_check(listing=_listing("Ujian Fisika"))

    assert not res.failures, res.failures
    assert res.warnings, "a missing fixture went unreported"
    assert session.asked == ["https://example.test/student/exams"], session.asked
    assert res.checked == 0


def test_the_exam_check_opens_the_fixture_and_not_the_first_card():
    """The card the check opens must be the fixture, not whatever is on top."""
    fixture = _fixture_module()
    # The second card's id, so the page it is served is the one it asked for.
    other = EXAM_ID[:-1] + "1"
    res, session = _run_check(listing=_listing("Ujian Fisika", fixture.TITLE),
                              page=_sitting(exam_id=other))

    assert not res.failures, res.failures
    assert session.asked[1].endswith(f"/student/exams/{other}"), session.asked


def test_the_exam_check_reports_a_page_that_refused_to_open():
    """A redirect is the app saying no, and it must not read as a pass."""
    res, _ = _run_check(status_code=302, location="/student/exams")

    assert res.failures
    assert "would not open" in " ".join(res.failures)


def test_the_exam_check_reports_a_page_that_is_not_the_sitting_page():
    res, _ = _run_check(page=_sitting().replace(f'data-exam-id="{EXAM_ID}"',
                                                'data-exam-id="other"'))

    assert res.failures
    assert "not the sitting page" in " ".join(res.failures)


# ── the fixture the check opens ──────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """Just enough PostgREST for `demo_exam_fixture.ensure`."""

    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.filters = []
        self.op = "select"
        self.payload = None

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def limit(self, count):
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        if self.op == "insert":
            row = dict(self.payload, id=f"new-{self.name}")
            self.client.rows.setdefault(self.name, []).append(row)
            self.client.writes.append((self.name, "insert", dict(self.payload)))
            return _Result([row])
        if self.op == "update":
            self.client.writes.append((self.name, "update", dict(self.payload)))
            self.client.updates.append((self.name, dict(self.payload)))
            return _Result([])
        rows = list(self.client.rows.get(self.name, []))
        for kind, column, value in self.filters:
            rows = [row for row in rows
                    if (row.get(column) == value) == (kind == "eq")]
        return _Result(rows)


class _FakeSupabase:
    def __init__(self, exams=(), submissions=()):
        fixture = _fixture_module()
        # Every row carries the columns the queries filter on: a fake that omits
        # them answers "no classes, no teacher", and the fixture is quietly never
        # written while the test still has something to assert about.
        self.rows = {
            "subjects": [{"id": "subject-1", "school_id": "school-1",
                          "name": fixture.SUBJECT}],
            "classes": [{"id": "class-a", "school_id": "school-1"},
                        {"id": "class-b", "school_id": "school-1"}],
            "teacher_assignments": [{"teacher_id": "teacher-1", "school_id": "school-1",
                                     "subject_id": "subject-1"}],
            "profiles": [{"id": "teacher-9", "school_id": "school-1", "role": "guru"}],
            "exams": list(exams),
            "submissions": list(submissions),
        }
        self.writes = []
        self.updates = []

    def table(self, name):
        return _Table(self, name)

    def inserted(self, name):
        return [payload for table, op, payload in self.writes
                if (table, op) == (name, "insert")]

    def updated(self, name):
        return [payload for table, payload in self.updates if table == name]


def test_the_fixture_is_written_sittable_in_every_way_the_check_needs():
    fixture = _fixture_module()
    client = _FakeSupabase()
    said = []
    result = fixture.ensure(client, "school-1", say=said.append)

    payload = client.inserted("exams")[0]
    assert payload["title"] == fixture.TITLE
    assert payload["subject_id"] == "subject-1"
    assert payload["class_ids"] == ["class-a", "class-b"], (
        "assigned to every class, or the student the check signs in as is refused"
    )
    assert payload["start_at"] is None and payload["end_at"] is None, (
        "a window is a date on which the fixture stops being sittable"
    )
    assert payload["anti_cheat_enabled"] is True
    assert payload["fullscreen_required"] is True, (
        "without it the blocker can never be revealed"
    )
    assert payload["status"] == "active" and payload["is_published"] is True
    assert payload["teacher_id"], "exams.teacher_id is NOT NULL"
    assert result == {"exam_id": "new-exams", "classes": 2, "voided": 0}
    assert said, "the command said nothing about what it did"


def test_running_the_fixture_again_repairs_that_row_instead_of_adding_one():
    fixture = _fixture_module()
    client = _FakeSupabase(exams=[{"id": "exam-1", "school_id": "school-1",
                                   "title": fixture.TITLE}])

    fixture.ensure(client, "school-1", say=lambda *_: None)

    assert client.inserted("exams") == [], "a repeated run added a second fixture"
    updated = client.updated("exams")[0]
    assert updated["class_ids"] == ["class-a", "class-b"]
    assert updated["end_at"] is None, (
        "a teacher's end date on the demo paper would close the gate for good"
    )
    assert updated["fullscreen_required"] is True


def test_a_standing_attempt_is_voided_so_the_fixture_can_be_sat_again():
    """A student who has submitted it is not offered it, so it must be re-opened.

    Voided rather than deleted: `retracted` is the app's own word for an attempt
    that does not stand, and it is what `open_sitting` reopens with a fresh clock.
    """
    fixture = _fixture_module()
    client = _FakeSupabase(
        exams=[{"id": "exam-1", "school_id": "school-1", "title": fixture.TITLE}],
        submissions=[{"id": "s1", "exam_id": "exam-1", "status": "graded"},
                     {"id": "s2", "exam_id": "exam-1", "status": "retracted"}],
    )

    result = fixture.ensure(client, "school-1", say=lambda *_: None)

    assert client.updated("submissions") == [{"status": "retracted"}]
    assert result["voided"] == 1, "only the attempt that stood should be counted"


def test_the_fixture_spec_is_the_only_place_the_title_is_written():
    """Two copies of the marker would be two chances for them to drift apart."""
    fixture = _fixture_module()
    smoke = _smoke_module()

    assert smoke._fixture().TITLE == fixture.TITLE
    for path in (SMOKE_PY, MANAGE_PY, DEPLOY_SH):
        assert fixture.TITLE not in path.read_text(encoding="utf-8"), (
            f"{path.name} spells the fixture's title out again instead of reading it"
        )


def test_manage_offers_the_fixture_command_and_seeding_makes_one():
    text = MANAGE_PY.read_text(encoding="utf-8")

    assert '"demo-exam"' in text, "manage.py does not offer `demo-exam`"
    assert "cmd_demo_exam" in text
    assert "demo_exam.ensure(supabase, sid)" in text, (
        "`seed` no longer leaves a sittable fixture behind"
    )


def test_the_runner_refreshes_the_fixture_before_it_is_read():
    script = DEPLOY_SH.read_text(encoding="utf-8")

    step = '"$REPO/manage.py" demo-exam'
    assert step in script, (
        "the deploy runner never refreshes the exam the smoke test opens"
    )
    at = script.index(step)
    assert at < script.index("deploy/smoke_test.py"), (
        "the fixture is refreshed after the check that reads it"
    )
    assert "START_BACKGROUND_SCHEDULERS=false" in script[at - 400:at], (
        "constructing the app here would start the retention loop, which purges"
    )
    # Never fatal: a box whose demo data is gone must not roll a healthy release
    # back, and the smoke test reports the missing fixture itself.
    block = script[at:script.index("deploy/smoke_test.py")]
    assert not re.search(r"^\s*exit\b", block, re.M), (
        "a fixture that could not be written fails the release"
    )


# ── a refusal *before* the release moves is recorded too ─────────────────────
#
# A quarantine is a record about a commit, so it is written only after a release
# has been merged and judged. Everything the run can refuse before that — a dirty
# checkout, a fetch with no network, an unreadable checkout, a migration release
# with no recovery point, a merge that cannot happen — used to leave nothing at
# all: no quarantine, no reload, no moved checkout, and a status page whose own
# readings (0 uncommitted files, clear quarantine) described the box as fine. A box
# in that state fetches every tick and deploys nothing, indefinitely.
#
# So each of those exits writes a record and, just as importantly, the *command's*
# own words: a stale `.git/index.lock` makes `git merge` fail while `git status`
# succeeds, which is exactly the case where the reason has to come from stderr
# rather than from the script's guess about what a merge failure means.

RECORD_START = "# preflight-logic:start"
RECORD_END = "# preflight-logic:end"
RECORD_CALL = re.compile(r"PREFLIGHT_GATE=([a-z_]+) PREFLIGHT_EXIT=(\d+)")

#: Every step the runner can refuse at before the merge. Named here so a new one
#: has to be added deliberately — the page needs a sentence for each, and the
#: service's `PREFLIGHT_GATES` has to gain it too.
RECORD_GATES = {
    "not_root",
    "no_checkout",
    "no_virtualenv",
    "checkout_unreadable",
    "dirty_checkout",
    "fetch_failed",
    "snapshot_refused",
    "lock_refused",
    "merge_refused",
}


def _record_block() -> str:
    script = DEPLOY_SH.read_text(encoding="utf-8")
    return script.split(RECORD_START, 1)[1].split(RECORD_END, 1)[0]


def _record_calls() -> list:
    """(gate, the exit code it records, offset) for every recorded refusal."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    return [(m.group(1), m.group(2), m.end()) for m in RECORD_CALL.finditer(script)]


def test_the_record_lives_between_its_own_delimiters():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert RECORD_START in script and RECORD_END in script, (
        "the block's delimiters are what let it be tested on its own, the way the "
        "quarantine's and Gate 0's are; keep them")
    block = _record_block()
    for fn in ("preflight_write() {", "preflight_forget() {"):
        assert fn in block, f"{fn} left the delimited block"


def test_every_pre_merge_refusal_is_recorded_and_leaves_with_that_code():
    """Both halves of the pair, per site.

    The gate key is what the page turns into a sentence and the exit code is what
    `systemctl status` reports, so the two are written together and have to agree
    with the `exit` that follows — a record naming a gate the run then does not
    leave through is worse than no record at all.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    calls = _record_calls()
    assert {gate for gate, _, _ in calls} == RECORD_GATES, (
        f"the recorded steps changed: {sorted({g for g, _, _ in calls})}")
    for gate, code, end in calls:
        found = re.search(r"^\s*exit (\d+)", script[end:end + 400], re.M)
        assert found, f"{gate} records a refusal and then does not leave"
        assert found.group(1) == code, (
            f"{gate} records exit {code} and then exits {found.group(1)} — the page "
            "and systemd would disagree about why the run ended")


def test_nothing_is_recorded_as_a_pre_merge_refusal_after_the_merge():
    """The record describes what stopped the release *getting* to the checkout.

    Past the merge there is a different record for that — the quarantine — and one
    written here would outlive the state it describes.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    # `merge_refused` itself is recorded *at* the merge — that is the refusal — so
    # the boundary is the moment the merge succeeded and the record was cleared.
    healed = script.index("preflight_forget", script.index("merge --ff-only"))
    late = [gate for gate, _, end in _record_calls() if end > healed]
    assert not late, f"recorded as pre-merge refusals after the merge: {late}"
    assert "preflight_write" not in script[script.index("Gate 0 survives the release"):], (
        "a pre-merge refusal record is written after the release was merged")


def test_a_merged_release_forgets_the_record():
    """The box must stop reporting a refusal to get here once it has got here."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    merge = script.index("merge --ff-only")
    forget = script.index("preflight_forget", merge)
    assert forget > merge, "nothing clears the record when a release merges"
    assert forget < script.index("Gate 0 survives the release"), (
        "the record is cleared after the next gate, so a refusal in between would "
        "be erased by a release that never finished")
    assert 'rm -f "$PREFLIGHT_FILE"' in _record_block(), (
        "preflight_forget does not remove the record it is named for")


def test_the_reasons_that_were_thrown_away_are_captured():
    """`--quiet` hid git's own words, so the runner guessed instead.

    The merge is the one that mattered: "not a fast-forward (history rewritten?)"
    was printed every two minutes while the real cause was a lock file git could
    not create. Every command whose failure *is* its message now has its stderr
    captured, and the captured text is what the record carries.
    """
    script = DEPLOY_SH.read_text(encoding="utf-8")
    for variable, command in (
        ("FETCH_OUT", 'fetch --quiet origin "$BRANCH" 2>&1'),
        ("MERGE_OUT", 'merge --ff-only --quiet "origin/$BRANCH" 2>&1'),
        ("SNAP_OUT", "--quiet 2>&1"),
    ):
        assert f"{variable}=$(" in script, f"{variable} is never captured"
        assert command in script, (
            f"the {variable} capture does not take the command's stderr, so the "
            "record can only carry a guess")
        assert f'PREFLIGHT_DETAIL="${{{variable}:-' in script, (
            f"{variable} is captured and then not recorded — the page would show a "
            "paraphrase of a failure whose whole diagnosis is the command's output")
    assert "not a fast-forward (history rewritten?)" not in script, (
        "the runner is guessing again: a merge can fail without history having been "
        "rewritten, and that guess is what hid the lock file")


def test_an_unreadable_checkout_is_not_read_as_clean():
    """`DIRTY=$(git status)` came back empty either way, and empty is what a clean
    tree looks like — so a checkout git could not read was merged into, and the
    failure surfaced later as a merge error about fast-forwards."""
    script = DEPLOY_SH.read_text(encoding="utf-8")
    guard = script[script.index("Guard against clobbering hand edits"):
                   script.index("BEFORE=$(as_owner")]
    assert "checkout_unreadable" in guard, (
        "a failed `git status` is still treated as a clean checkout")
    assert "2>&1" in guard, "the failure is still thrown away instead of recorded"
    assert guard.index("checkout_unreadable") < guard.index("dirty_checkout"), (
        "the unreadable arm has to come first: afterwards the empty answer has "
        "already been read as a clean tree")
    assert not re.search(r'DIRTY=\$\(as_owner git -C "\$REPO" status --porcelain\)', script), (
        "the uncaptured assignment is back, so an empty answer means both "
        "\"clean\" and \"git said nothing\"")


# ── and it behaves ───────────────────────────────────────────────────────────

SHA_C = "c" * 40


def _record_harness(tmp_path: Path) -> str:
    """The real block, lifted out and given the paths it reads.

    `AFTER_FULL` is deliberately *not* set here: the block has to survive a
    refusal that happens before a commit is under judgement, which is what the
    first two exits in the script do under `set -u`.
    """
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return (
        "set -uo pipefail\n"
        f'STATE_DIR="{state}"\n'
        f'PREFLIGHT_FILE="{state}/refused-before-merge"\n'
        + _record_block()
    )


def _run_record(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", _record_harness(tmp_path) + body],
                          capture_output=True, text=True)


needs_a_bash = pytest.mark.skipif(BASH is None, reason="needs a bash to run the record")


@needs_a_bash
def test_the_record_is_five_lines_a_page_can_read(tmp_path):
    done = _run_record(
        tmp_path,
        f'AFTER_FULL="{SHA_C}"\n'
        'PREFLIGHT_GATE=merge_refused PREFLIGHT_EXIT=6 \\\n'
        '  PREFLIGHT_DETAIL="error: Unable to create index.lock: File exists" \\\n'
        "  preflight_write\n",
    )
    assert done.returncode == 0, done.stderr
    lines = (tmp_path / "state/refused-before-merge").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5, lines
    assert lines[0] == "merge_refused"
    assert lines[1].startswith("20"), "the record has no time on it"
    assert lines[2] == "6"
    assert lines[3] == SHA_C
    assert "index.lock" in lines[4], "the command's own words did not reach the record"


@needs_a_bash
def test_a_refusal_before_there_is_a_commit_records_an_empty_one(tmp_path):
    """The early exits (no root, no checkout) refuse before `origin/main` is read,
    and `set -u` must not turn that into a crash that loses the record."""
    done = _run_record(
        tmp_path,
        'PREFLIGHT_GATE=not_root PREFLIGHT_EXIT=2 \\\n'
        '  PREFLIGHT_DETAIL="uid 1000 is not root" preflight_write\n',
    )
    assert done.returncode == 0, done.stderr
    body = (tmp_path / "state/refused-before-merge").read_text(encoding="utf-8")
    assert body.splitlines()[3] == "", "an empty commit became something else"


@needs_a_bash
def test_the_record_is_readable_by_the_app_that_renders_the_page(tmp_path):
    """Root writes it and the service user has to read it, so it cannot be 0600
    inside a directory the app cannot reach."""
    assert 'chmod 0644 "$PREFLIGHT_FILE"' in DEPLOY_SH.read_text(encoding="utf-8"), (
        "the record is not made readable by the app that renders the page")
    done = _run_record(
        tmp_path,
        'PREFLIGHT_GATE=dirty_checkout PREFLIGHT_EXIT=4 PREFLIGHT_DETAIL="?? x" '
        "preflight_write\n",
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "state/refused-before-merge").exists()


@needs_a_bash
def test_a_second_refusal_replaces_the_first_rather_than_stacking(tmp_path):
    """One record, always the newest: a page showing the oldest refusal would point
    an operator at a cause that has already been fixed."""
    done = _run_record(
        tmp_path,
        'PREFLIGHT_GATE=dirty_checkout PREFLIGHT_EXIT=4 PREFLIGHT_DETAIL="first" '
        "preflight_write\n"
        'PREFLIGHT_GATE=fetch_failed PREFLIGHT_EXIT=5 PREFLIGHT_DETAIL="second" '
        "preflight_write\n",
    )
    assert done.returncode == 0, done.stderr
    first = (tmp_path / "state/refused-before-merge").read_text(encoding="utf-8")
    assert first.splitlines()[0] == "fetch_failed"


@needs_a_bash
def test_a_merged_release_removes_the_record_and_a_missing_one_is_not_an_error(tmp_path):
    done = _run_record(tmp_path, "preflight_forget\n")
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "state/refused-before-merge").exists()
    done = _run_record(
        tmp_path,
        'PREFLIGHT_GATE=snapshot_refused PREFLIGHT_EXIT=12 PREFLIGHT_DETAIL="x" '
        "preflight_write\npreflight_forget\n",
    )
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "state/refused-before-merge").exists()
