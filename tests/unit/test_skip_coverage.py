"""The coverage auditor must fail closed when testcases do not run."""
import xml.etree.ElementTree as ET
from deploy import skip_coverage


def _report(tmp_path, names, skipped=(), failures=()):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for name in names:
        case = ET.SubElement(suite, "testcase", name=name)
        if name in skipped:
            ET.SubElement(case, "skipped")
        if name in failures:
            ET.SubElement(case, "failure")
    path = tmp_path / "result.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_all_required_exercised(tmp_path):
    ok, message = skip_coverage.audit(
        _report(tmp_path, ["a", "b"]), minimum=2, required=("a", "b"))
    assert ok and "0 skipped" in message


def test_skipped_case_is_a_failure(tmp_path):
    ok, message = skip_coverage.audit(
        _report(tmp_path, ["a"], skipped=("a",)), minimum=1, required=("a",))
    assert not ok and "1 skipped" in message


def test_failed_or_missing_case_is_a_failure(tmp_path):
    ok, message = skip_coverage.audit(
        _report(tmp_path, ["a"], failures=("a",)), minimum=2, required=("a", "b"))
    assert not ok and "failed/error" in message and "missing" in message


def test_no_xml_is_a_failure(tmp_path):
    ok, message = skip_coverage.audit(tmp_path / "missing.xml", minimum=0, required=())
    assert not ok and "No parseable" in message
