"""A route that names a service needs that service in the commit that carries it.

Measured shape of the defect, and why the suite did not see it: ``app/routes/
admin_sekolah.py`` grew an ``from app.services import <new_service>`` while the
service itself was still untracked. Every test in this suite passed on the
machine that wrote it — the module was *on disk*, so the import resolved. The
file that shipped named a module the release did not contain, so the box's
``gunicorn`` worker died at boot: not a page that 500s, the process that never
starts, which reads as a 502 for every route at once and names nothing.

The guard therefore asks git, not the filesystem. ``git ls-files`` answers from
the index, and the index is exactly what a partial commit would carry — staging
the route alone is the commit that must be refused. A module staged beside its
route is fine and passes, because it is in the release being prepared.

Two resolutions matter, and getting either wrong makes the guard vacuous:

* ``from app.services import new_service`` is a **submodule** import when
  ``app/services/new_service.py`` exists (the measured case), and only then does
  the submodule itself have to be tracked. When no such file exists the name is
  an attribute of the package, and the package's own ``__init__.py`` is the
  thing that must be committed. Treating the package as enough in both cases
  would have passed the very commit this file exists to refuse.
* An import of something that exists in neither the index nor the tree is
  reported too. It is the same release outcome — an app that cannot import —
  and a guard that only asked about tracking would call a typo "not our
  problem".
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

from tests.unit.git_env import git

ROOT = Path(__file__).resolve().parents[2]
ROUTES_DIR = ROOT / "app" / "routes"
#: The file whose partial commit was measured. Named in its own test so a failure
#: reads as "the file this happened to" rather than as a filesystem error.
MEASURED_ROUTE = ROUTES_DIR / "admin_sekolah.py"


def _tracked(repo: Path) -> set[str]:
    """Every path in the index under ``app/``, repo-relative.

    ``-z`` because a path can hold anything git allows; splitting on newlines
    would read such a name as two paths and pass a commit that carries neither.
    """
    out = git("ls-files", "-z", "--", "app", cwd=repo)
    return {name for name in out.stdout.split("\0") if name}


def _module_paths(dotted: str) -> list[str]:
    """The two files a dotted module can be: a module, or a package."""
    base = dotted.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


def _package_of(route: Path) -> str:
    """`app.routes.admin_sekolah` → `app.routes`, for a relative import.

    Read from the path's own parts rather than from this checkout's root: the
    guard is handed routes in a scratch repository by the tests below, and
    resolving those against ``ROOT`` is an error rather than an answer.
    """
    parts = list(route.with_suffix("").parts)
    start = parts.index("app") if "app" in parts else 0
    return ".".join(parts[start:-1])


class Named(NamedTuple):
    """One name a route imports, and what would have to be committed for it.

    ``attribute`` is set only for ``from package import name`` where no
    ``package/name.py`` exists: Python then reads the name off the package's own
    module, so the package's ``__init__.py`` is what has to be committed *and*
    define the name. Without that second half a typo would be waved through as
    "an attribute" and the import would still be the one that kills the worker.
    """
    label: str
    paths: list[str]
    attribute: str | None = None


def _defined_in(path: Path, name: str) -> bool:
    """Whether a package's ``__init__`` defines (or imports) ``name`` at its top.

    Anything unreadable is answered ``True``: this guard exists to refuse a tree
    that cannot import, and inventing a refusal out of a file it could not parse
    would be a different, worse tool.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError):
        return True
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            return True
        if isinstance(node, ast.Assign):
            # `A, B, C = "a", "b", "c"` is one statement and three names; reading
            # only a bare Name target called `KEY_STALE` undefined.
            for target in node.targets:
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name) and leaf.id == name:
                        return True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return True
        if isinstance(node, ast.ImportFrom) and any(
                alias.name == name or alias.asname == name for alias in node.names):
            return True
        if isinstance(node, ast.Import) and any(
                (alias.asname or alias.name.split(".")[0]) == name
                for alias in node.names):
            return True
    return False


