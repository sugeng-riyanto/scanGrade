"""Isolated staging gate tests using stdlib only (fast independent CI)."""
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from deploy import scanmeet_staging_gate as gate


def valid():
    return {
        "SCANMEET_STAGING": "true",
        "SCANMEET_STAGING_URL": "https://staging.school.edu",
        "SCANGRADE_LIVE_ENABLED": "true",
        "SCANGRADE_LIVE_EXAM_ENABLED": "true",
        "LIVEKIT_URL": "wss://school.livekit.cloud",
        "LIVEKIT_API_KEY": "synthetic-fixture-key",
        "LIVEKIT_API_SECRET": "synthetic-fixture-secret",
        "SUPABASE_URL": "https://independent-staging.supabase.co",
        "SUPABASE_SERVICE_KEY": "synthetic-stage-service-key",
        "SUPABASE_ANON_KEY": "synthetic-stage-anon-key",
        "FLASK_SECRET_KEY": "synthetic-stage-session-key",
    }


class ScanMeetStagingBoundary(unittest.TestCase):
    def test_isolated_private_stage_is_accepted(self):
        self.assertEqual(gate.validate(valid(), "prod-ref"), [])

    def test_real_production_project_is_blocked(self):
        env = valid()
        env["SUPABASE_URL"] = "https://prod-ref.supabase.co"
        self.assertTrue(any("isolated" in issue for issue in gate.validate(env, "prod-ref")))

    def test_flags_off_cannot_pretend_to_be_a_pilot(self):
        env = valid()
        env["SCANGRADE_LIVE_EXAM_ENABLED"] = "false"
        self.assertTrue(any("both Live" in issue for issue in gate.validate(env, "prod-ref")))

    def test_production_domain_refused(self):
        env = valid()
        env["SCANMEET_STAGING_URL"] = "https://scan-grade.app"
        self.assertTrue(any("separate HTTPS" in s for s in gate.validate(env, "prod-ref")))

    def test_placeholder_supabase_key_refused(self):
        env = valid()
        env["SUPABASE_SERVICE_KEY"] = "replace-me"
        self.assertTrue(any("placeholder" in s for s in gate.validate(env, "prod-ref")))

    def test_check_file_refuses_in_repo_secret(self):
        with tempfile.TemporaryDirectory() as root:
            tmp_path = Path(root)
            secret = tmp_path / "staging.env"
            secret.write_text("example=not-a-secret")
            secret.chmod(0o600)
            self.assertTrue(any("outside" in s for s in gate.validate_file(secret, tmp_path)))

    def test_check_file_refuses_world_readable_secret(self):
        with tempfile.TemporaryDirectory() as root:
            tmp_path = Path(root)
            checkout = tmp_path / "checkout"
            checkout.mkdir()
            secret = tmp_path / "outside.env"
            secret.write_text("example=not-a-secret")
            secret.chmod(0o644)
            self.assertTrue(any("0600" in s
                                for s in gate.validate_file(secret, checkout, measure=True)))

    def test_check_file_accepts_external_private_secret(self):
        """No rule but the mode refuses this file, on every platform.

        The mode half is asked separately below with a synthetic integer: Windows
        cannot produce a 0600 file, so a test that asserted `[]` here would fail on
        the developer checkout for a reason that is not a defect — and a test that
        patched the flag would be asserting its own patch.
        """
        with tempfile.TemporaryDirectory() as root:
            tmp_path = Path(root)
            checkout = tmp_path / "checkout"
            checkout.mkdir()
            secret = tmp_path / "outside.env"
            secret.write_text("example=not-a-secret")
            self.assertEqual(gate.validate_file(secret, checkout, measure=False), [])

    def test_private_mode_is_accepted(self):
        self.assertEqual(gate.mode_problem(0o100600, measure=True), [])

    def test_world_readable_mode_is_refused(self):
        self.assertTrue(any("0600" in s for s in gate.mode_problem(0o100644, measure=True)))

    def test_group_or_other_write_is_refused(self):
        self.assertTrue(any("0600" in s for s in gate.mode_problem(0o100620, measure=True)))

    def test_a_directory_is_not_an_env_file(self):
        self.assertTrue(gate.mode_problem(0o040700, measure=True))

    def test_a_platform_without_posix_bits_does_not_pretend_to_have_measured(self):
        """Windows reports 0o666 for a chmod-0600 file; claiming 0600 was checked
        there would refuse every valid env and pass the world-readable test while
        reading nothing."""
        self.assertEqual(gate.mode_problem(0o100644, measure=False), [])
        self.assertEqual(gate.MEASURE_MODES, os.name == "posix")

    def test_check_file_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            tmp_path = Path(root)
            checkout = tmp_path / "checkout"
            checkout.mkdir()
            private = tmp_path / "outside.env"
            private.write_text("example")
            private.chmod(0o600)
            symlink = tmp_path / "linked.env"
            symlink.symlink_to(private)
            self.assertTrue(any("symlink" in s for s in gate.validate_file(symlink, checkout)))


