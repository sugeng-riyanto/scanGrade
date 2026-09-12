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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

import subprocess
import sys

DEPLOY_SH = DEPLOY / "scangrade-deploy.sh"
INSTALL_SH = DEPLOY / "install-auto-deploy.sh"
SMOKE_PY = DEPLOY / "smoke_test.py"
APP_SERVICE = DEPLOY / "scangrade.service"
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
