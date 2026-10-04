"""An operator must never be shown a library's raw text where a code will do.

`docs/ERROR_TEXT_AUDIT.md` records the decision for each class. This file holds the
last half of it: the *Remaining* inventory — the operator surfaces that still
interpolated `str(e)` / `{e}` straight into the response, so what the school admin or
the super admin read was PostgREST's JSON body (``{"code":"23505","message":"duplicate
key…"}``) instead of the sentence `failure.sentence` writes, which carries the code and
drops the developer's payload.

Operator surfaces here means the school admin, the super admin and the platform admin
routes — the people who act on a failure. The student/teacher conversation and
announcement endpoints are a different audience and are not in this file.

The rule is deliberately absolute rather than a per-site exception list: a new write
that stringifies its own exception would otherwise reintroduce the class, and the
next audit would have to find it again.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The operator-facing route modules. Every one already imports `failure`.
OPERATOR_FILES = [
    ROOT / "app" / "routes" / "admin.py",
    ROOT / "app" / "routes" / "admin_sekolah.py",
    ROOT / "app" / "routes" / "super_admin.py",
]

#: A raw exception interpolated into a response or flash. Deliberately narrow so it
#: does not catch a logger line (`logger.error(f"… {e}")` is for the log, not a reader)
#: nor a `getattr(e, "user_message", …)` fallback, which is the classified shape.
_RAW_IN_RESPONSE = re.compile(
    r"""(?:jsonify|flash|refuse|back)\s*\([^)]*"""
    r"""(?:str\(\s*(?:e|exc)\s*\)|[{]\s*(?:e|exc)\s*[}])""",
    re.DOTALL,
)


class TestNoOperatorSurfaceShowsRawExceptionText:
    def test_every_operator_route_module_is_covered(self):
        # If a module is renamed the scan would silently pass over nothing.
        for path in OPERATOR_FILES:
            assert path.exists(), f"{path.name} moved; this guard is now blind"
            assert "failure.sentence" in path.read_text(encoding="utf-8-sig"), (
                f"{path.name} no longer uses the shared sentence helper")

    def test_no_raw_exception_reaches_an_operator_response(self):
        offenders = []
        for path in OPERATOR_FILES:
            src = path.read_text(encoding="utf-8-sig")
            for match in _RAW_IN_RESPONSE.finditer(src):
                line = src[:match.start()].count("\n") + 1
                snippet = " ".join(match.group(0).split())[:90]
                offenders.append(f"{path.name}:{line}  {snippet}")
        assert not offenders, (
            "these operator responses interpolate the raw exception instead of "
            "failure.sentence, so the reader gets the library's JSON body rather than "
            "the Postgres code:\n  " + "\n  ".join(offenders))

    def test_the_guard_actually_sees_a_raw_interpolation(self):
        # The pattern must be able to fail, or the test above proves nothing.
        probe = 'return jsonify({"error": str(e)}), 400\n'
        assert _RAW_IN_RESPONSE.search(probe)
        probe2 = 'flash(f"Gagal: {e}", "error")\n'
        assert _RAW_IN_RESPONSE.search(probe2)
        # …and must not flag the classified shape or a log line.
        assert not _RAW_IN_RESPONSE.search('jsonify({"error": failure.sentence(e)})')
        assert not _RAW_IN_RESPONSE.search('logger.error(f"boom: {e}")')


class TestTheCodeTravels:
    def test_a_postgrest_refusal_names_its_code(self):
        from postgrest.exceptions import APIError

        from app.utils import failure

        exc = APIError({"code": "23505", "message": "duplicate key value violates "
                                                     "unique constraint",
                        "details": None, "hint": None})
        sentence = failure.sentence(exc)
        assert "23505" in sentence, "the Postgres code did not travel"
        assert "duplicate key value violates" not in sentence, (
            "the library's JSON body reached the reader")
