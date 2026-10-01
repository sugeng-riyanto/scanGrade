"""An Alpine `x-for` clones exactly ONE root element, and an inert one clones nothing.

Alpine's `x-for` reads the template's first element child and repeats it. Two shapes
fail silently:

* **two roots** — a `<tr>` *and* a nested `<template>` beside it: Alpine takes the
  first and the second is ignored, or (with a bare wrapper `<template>`) the only
  root is the inert one;
* **an inert root** — a `<template>` wrapping the real rows: `<template>` renders
  nothing on its own, so Alpine faithfully clones "nothing" once per item. The page
  answers 200, no exception is raised, and the whole `<tbody>` stays empty.

`/admin-sekolah/teachers`' assignment matrix shipped exactly that. Its classes, its
cells and its `semua mapel` shortcut were all inside a `<template>` inside a
`<template x-for>`, so the matrix drew its subject columns and **no rows at all** —
reported as "widgetnya tidak muncul column or row". Every source-level test passed,
because they read the template for strings rather than rendering it, and nothing a
server sees can tell the difference: the failure only exists once a browser runs.

This file therefore reads every `x-for` in every template and refuses a root that is
not exactly one rendered element.
"""
from __future__ import annotations

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

#: An opening or closing tag, with its attributes (quoted `>` tolerated inside).
_TAG = re.compile(r"""<(/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>""")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
#: Bodies that are not markup: what they contain is not a cloned element.
_BLANKED = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I),
    re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I),
)


def blank_bodies(text: str) -> str:
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    for pattern in _BLANKED:
        text = pattern.sub(blank, text)
    return text


def xfor_roots(text: str) -> list[tuple[int, list[str]]]:
    """For each `<template x-for=...>`, the tag names of its DIRECT children."""
    text = blank_bodies(text)
    found = []
    for match in _TAG.finditer(text):
        if match.group(1) or match.group(2).lower() != "template":
            continue
        if "x-for" not in match.group(3):
            continue
        line = text.count("\n", 0, match.start()) + 1
        roots: list[str] = []
        stack: list[str] = []
        for tag in _TAG.finditer(text, match.end()):
            closing, name, attrs = tag.group(1), tag.group(2).lower(), tag.group(3)
            if closing:
                if not stack:
                    break  # the x-for template itself closed
                while stack:
                    if stack.pop() == name:
                        break
                continue
            if not stack:
                roots.append(name)  # a direct child of the x-for template
            if name not in _VOID and not attrs.rstrip().endswith("/"):
                stack.append(name)
        found.append((line, roots))
    return found


def test_every_xfor_template_clones_a_rendered_element():
    offenders = []
    for page in sorted(TEMPLATES.rglob("*.html")):
        text = page.read_text(encoding="utf-8", errors="replace")
        if "x-for" not in text:
            continue
        for line, roots in xfor_roots(text):
            if len(roots) != 1:
                offenders.append(
                    f"{page.relative_to(TEMPLATES)}:{line} has {len(roots)} root "
                    f"elements {roots} — Alpine clones only the first and ignores "
                    f"the rest")
            elif roots[0] == "template":
                offenders.append(
                    f"{page.relative_to(TEMPLATES)}:{line} roots on an inert "
                    f"<template>, which renders nothing: the loop produces no rows "
                    f"at all")
    assert not offenders, (
        "these Alpine x-for templates cannot render what they describe:\n  "
        + "\n  ".join(offenders))


def test_the_rule_bites_on_the_defect_it_describes():
    """Pointed at the real shape, because a guard that cannot fire is a comment."""
    # The matrix as it shipped: one root, but an inert <template>.
    broken = ('<tbody>\n'
              '  <template x-for="g in grades()" :key="g">\n'
              '    <template>\n'
              '      <tr class="bg-stone-100"><td x-text="g"></td></tr>\n'
              '      <template x-for="c in classesOf(g)" :key="c.id">\n'
              '        <tr><td x-text="c.name"></td></tr>\n'
              '      </template>\n'
              '    </template>\n'
              '  </template>\n'
              '</tbody>')
    roots = xfor_roots(broken)
    assert roots, "the scanner stopped seeing x-for templates"
    assert any(r == ["template"] for _line, r in roots), \
        "the inert-root signal stopped firing on the real defect"

    # …and the shape that replaced it, which is what keeps this honest.
    fixed = ('<tbody>\n'
             '  <template x-for="row in rows()" :key="row.key">\n'
             '    <tr>\n'
             '      <template x-if="row.cls"><td x-text="row.cls.name"></td></template>\n'
             '      <template x-for="s in subjects" :key="s.id"><td></td></template>\n'
             '    </tr>\n'
             '  </template>\n'
             '</tbody>')
    # The outer loop roots on a real <tr>; the inner ones root on <td>, which is
    # legal — a nested x-for is only a problem when its OWN root is inert.
    assert xfor_roots(fixed)[0][1] == ["tr"], xfor_roots(fixed)
