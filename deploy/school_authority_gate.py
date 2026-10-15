#!/usr/bin/env python3
"""Does every teacher-facing write route file its rows under the school of *this* request?

A teacher can be an active member of more than one NPSN since migration 044, and
`app/utils/auth.py` resolves the school of every authenticated request from the
verified active membership. So there are now **two** schools a route can name for a
person: the one this request is acting in (`g.user_school_id`, the resolved active
school) and the one their profile names (`g.user_home_school_id`). For a teacher who
works at a second school those are different schools, and a row filed under the wrong
one is not a display bug: `school_id` scopes a paper's weighting, its pupils, its
marks and its reports, so a write that resolves it wrongly crosses a tenant boundary.

That is not hypothetical. This repository has shipped the same defect four times:

* `POST /exam/` took `school_id` straight out of the body — `filtered.get("school_id")
  or g.get("user_school_id")` — so a caller could file a paper into any school, member
  or not;
* `PUT /exam/<id>` kept the row's school while letting the same body move it, and the
  ownership branch skips the school comparison, so an owner could re-file their own
  paper;
* the exam builder wrote `g.user_school_id`, which *is* `profiles.school_id`, so a
  teacher working at a second NPSN filed papers — and their weighting and results —
  into their home school;
* `generate_remedial` did the same, so a remedial set followed the caller's profile
  rather than the school the source paper (and its results) live in.

Every one of those was found by hand, one at a time. This is the general check: a
teacher-facing write route may take its school from the request's active school and
from nowhere else, and where a body names a school it must go through
`filing_school_for_request`, which *refuses* a claim it cannot verify rather than
obeying it.

What counts as a teacher-facing write route
-------------------------------------------

A function behind a route decorator whose methods include a mutating one (`POST`,
`PUT`, `PATCH`, `DELETE`) and whose decorators include the teacher's own door
(`teacher_required`, `teacher_or_admin_required`, or `role_required` naming a teaching
role). The doors are the selector rather than a blueprint name on purpose: a route is
teacher-facing because a teacher can reach it. A school **admin**'s writes
(`admin_sekolah_required`) answer the same question but are a different audit — they
are the one role that is *not* cross-school — and `@require_school_access` is not a
door here, it is the evidence that a row's school is this caller's (see below).

What counts as resolving a school
---------------------------------

The expressions that *supply* a school to something the route writes or scopes:

* a value under the key `school_id` in a payload (`{"school_id": …}`,
  `payload["school_id"] = …`);
* the `school_id=` keyword of a call;
* the column argument of `.eq("school_id", …)` / `.neq(…)` / `.filter(…)`, which is
  what scopes a write to one school.

A payload that is built first and inserted later (`data = {"school_id": …}; …
.insert(data)`) is therefore read where it is **assembled**, which is the line that
resolves the school, rather than at the call that files it. What that leaves outside
the audit is a school that never carries the name `school_id` anywhere on its way to a
write — a bare service argument, say. There is no such call in this repository, and if
one appears this gate will not see it: the guard below pins the shapes that *are*
read, so the limit is stated rather than discovered later.

Each is traced to a source, and the sources are an explicit table rather than a guess:

* `filing_school_for_request(...)` — the authority, which also refuses;
* `g.get("user_school_id")` — the request's resolved active school;
* a module-level accessor whose body returns one of those (`def _school_id(): return
  g.get("user_school_id")`), inlined one level because that is the shape this
  repository writes these accessors in;
* a row read whose school `@require_school_access` has already checked, or the
  fallback shape `row.get("school_id") or g.get("user_school_id")`;
* `None` — the "there is nothing to repair" sentinel a guarded assignment leaves
  behind (`filing_school_id = None; if …: filing_school_id, _ = authority()`), which
  is the absence of a school rather than a second source for one.

Everything else is a finding, and the shapes worth naming are reported with their own
sentence because each is a different mistake: a school named by the request and not
passed through the authority; the *home* school, refused **anywhere** in a write route
because no write may use it; a school read from the session directly; a row's school
without the decorator that checks it; and a value the audit cannot trace at all — "we
could not tell where this school came from" is the honest finding, and printing it as
`untraceable` is the point rather than a gap.

Exit codes, like the other gates:

  0  every teacher-facing write route resolves its school from this request
  1  at least one does not: the release would ship a tenant boundary that is a guess
  2  could not measure — no route modules found, one that does not parse, or **not one
     teacher-facing write route**: a tree whose door decorators match nothing is a tree
     this check read nothing in, and reporting 0 findings over it is how a renamed
     decorator would turn a gate into a green light. Never a verdict on the release,
     and never a rollback. On a successful run the count is printed, because
     "0 findings" and "0 routes" are the two readings of the same silence.

Usage:
    python deploy/school_authority_gate.py            # check: 0 / 1 / 2
    python deploy/school_authority_gate.py --json     # machine-readable
    python deploy/school_authority_gate.py --tree DIR # another checkout
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

#: The methods that change something. A GET cannot file a row.
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

#: The doors a teacher walks through. `role_required` is here because it is how a route
#: says "any of these roles", and a teacher is one of them.
TEACHER_DOORS = {"teacher_required", "teacher_or_admin_required", "role_required"}

#: The one function that answers "which school is this request for?", returning
#: `(school_id, error)`. A route that accepts a school from the request must send it
#: here rather than use it.
AUTHORITY = "filing_school_for_request"

#: The decorator that proves a row's `school_id` is the caller's own school.
ROW_GUARD = "require_school_access"

#: The request's resolved active school — the only school a write may use directly.
ACTIVE_SCHOOL = "user_school_id"

#: The profile's school, which for a cross-school teacher is *not* the school they are
#: acting in. A write route never has a use for it.
HOME_SCHOOL = "user_home_school_id"

#: The keys that name a school in a payload or a query.
SCHOOL_KEYS = ("school_id", "school", "schoolId")

#: The kinds of source that are *not* the request's active school.
BAD_KINDS = {"request", "home", "session"}


class Finding(dict):
    def __str__(self) -> str:                      # pragma: no cover - formatting only
        return (f"{self['file']}:{self['line']} {self['function']} — {self['kind']}: "
                f"{self['detail']}")


def _name_of(node: ast.AST) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    for attr in ("id", "attr"):
        name = getattr(target, attr, None)
        if name:
            return name
    return ""


def _methods(decorator: ast.AST) -> set[str]:
    if not isinstance(decorator, ast.Call):
        return set()
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            return {e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant)}
    return set()


def _strings(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _source_of(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:                              # pragma: no cover - unparse is total
        return ""


def _is_teacher_door(decorator: ast.AST) -> bool:
    """Read off TEACHER_DOORS, so the list and the selector cannot drift apart."""
    name = _name_of(decorator)
    if name == "role_required":
        return any("guru" in value or "teacher" in value for value in _strings(decorator))
    return name in TEACHER_DOORS


def _is_mutating(decorator: ast.AST) -> bool:
    return bool(_methods(decorator) & MUTATING)


def _names_in(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def request_locals(func: ast.AST) -> set[str]:
    """Names whose value came out of the request, one step at a time.

    `data = request.get_json()` is obvious; `filtered = {k: v for k, v in data.items()
    if k in WRITABLE}` is one step further, and `filtered.get("school_id")` is the exact
    shape the old `POST /exam/` defect took. Without this the value is reported as a
    row read, which points the operator at the wrong fix — a row guard would never help
    a school that came from the client.
    """
    out: set[str] = set()
    for _ in range(4):                      # a small fixed point, not a chase
        grew = False
        for node in ast.walk(func):
            if isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value, targets = node.value, [node.target]
            else:
                continue
            # A value that *calls the authority* is not request-derived, even when it
            # passes the body in: `school_id, _ = filing_school_for_request(
            # requested_school_id=data.get("school_id"))` is the correct shape, and
            # marking its result as "named by the request" would flag it.
            if AUTHORITY in _source_of(value):
                continue
            from_request = "request." in _source_of(value) or bool(
                _names_in(value) & out)
            if not from_request:
                continue
            for target in targets:
                for name in _target_names(target):
                    if name not in out:
                        out.add(name)
                        grew = True
        if not grew:
            break
    return out


def _receiver_root(node: ast.AST) -> str:
    """The name an expression is read off: `data` in `data.get("school_id")`."""
    target = node.func if isinstance(node, ast.Call) else node
    target = getattr(target, "value", target)
    while not isinstance(target, ast.Name) and hasattr(target, "value"):
        target = target.value
    return target.id if isinstance(target, ast.Name) else ""


def _request_school(node: ast.AST, from_request: set[str] = frozenset()) -> bool:
    """`request.form.get("school_id")`, `data.get("school_id")` where data is the body."""
    if isinstance(node, ast.Subscript):
        return (any(k in SCHOOL_KEYS for k in _strings(node.slice))
                and _receiver_root(node) in from_request)
    if not isinstance(node, ast.Call):
        return False
    if not any(k in SCHOOL_KEYS for k in _strings(node)):
        return False
    if _source_of(node.func).startswith("request."):
        return True
    return bool(_receiver_root(node)) and _receiver_root(node) in from_request


def _row_read(node: ast.AST) -> bool:
    """`exam.get("school_id")` / `row["school_id"]` — a school read off a row."""
    if isinstance(node, ast.Call):
        return (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and any(k in SCHOOL_KEYS for k in _strings(node)))
    if isinstance(node, ast.Subscript):
        return any(k in SCHOOL_KEYS for k in _strings(node.slice))
    return False


def _accessors(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level functions that simply answer with a school.

    `def _school_id(): sid = g.get("user_school_id"); … return sid` is a resolver, not a
    second source, so a call to it is inlined. One level only: an accessor that calls
    another accessor is reported as untraceable rather than chased, because a chain of
    three is how "the audit cannot say" becomes "the audit says yes".
    """
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
        if not returns:
            continue
        kinds: set[str] = set()
        for statement in returns:
            kinds |= classify(statement.value, {})
        if kinds:
            out[node.name] = kinds
    return out