def imports_named(source: str, route: Path = MEASURED_ROUTE,
                  repo: Path = ROOT) -> list[Named]:
    """Every ``app.*`` import this file makes, with the files that satisfy it.

    The candidates are the paths that *could* satisfy the import; the caller
    decides whether one is in the index. Function bodies are walked as well as
    the module top: a deferred import inside a view is still an import the
    release has to be able to run, and it is the shape most likely to be added
    in a hurry and forgotten in ``git add``.
    """
    tree = ast.parse(source, filename=str(route))
    found: list[Named] = []
    package = _package_of(route)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    found.append(Named(alias.name, _module_paths(alias.name)))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            base = package
            if node.level > 1:
                base = ".".join(package.split(".")[: -(node.level - 1)])
            module = f"{base}.{node.module}" if node.module else base
        elif node.module and (node.module == "app" or node.module.startswith("app.")):
            module = node.module
        else:
            continue

        base_paths = _module_paths(module)
        for alias in node.names:
            if alias.name == "*":
                found.append(Named(module, base_paths))
                continue
            submodule = f"{module}.{alias.name}"
            sub_paths = _module_paths(submodule)
            # A file either on disk or in the index makes this a submodule import;
            # otherwise the name is an attribute the package's __init__ provides.
            exists = any((repo / p).exists() for p in sub_paths)
            found.append(Named(submodule, sub_paths) if exists else
                         Named(submodule, base_paths, alias.name))
    return found


def untracked_imports(route: Path, repo: Path = ROOT,
                      tracked: set[str] | None = None) -> list[str]:
    """The imports of ``route`` that the index does not carry, named with a path.

    An untracked module, a module that is nowhere, and a package attribute the
    package does not define are all returned, and each line says which it is —
    the fixes differ (``git add``, writing the file, correcting the name), and a
    report that said only "missing" would send an operator to the wrong one.
    """
    tracked = _tracked(repo) if tracked is None else tracked
    problems = []
    for item in imports_named(route.read_text(encoding="utf-8-sig"), route, repo):
        # The attribute case first: its paths are the package's, and the package
        # being tracked is the question *after* whether it provides the name.
        if item.attribute:
            # The *file* that provides the package: `app/utils/auth.py` and
            # `app/utils/auth/__init__.py` are two different answers, and only
            # one of them is the module the import reads.
            provider = next((path for path in item.paths if path in tracked), None)
            if provider is None:
                problems.append(
                    f"{item.label}: the package is not in the index "
                    f"(neither {' nor '.join(item.paths)})")
            elif not _defined_in(repo / provider, item.attribute):
                problems.append(
                    f"{item.label}: {provider} is committed but defines no "
                    f"`{item.attribute}`")
            continue
        if any(path in tracked for path in item.paths):
            continue
        if any((repo / path).exists() for path in item.paths):
            problems.append(f"{item.label}: {item.paths[0]} is on disk but not in the index")
        else:
            problems.append(f"{item.label}: neither {' nor '.join(item.paths)} exists")
    return problems


def _git_answers(repo: Path) -> bool:
    """Whether this checkout can answer a tracking question at all."""
    out = git("rev-parse", "--is-inside-work-tree", cwd=repo)
    return out.returncode == 0 and out.stdout.strip() == "true"


def _commit(repo: Path, message: str = "fixture") -> None:
    git("-c", "user.name=T", "-c", "user.email=t@example.com",
        "commit", "-q", "-m", message, cwd=repo, check=True)


def _checkout(tmp_path: Path) -> Path:
    """A real git checkout holding one tracked module and one tracked route."""
    repo = tmp_path / "repo"
    (repo / "app" / "routes").mkdir(parents=True)
    (repo / "app" / "services").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "routes" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "services" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "services" / "kept.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "app" / "routes" / "admin_sekolah.py").write_text(
        "from app.services import kept\n", encoding="utf-8")
    git("init", "-q", str(repo))
    git("add", "-A", cwd=repo, check=True)
    _commit(repo)
    return repo


class TestTheMeasuredRoute:
    def test_admin_sekolah_imports_only_modules_its_commit_carries(self):
        assert _git_answers(ROOT), (
            "this checkout cannot answer what is committed, so the guard below "
            "would pass by reading nothing")
        problems = untracked_imports(MEASURED_ROUTE)
        assert problems == [], (
            "these imports are not in the commit that would carry "
            f"app/routes/admin_sekolah.py, so the box's worker cannot start: {problems}")

    def test_every_route_is_held_to_the_same_standard(self):
        """The same accident in another route is the same release outcome."""
        assert _git_answers(ROOT)
        tracked = _tracked(ROOT)
        problems = {
            route.relative_to(ROOT).as_posix(): untracked_imports(route, ROOT, tracked)
            for route in sorted(ROUTES_DIR.glob("*.py"))
        }
        offenders = {name: found for name, found in problems.items() if found}
        assert offenders == {}, (
            "a route names a module the release would not contain: " + repr(offenders))