if __name__ == "__main__":
    unittest.main()


class ThePrivateFileIsNamedForWhatIsWrongWithIt(unittest.TestCase):
    """A missing file and an unreadable one are different sentences.

    On a box where staging has not been set up yet the file is absent, and the
    old message — "not readable" — sent the reader looking for a permission
    problem that was not there. The other half (a directory, an in-repo file, a
    world-readable mode) is already held by `ScanMeetStagingBoundary` above.
    """

    def test_a_missing_private_env_says_it_does_not_exist(self):
        with tempfile.TemporaryDirectory() as checkout, \
                tempfile.TemporaryDirectory() as elsewhere:
            missing = Path(elsewhere) / "staging.env"
            problems = gate.validate_file(missing, Path(checkout))
            self.assertTrue(any("does not exist" in p for p in problems), problems)
            self.assertFalse(any("not readable" in p for p in problems), problems)


def _without_livekit(call):
    """Run `call` with `livekit` unimportable, capturing its stdout.

    `sys.modules[name] = None` is the interpreter's own way to make an import
    fail ("import of livekit halted; None in sys.modules"), which is closer to a
    venv without the package than deleting a file would be.
    """
    import contextlib
    import io

    hidden = sys.modules.get("livekit")
    sys.modules["livekit"] = None
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = call()
    finally:
        if hidden is None:
            sys.modules.pop("livekit", None)
        else:
            sys.modules["livekit"] = hidden
    return code, buffer.getvalue()


class TheOptionalSdkIsNamedRatherThanBlamed(unittest.TestCase):
    """A venv built from `requirements.txt` alone is a good production venv.

    On it, `--probe-livekit` used to answer "LiveKit server connectivity/
    authorization" — a sentence that sends an operator to the LiveKit dashboard
    and the firewall for a Python package that is simply not installed. The
    staging set is optional (`requirements-live.txt`), so its absence is named,
    and it is exit 2 (could not measure) rather than 1 (the credentials
    disagreed): those are different facts and only one of them is actionable.
    """

    def _env_file(self, root):
        import json as _json

        path = Path(root) / "staging.env"
        path.write_text(
            "\n".join(f"{key}={_json.dumps(value) if ' ' in value else value}"
                      for key, value in valid().items()) + "\n",
            encoding="utf-8")
        # `write_text` makes the file 0644 on Linux (0666 & ~umask), which the gate
        # refuses on purpose -- and it is the first refusal, so the SDK branch these
        # tests are about was never reached there. The runbook creates this file
        # with `umask 077`; this fixture is the same private file.
        path.chmod(0o600)
        return path

    def test_the_staging_gate_names_a_missing_sdk(self):
        from deploy import scanmeet_staging_gate as staging

        with tempfile.TemporaryDirectory() as root:
            env_file = self._env_file(root)
            code, out = _without_livekit(
                lambda: staging.main(["--env", str(env_file), "--probe-livekit"]))
        self.assertEqual(code, 2, "a missing package is not a credential failure")
        self.assertIn("requirements-live.txt", out, (
            "the fix is a pip install line; the message has to be that line"))
        self.assertNotIn("connectivity/authentication", out, (
            "this sentence sends its reader to the LiveKit dashboard and the "
            "firewall for a package that is not installed"))

    def test_the_connectivity_probe_answers_two_for_a_missing_sdk(self):
        from deploy import livekit_connectivity

        with tempfile.TemporaryDirectory() as root:
            env_file = self._env_file(root)
            code, out = _without_livekit(
                lambda: livekit_connectivity.main(["--env", str(env_file)]))
        self.assertEqual(code, 2)
        self.assertIn("requirements-live.txt", out)

    def test_the_webhook_probe_says_which_file_installs_pyjwt(self):
        """The exit code alone is not enough here, and the mutation proved it.

        Without the guard, `main` still answers 2 — the signing call raises
        `AttributeError`, `probes()` turns it into a `ProbeError`, and the code
        is identical. What differs is the sentence: "'NoneType' object has no
        attribute 'encode'" names nothing a reader can install.
        """
        import contextlib
        import io

        from deploy import scanmeet_webhook_probe as probe

        buffer = io.StringIO()
        with unittest.mock.patch.object(probe, "jwt", None), \
                contextlib.redirect_stdout(buffer):
            code = probe.main(["--url", "https://staging.school.edu",
                               "--api-key", "synthetic", "--api-secret", "synthetic"])
        out = buffer.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("requirements-live.txt", out, out)
        self.assertNotIn("has no attribute", out, (
            "a reader sent to a NoneType traceback cannot install the package"))
