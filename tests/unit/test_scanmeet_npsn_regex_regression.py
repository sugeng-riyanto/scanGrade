"""Regression: Super Admin NPSN lookup accepts exactly eight ASCII digits.

Extract the pattern from the actual route AST, preventing a test that merely
duplicates a correct constant while production still uses the wrong regex.
"""
import ast
import re
from pathlib import Path

import pytest


def _route_npsn_pattern():
    source = Path(__file__).resolve().parents[2] / "app/routes/super_admin.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    route = next(node for node in ast.walk(module)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "scanmeet_school_licenses")
    matches = [
        node for node in ast.walk(route)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.func.attr == "fullmatch"
    ]
    assert len(matches) == 1, "Expected a single NPSN fullmatch gate"
    return ast.literal_eval(matches[0].args[0])


@pytest.mark.parametrize("npsn", ["12345678", "00000001", "98765432"])
def test_npsn_eight_digits_are_accepted(npsn):
    assert re.fullmatch(_route_npsn_pattern(), npsn) is not None


@pytest.mark.parametrize("npsn", ["", "1234567", "123456789", "1234abcd",
                                    "1234 678", "１２３４５６７８", "1234\\d{8}"])
def test_invalid_npsn_is_rejected(npsn):
    assert re.fullmatch(_route_npsn_pattern(), npsn) is None