def classify(node: ast.AST, accessors: dict[str, set[str]],
             from_request: set[str] = frozenset()) -> set[str]:
    """What a school-valued expression is: a set of source kinds.

    A set rather than one value because `row.get("school_id") or g.get("user_school_id")`
    is one expression with two sources, and which one answers depends on the row.
    """
    if node is None:
        return {"unknown"}
    if isinstance(node, ast.Constant):
        # `None` is the "nothing to repair" sentinel, not a school.
        return {"none"}
    source = _source_of(node)
    if HOME_SCHOOL in source:
        return {"home"}
    if "session" in source and "school" in source:
        return {"session"}
    if _request_school(node, from_request):
        return {"request"}
    if isinstance(node, ast.Call) and _name_of(node) == AUTHORITY:
        return {"authority"}
    if isinstance(node, ast.Call) and _name_of(node) in accessors:
        return set(accessors[_name_of(node)])
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        kinds: set[str] = set()
        for value in node.values:
            kinds |= classify(value, accessors, from_request)
        return kinds
    if isinstance(node, ast.Name):
        return {"name"}
    if ACTIVE_SCHOOL in source:
        return {"active"}
    if _row_read(node):
        return {"row"}
    return {"unknown"}


def school_locals(func: ast.AST, accessors: dict[str, set[str]]) -> dict[str, set[str]]:
    """Local name -> the kinds of school it was assigned from."""
    out: dict[str, set[str]] = {}
    from_request = request_locals(func)
    for node in ast.walk(func):
        value = None
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        else:
            continue
        kinds = classify(value, accessors, from_request)
        for target in targets:
            for name in _target_names(target):
                out.setdefault(name, set()).update(kinds)
    return out


