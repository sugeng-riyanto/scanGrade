"""Isolated staging gate tests using stdlib only (fast independent CI)."""
import os
import tempfile
import unittest
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