class TestThePartialCommit:
    def test_a_service_left_out_of_the_index_is_named(self, tmp_path):
        """The measured commit: the route staged, the service only on disk."""
        repo = _checkout(tmp_path)
        (repo / "app" / "services" / "new_service.py").write_text(
            "VALUE = 2\n", encoding="utf-8")
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text("from app.services import new_service\n", encoding="utf-8")
        git("add", "app/routes/admin_sekolah.py", cwd=repo, check=True)

        problems = untracked_imports(route, repo)
        assert len(problems) == 1, problems
        assert "app.services.new_service" in problems[0]
        assert "not in the index" in problems[0], (
            "the report does not say the file exists but was not committed: "
            + problems[0])

    def test_staging_the_service_beside_the_route_clears_it(self, tmp_path):
        """A commit is allowed to add both; the guard is about the tree, not fear."""
        repo = _checkout(tmp_path)
        (repo / "app" / "services" / "new_service.py").write_text(
            "VALUE = 2\n", encoding="utf-8")
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text("from app.services import new_service\n", encoding="utf-8")
        git("add", "-A", cwd=repo, check=True)

        assert untracked_imports(route, repo) == []

    def test_a_service_deleted_from_the_index_while_on_disk_is_named(self, tmp_path):
        """``git rm --cached`` is the same release as never adding it."""
        repo = _checkout(tmp_path)
        git("rm", "-q", "--cached", "app/services/kept.py", cwd=repo, check=True)
        route = repo / "app" / "routes" / "admin_sekolah.py"
        problems = untracked_imports(route, repo)
        assert problems and "app.services.kept" in problems[0], problems


class TestWhatCountsAsAnImport:
    def test_an_attribute_of_a_tracked_package_is_not_a_missing_submodule(self, tmp_path):
        """``from app.services import helper`` where `helper` is a function."""
        repo = _checkout(tmp_path)
        (repo / "app" / "services" / "__init__.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8")
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text("from app.services import helper\n", encoding="utf-8")
        git("add", "-A", cwd=repo, check=True)
        assert untracked_imports(route, repo) == []

    def test_a_deferred_import_inside_a_view_is_read(self, tmp_path):
        """A function-local import ships with the release and can still be absent."""
        repo = _checkout(tmp_path)
        (repo / "app" / "services" / "ghost.py").write_text(
            "VALUE = 3\n", encoding="utf-8")
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text(
            "def view():\n"
            "    from app.services import ghost\n"
            "    return ghost\n", encoding="utf-8")
        problems = untracked_imports(route, repo)
        assert problems and "app.services.ghost" in problems[0], problems
        assert "not in the index" in problems[0], problems

    def test_an_import_that_is_nowhere_says_so_rather_than_calling_it_untracked(
            self, tmp_path):
        repo = _checkout(tmp_path)
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text("import app.services.ghost\n", encoding="utf-8")
        problems = untracked_imports(route, repo)
        assert problems and "exists" in problems[0], problems
        assert "index" not in problems[0], (
            "a module that was never written is not a staging mistake: "
            + problems[0])

    def test_a_package_that_does_not_define_the_name_is_a_refusal(self, tmp_path):
        """A typo in the attribute is the same release as a missing module."""
        repo = _checkout(tmp_path)
        route = repo / "app" / "routes" / "admin_sekolah.py"
        route.write_text("from app.services import keptt\n", encoding="utf-8")
        problems = untracked_imports(route, repo)
        assert problems and "defines no `keptt`" in problems[0], problems


class TestTheDeployGateRunsIt:
    """A guard nobody runs is a comment; the gate has to carry this file."""

    def test_the_theme_gate_runs_this_file(self):
        gate = (ROOT / "deploy" / "theme_gate.sh").read_text(encoding="utf-8")
        assert "tests/unit/test_route_imports_are_committed.py" in gate, (
            "the release gate does not run the committed-imports guard, so a "
            "partial commit reaches the box and dies there instead")
