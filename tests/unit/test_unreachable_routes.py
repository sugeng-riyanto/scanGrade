"""No route function may contain code after an unconditional ``return``.

``/super-admin/demo-settings`` shipped as a page whose ``render_template`` call sat
*below* a bare ``return jsonify({"success": True})``. Python accepted it, the import
succeeded, and the route answered 200 — so every smoke test passed while the page
itself was unreachable and the form could never load.

A statement after a terminator is always a defect: either the route was meant to
render a page (dead code silently changed its behaviour) or the earlier return is a
mistake. This turns that class of bug into a test failure.
"""
import ast
import pathlib

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "routes"
TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _dead_after(block):
    """Statements that follow a terminator at this block's own level."""
    dead = []
    for i, node in enumerate(block):
        if isinstance(node, TERMINATORS) and i + 1 < len(block):
            dead.extend(block[i + 1:])
    return dead


def _collect(node, found, func=None):
    for field, value in ast.iter_fields(node):
        if isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
            for stmt in _dead_after(value):
                found.append((func, stmt))
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, ast.AST):
                name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
                _collect(child, found, name)


def _unreachable(path):
    found = []
    _collect(ast.parse(path.read_text(encoding="utf-8-sig")), found)
    return [
        f"{path.name}:{stmt.lineno}: {type(stmt).__name__} in {func}()"
        for func, stmt in found
    ]


def test_no_unreachable_code_in_routes():
    offenders = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        offenders.extend(_unreachable(path))

    assert not offenders, "unreachable statements hide route behaviour:\n  " + "\n  ".join(offenders)


def test_the_checker_itself_detects_the_demo_settings_shape():
    """Guard the guard: the exact code shape that caused the bug must be flagged."""
    src = (
        "def page():\n"
        "    if request.method == 'POST':\n"
        "        save()\n"
        "    return jsonify({'success': True})\n"
        "    current = load()\n"
        "    return render_template('page.html', current=current)\n"
    )
    found = []
    _collect(ast.parse(src), found)

    assert len(found) == 2, f"expected 2 unreachable statements, not {len(found)}"
