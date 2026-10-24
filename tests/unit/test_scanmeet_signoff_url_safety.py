"""Pasteable ScanMeet reports never leak URL secrets."""
import unittest
from deploy.scanmeet_media_gate import Row, ROW_ORDER, EXIT_CANNOT_MEASURE, signoff_sheet


class SignoffUrlSafety(unittest.TestCase):
    def test_url_userinfo_query_and_fragment_are_removed(self):
        rows = [Row(name, "skipped", "not yet measured") for name in ROW_ORDER]
        report = signoff_sheet(
            "https://user:private@staging.example.edu/live?token=hidden#fragment",
            "exam-1", rows, EXIT_CANNOT_MEASURE)
        self.assertIn("https://staging.example.edu/live", report)
        for secret in ("user:", "private@", "token=", "hidden", "#fragment"):
            self.assertNotIn(secret, report)
        self.assertIn("CANNOT MEASURE", report)
