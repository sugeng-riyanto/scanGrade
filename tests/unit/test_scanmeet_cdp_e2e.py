"""Offline acceptance-contract tests for the real CDP driver (no server/camera)."""
import unittest

from deploy import scanmeet_cdp_e2e as e2e


STAGING = "https://stage.school.example.edu"
LOGINS = {"teacher": "t@school:synthetic-pass", "pupil_a": "a@school:synthetic-pass", "pupil_b": "b@school:synthetic-pass"}


def sample(*, incoming=0, frames=0, tiles=1, mic=False, audio=0,
           audio_packets=0, videos=None, hand=True, pcs=2):
    if videos is None:
        videos = [{"width": 640, "height": 480, "frames": 100} for _ in range(tiles)]
    return {"pcCount": pcs, "incomingVideo": incoming, "videoFrames": frames,
            "videos": videos, "tiles": tiles, "sendingAudio": audio,
            "micVisible": mic, "handEnabled": hand, "audioPackets": audio_packets}


class UrlAndSafety(unittest.TestCase):
    def test_refuses_missing_explicit_staging_confirmation(self):
        with self.assertRaises(e2e.Unmeasured):
            e2e.check_preconditions(STAGING, "exam-id", LOGINS,
                                    confirmed=False, expected_url=STAGING)

    def test_refuses_production_hostname(self):
        with self.assertRaises(e2e.Unmeasured):
            e2e.safe_staging_url("https://scan-grade.app", "https://scan-grade.app")

    def test_refuses_different_staging_host(self):
        with self.assertRaises(e2e.Unmeasured):
            e2e.safe_staging_url(STAGING, "https://other.school.edu")

    def test_refuses_insecure_local_and_embedded_creds(self):
        for url in ("http://stage.school.edu", "https://localhost", "https://127.0.0.1",
                    "https://x:y@stage.school.edu", "https://stage.school.edu?token=x",
                    "https://stage.school.edu/live/exam/123"):
            with self.subTest(url=url), self.assertRaises(e2e.Unmeasured):
                e2e.safe_staging_url(url, url)

    def test_accepts_exact_staging_origin_and_distinct_accounts(self):
        self.assertEqual(e2e.check_preconditions(STAGING, "safe-exam-id", LOGINS,
                          confirmed=True, expected_url=STAGING), STAGING)

    def test_refuses_duplicate_accounts(self):
        creds = dict(LOGINS, pupil_b="a@school:different-password")
        with self.assertRaises(e2e.Unmeasured):
            e2e.check_preconditions(STAGING, "id", creds, confirmed=True,
                                    expected_url=STAGING)

    def test_refuses_unset_credentials(self):
        creds = dict(LOGINS, pupil_a="")
        with self.assertRaises(e2e.Unmeasured):
            e2e.check_preconditions(STAGING, "id", creds, confirmed=True,
                                    expected_url=STAGING)

    def test_refuses_injected_exam_path(self):
        with self.assertRaises(e2e.Unmeasured):
            e2e.check_preconditions(STAGING, "../../production", LOGINS,
                                    confirmed=True, expected_url=STAGING)


class MediaPrivacyContract(unittest.TestCase):
    def setUp(self):
        self.teacher = sample(incoming=2, frames=80, tiles=3)
        self.a = sample()
        self.b = sample()

    def test_three_participants_with_private_pupil_cameras(self):
        e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_hidden_remote_track_fails_even_with_only_one_tile(self):
        self.a["incomingVideo"] = 1
        self.a["videoFrames"] = 20
        with self.assertRaisesRegex(e2e.Finding, "privacy breach"):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_visible_second_video_fails_even_without_stats(self):
        self.b["tiles"] = 2
        with self.assertRaisesRegex(e2e.Finding, "video tiles"):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_teacher_missing_second_pupil_video_fails(self):
        self.teacher["incomingVideo"] = 1
        with self.assertRaisesRegex(e2e.Finding, "both independent"):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_no_instrumented_peer_connection_is_unmeasured(self):
        self.a["pcCount"] = 0
        with self.assertRaises(e2e.Unmeasured):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_student_mic_early_fails(self):
        self.a["sendingAudio"] = 1
        with self.assertRaisesRegex(e2e.Finding, "before teacher approval"):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_hidden_and_muted_camera_fails(self):
        self.b["videos"] = [{"width": 0, "height": 0, "frames": 0}]
        with self.assertRaisesRegex(e2e.Finding, "no decoded video dimensions"):
            e2e.assert_baseline(self.teacher, self.a, self.b)

    def test_decoded_frames_must_advance(self):
        with self.assertRaisesRegex(e2e.Finding, "no new decoded"):
            e2e.assert_frames_advance(self.teacher, self.teacher, "teacher")
        e2e.assert_frames_advance(self.teacher, dict(self.teacher, videoFrames=100), "teacher")

    def test_local_camera_frames_must_advance(self):
        with self.assertRaisesRegex(e2e.Finding, "locally displayed"):
            e2e.assert_tile_frames_advance(self.a, self.a, "student")
        e2e.assert_tile_frames_advance(self.a, dict(self.a, videos=[{"frames": 111}]), "student")


class MicrophoneStateContract(unittest.TestCase):
    def test_approve_and_revoke_actual_microphone_sender(self):
        e2e.assert_audio_gating(sample(), sample(mic=True, audio=1),
                                sample(mic=False, audio=0),
                                sample(audio_packets=0), sample(audio_packets=30))

    def test_fail_when_approval_only_shows_button(self):
        with self.assertRaisesRegex(e2e.Finding, "cannot publish"):
            e2e.assert_audio_gating(sample(), sample(mic=True, audio=0),
                                    sample(), sample(), sample(audio_packets=20))

    def test_fail_if_rtp_is_not_received_by_teacher(self):
        with self.assertRaisesRegex(e2e.Finding, "did not receive"):
            e2e.assert_audio_gating(sample(), sample(mic=True, audio=1),
                                    sample(), sample(audio_packets=9), sample(audio_packets=9))

    def test_fail_if_sender_remains_after_revoke_even_hidden_button(self):
        with self.assertRaisesRegex(e2e.Finding, "continued after"):
            e2e.assert_audio_gating(sample(), sample(mic=True, audio=1),
                                    sample(mic=False, audio=1),
                                    sample(), sample(audio_packets=20))


class ReportContract(unittest.TestCase):
    def test_unmeasured_is_never_reported_as_pass(self):
        report = e2e.sanitized_result(e2e.EXIT_UNMEASURED, "staging missing")
        self.assertEqual(report["exit"], 2)
        self.assertEqual(report["status"], "UNMEASURED")

    def test_script_error_is_not_silently_accepted(self):
        with self.assertRaises(e2e.Unmeasured):
            e2e.assess_response({"exceptionDetails": {"text": "broken"}})
        with self.assertRaises(e2e.Unmeasured):
            e2e.assess_response({"result": {"value": None}})

    def test_runtime_reading_keeps_only_values(self):
        self.assertEqual(e2e.assess_response({"result": {"value": {"tiles": 1}}}), {"tiles": 1})


if __name__ == "__main__":
    unittest.main()
