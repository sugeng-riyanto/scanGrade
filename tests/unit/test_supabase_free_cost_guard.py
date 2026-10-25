"""Free-tier Supabase cost regression guard.

Existing broad queries are technical debt, not a reason to block production;
new broad queries in the shared query module must not silently increase cost.
This checks source code only: no database access, secrets, or billing changes.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]
QUERY_MODULE = ROOT / "app" / "models" / "supabase_queries.py"
# Audited main at 4e455df1 had 16 full-row projections. Lower this ceiling
# after replacing each with the smallest set of columns callers need.
LEGACY_STAR_PROJECTION_CEILING = 16


def _full_row_projections(source: str) -> list[int]:
    tree = ast.parse(source)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "select" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            # Includes embedded relations and the wildcard inside the projection.
            projections = [part.strip() for part in first.value.split(",")]
            if "*" in projections or first.value.strip() == "*":
                lines.append(node.lineno)
    return sorted(lines)


def test_shared_supabase_queries_must_not_increase_full_row_reads():
    lines = _full_row_projections(QUERY_MODULE.read_text(encoding="utf-8"))
    assert len(lines) <= LEGACY_STAR_PROJECTION_CEILING, (
        "Supabase Free egress regression: shared query module now has "
        f"{len(lines)} full-row select(*) projections (budget "
        f"{LEGACY_STAR_PROJECTION_CEILING}); lines: {lines}. "
        "Use explicit columns or reduce an existing wildcard before adding one."
    )


def test_egress_sensitive_autosave_and_exam_card_regressions_remain_tested():
    # These tests exercise real mocked PostgREST traffic. Removing them would
    # remove the guard against canvas echo and large exam-card payloads.
    source = (ROOT / "tests" / "unit" / "test_crud_egress.py").read_text(
        encoding="utf-8"
    )
    for protected_test in (
        "test_draft_writes_keep_answers_without_echoing_canvas",
        "test_exam_cards_request_metadata_and_keep_scope",
        "test_delete_exam_does_not_download_deleted_answers",
    ):
        assert f"def {protected_test}(" in source, (
            f"Supabase egress regression test was removed: {protected_test}"
        )


def test_cost_guard_policy_is_explicitly_documented():
    policy = (ROOT / "docs" / "SUPABASE_FREE_COST_POLICY.md").read_text(
        encoding="utf-8"
    )
    for required in ("Free", "egress", "autosave", "billing", "500"):
        assert required.lower() in policy.lower()
