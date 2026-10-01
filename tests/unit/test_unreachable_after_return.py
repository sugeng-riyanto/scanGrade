"""No statement may sit behind a `return` in the same block.

This file exists because the same defect shipped twice, in two different features,
and neither was visible in review:

* `_activate_subscription` built a subscription invoice and ended with
  `return code`; the `_generate_invoice(...)` call and the "activated" log line sat
  **after** it, so no real activation ever wrote a receipt — production held only
  demo fixtures.
* `_test_key_internal` had a quota/rate-limit message **after** its generic error
  return, so a rate-limited API key told the teacher "Gagal" and the "wait a
  while" advice the branch was written for never reached anyone.

Both are the kind of dead code a linter can be argued out of ("maybe it is
deliberate"), and both were reached only because a *phase audit* looked for this
exact shape. So the audit becomes a check: any statement that can never execute
because the block already returned is a finding.

Scope: the shipped code under `app/` and `deploy/`, not the tests (a test may
deliberately park a statement). The rule is structural — statements in the same
`body` list, after a `Return`/`Raise`/`Continue`/`Break` — so a return inside a
nested `if` or `try` does not flag its siblings.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "app", ROOT / "deploy")

TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def unreachable_in(source: str):
    """`[(lineno, kinds)]` — statements after a terminator in the same block."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if isinstance(stmt, TERMINATORS) and i != len(body) - 1:
                rest = body[i + 1:]
                if rest:
                    hits.append((stmt.lineno, [type(s).__name__ for s in rest]))
    return hits


def shipped_files():
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")):
            yield path


class TestNoStatementIsStrandedBehindAReturn:
    def test_the_scanner_catches_the_shape(self):
        """A guard on the guard: the audit must fail on the defect it hunts."""
        src = "def f():\n    return 1\n    x = 2\n"
        assert unreachable_in(src), "the scanner does not see dead code after a return"

    def test_the_scanner_ignores_a_return_that_ends_its_block(self):
        src = "def f():\n    if x:\n        return 1\n    return 2\n"
        assert unreachable_in(src) == []

    def test_no_shipped_module_has_unreachable_code(self):
        failures = []
        for path in shipped_files():
            try:
                # utf-8-sig, because at least one shipped module carries a BOM
                # and `ast.parse` refuses a leading U+FEFF.
                source = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            for lineno, kinds in unreachable_in(source):
                failures.append(f"{path.relative_to(ROOT)}:{lineno} -> {kinds}")
        assert not failures, (
            "these statements can never run because their block already "
            "returned/raised — a side effect behind a return is how the "
            "activation invoice and the quota message went missing:\n  "
            + "\n  ".join(failures))