def _target_names(node: ast.AST):
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _target_names(element)


def school_uses(func: ast.AST) -> list[tuple[int, str, ast.AST]]:
    """Every expression that supplies a school, with its line and a short label."""
    uses: list[tuple[int, str, ast.AST]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in SCHOOL_KEYS:
                    uses.append((key.lineno, f'"{key.value}":', value))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and \
                        any(k in SCHOOL_KEYS for k in _strings(target.slice)):
                    uses.append((target.lineno, 'payload["school_id"] =', node.value))
        elif isinstance(node, ast.Call):
            name = _name_of(node)
            for kw in node.keywords:
                if kw.arg in SCHOOL_KEYS and name != AUTHORITY:
                    uses.append((kw.value.lineno, f"{kw.arg}=", kw.value))
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "eq", "neq", "filter", "match", "gte", "lte", "gt", "lt"):
                if node.args and any(k in SCHOOL_KEYS for k in _strings(node.args[0])):
                    for argument in node.args[1:2]:
                        uses.append((node.lineno, f'.{node.func.attr}("school_id")', argument))
    return uses


def audit_module(path: pathlib.Path) -> tuple[list[Finding], int]:
    """This module's findings, and how many teacher-facing write routes it holds.

    The count is the difference between "every write route resolves its school
    correctly" and "no write route was found, so there was nothing to check": a
    rename of the door decorator would leave this gate reporting green over a tree
    it never read, which is the failure mode the exit code below exists for.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    accessors = _accessors(tree)
    findings: list[Finding] = []
    examined = 0

    # The home school is refused wherever it appears in a write route: there is no
    # legitimate use for it on this side, and "used it somewhere I did not think to
    # trace" is exactly the failure this gate exists for.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == HOME_SCHOOL:
            findings.append(Finding(
                file=path.name, line=node.lineno, function=_enclosing(tree, node),
                kind="home school", detail=f"{_source_of(node)} is the profile's school, "
                     f"which for a teacher working at a second NPSN is not the school "
                     f"this request acts in"))
        elif isinstance(node, ast.Call) and any(
                k == HOME_SCHOOL for k in _strings(node)):
            findings.append(Finding(
                file=path.name, line=node.lineno, function=_enclosing(tree, node),
                kind="home school", detail=f"{_source_of(node)} reads the profile's "
                     f"school; a write must use this request's"))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = node.decorator_list
        if not any(_is_mutating(d) for d in decorators):
            continue
        if not any(_is_teacher_door(d) for d in decorators):
            continue
        examined += 1
        guarded_row = any(_name_of(d) == ROW_GUARD for d in decorators)
        locals_ = school_locals(node, accessors)
        from_request = request_locals(node)
        allowed = {"authority", "active", "none"} | ({"row"} if guarded_row else set())
        for line, label, expression in school_uses(node):
            kinds = (locals_.get(expression.id, {"unknown"})
                     if isinstance(expression, ast.Name)
                     else classify(expression, accessors, from_request))
            if "request" in kinds:
                findings.append(Finding(
                    file=path.name, line=line, function=node.name, kind="request school",
                    detail=f"{label} {_source_of(expression)} — a school named by the "
                           f"request must go to {AUTHORITY}(requested_school_id=…) and be "
                           f"refused if it cannot be verified, not used"))
            elif "home" in kinds:
                findings.append(Finding(
                    file=path.name, line=line, function=node.name, kind="home school",
                    detail=f"{label} {_source_of(expression)} — the profile's school is "
                           f"not the school this request acts in"))
            elif "session" in kinds:
                findings.append(Finding(
                    file=path.name, line=line, function=node.name, kind="session school",
                    detail=f"{label} {_source_of(expression)} — the school must come from "
                           f"the request's own resolution, not from the session"))
            elif "row" in kinds and not guarded_row and "active" not in kinds:
                findings.append(Finding(
                    file=path.name, line=line, function=node.name, kind="row school",
                    detail=f"{label} {_source_of(expression)} — a row's school is this "
                           f"caller's only when @{ROW_GUARD} has checked the row, or when "
                           f"the expression falls back to the active school"))
            elif kinds - allowed and "row" not in kinds:
                findings.append(Finding(
                    file=path.name, line=line, function=node.name, kind="untraceable",
                    detail=f"{label} {_source_of(expression)} — this school cannot be "
                           f"traced to the request's active school or to {AUTHORITY}()"))
        for argument in node.args.args + node.args.kwonlyargs:
            if argument.arg in ("school_id", "school"):
                findings.append(Finding(
                    file=path.name, line=node.lineno, function=node.name,
                    kind="parameter", detail="the school arrives as a parameter, so the "
                         "caller names it; the school is the server's to resolve"))
    return findings, examined


def _enclosing(tree: ast.Module, node: ast.AST) -> str:
    """The innermost function the node sits in, for a finding the reader can locate.

    Last match wins: `ast.walk` yields a nested `def` after the one that contains it,
    so the walk order is what makes the answer the inner function rather than the
    outer one.
    """
    best = ""
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                candidate.lineno <= node.lineno <= (candidate.end_lineno or candidate.lineno):
            best = candidate.name
    return best


def audit(root: pathlib.Path) -> tuple[list[Finding], int, int]:
    """Findings, the status, and how many teacher-facing write routes were read."""
    # No early return for an empty `app/routes/`: the caller refuses on `examined ==
    # 0` and names whichever of the two reasons applies, so there is one rule here
    # instead of two spellings of the same refusal — a second copy silently made a
    # mutation of this one an equivalent mutant, which is a guard nobody is testing.
    routes = root / "app" / "routes"
    # Recursive on purpose: the routes are flat today, and a blueprint moved into a
    # package below this directory would otherwise leave the gate reporting OK over a
    # file it never opened.
    modules = sorted(routes.rglob("*.py"))
    findings: list[Finding] = []
    examined = 0
    unreadable = 0
    for path in modules:
        try:
            found, count = audit_module(path)
        except SyntaxError:
            unreadable += 1
            continue
        findings.extend(found)
        examined += count
    return findings, (2 if unreadable else 0), examined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tree", default=str(pathlib.Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    root = pathlib.Path(args.tree).resolve()
    routes = root / "app" / "routes"
    findings, status, examined = audit(root)
    # Nothing to judge is not the same as nothing wrong: a repository whose routes are
    # still there but whose door decorator was renamed would otherwise pass this check
    # without a single route being read.
    if status == 0 and examined == 0:
        status = 2
    if status == 2:
        if any(routes.rglob("*.py")):
            print(f"could not measure: {routes} holds modules but not one teacher-facing "
                  f"write route — the door selector matches nothing, so this check "
                  f"would pass without reading a single route", file=sys.stderr)
        else:
            print(f"could not measure: no route modules under {routes}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"findings": findings, "examined": examined,
                          "exit": 1 if findings else 0}, indent=1))
        return 1 if findings else 0
    if findings:
        print(f"{len(findings)} place(s) in teacher-facing write routes resolve a school "
              f"from somewhere other than this request's active school:\n")
        for finding in findings:
            print(f"  {finding}")
        print(f"\nThe school of a write is the server's answer: {AUTHORITY}() for a "
              f"create, g.get(\"{ACTIVE_SCHOOL}\") for a row in this request's school.")
        return 1
    print(f"school authority: OK — {examined} teacher-facing write route(s) checked; "
          f"every one files its rows under the school of this request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
