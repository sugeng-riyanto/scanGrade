"""No account-creation path may leave an auth user behind.

An account is written in an order the foreign keys force — ``auth user ->
profiles -> (students|teachers)`` — and the *last* write is the one the database
can reject. So any failure leaves an auth user plus a profile with no role row:
an account that can sign in and belongs to nothing.

Every path that creates accounts has now been closed, and the load-bearing test
here is the last one: it walks the source and requires each ``create_user`` call
to be undone by its own function. A behavioural test only covers the call sites
that exist today; the walk is what catches the next importer, route, or seed
helper copied without the rollback.

`manage.py` gets its own tests because it is the one written like a script rather
than a service: it prints to the operator and returns ``None`` instead of raising,
and it recovers an existing account by email when the address is taken. That last
part is why its rollback has to know whether *this call* created the account —
deleting a recovered, pre-existing account on a transient failure would destroy
somebody's real login.

The sweep is deliberately stricter than "there is an undo somewhere in the
function": the undo has to be reachable from the branch that handles the failure.
A ``create_user`` whose ``try`` then does more work that can raise must undo in
*every* handler of that ``try``, because any of them can be the branch that runs
after the account already exists. Pulling the call out of the handler -- which is
what a refactor that "tidies up" the rollback looks like -- is exactly what the
weaker rule accepted.
"""
import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ── fakes for manage.py's seeding helper ─────────────────────────────────────

class _Resp:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self.payload = None

    def upsert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        # Recorded before the refusal, so a test can prove the write was actually
        # reached instead of passing because the helper bailed out earlier.
        self.store.attempted.append(self.table)
        if self.table in self.store.fail:
            raise RuntimeError(f"permission denied for table {self.table}")
        self.store.tables.setdefault(self.table, []).append(dict(self.payload))
        return _Resp([self.payload])


class _User:
    def __init__(self, uid, email):
        self.id = uid
        self.email = email


class _Admin:
    def __init__(self, store):
        self.store = store

    def create_user(self, attributes):
        email = attributes["email"]
        if email in self.store.existing:
            raise Exception("User already registered")
        uid = f"user-{len(self.store.created) + 1}"
        self.store.created.append({"id": uid, **attributes})
        return type("Created", (), {"user": type("U", (), {"id": uid})()})()

    def list_users(self):
        # `existing` maps email -> uid. Getting these the wrong way round made the
        # recovery loop miss, so the helper returned early and the test below
        # passed without ever reaching the write it was supposed to check.
        return [_User(uid, email) for email, uid in self.store.existing.items()]

    def delete_user(self, uid, should_soft_delete=False):
        self.store.deleted.append(uid)
        for rows in self.store.tables.values():
            rows[:] = [r for r in rows if r.get("id") != uid]


class FakeSupabase:
    def __init__(self, fail=(), existing=None):
        self.tables = {}
        self.created = []
        self.deleted = []
        self.attempted = []
        self.fail = set(fail)
        self.existing = dict(existing or {})
        self.auth = type("Auth", (), {"admin": _Admin(self)})()

    def table(self, name):
        return _Query(self, name)


@pytest.fixture(scope="module")
def manage():
    """`manage.py` builds a Flask app at import time, so import it once."""
    import manage as manage_module
    return manage_module


def _payload(role="murid", email="budi@sekolah.id", **extra):
    data = {"email": email, "password": "pw", "role": role, "full_name": "Budi"}
    data.update(extra)
    return data


# ── manage.py: the rollback ──────────────────────────────────────────────────

def test_a_failed_profile_write_undoes_the_new_account(manage):
    fake = FakeSupabase(fail=["profiles"])

    uid = manage._create_user(fake, _payload(), school_id="s1")

    assert uid is None
    assert "profiles" in fake.attempted
    assert len(fake.created) == 1, "the auth user was created first"
    assert fake.deleted == [fake.created[0]["id"]], "it must be undone"
    assert not fake.tables.get("profiles")


def test_a_failed_role_row_undoes_the_new_account(manage):
    fake = FakeSupabase(fail=["students"])

    uid = manage._create_user(fake, _payload(), school_id="s1", class_id="c1")

    assert uid is None
    assert fake.deleted == [fake.created[0]["id"]]
    assert "profiles" in fake.tables, "the profile was written before the failure"


def test_a_failed_teachers_row_undoes_the_new_account(manage):
    fake = FakeSupabase(fail=["teachers"])

    uid = manage._create_user(fake, _payload(role="guru"), school_id="s1")

    assert uid is None
    assert fake.deleted == [fake.created[0]["id"]]


def test_an_account_that_already_existed_is_never_deleted(manage):
    """The seed is re-runnable, and a re-run recovers the uid by email. Deleting
    that on a failure would remove a real account, not a half-made one."""
    fake = FakeSupabase(fail=["profiles"], existing={"budi@sekolah.id": "existing-uid"})

    uid = manage._create_user(fake, _payload(), school_id="s1")

    assert uid is None
    assert fake.created == [], "no new auth user was made"
    assert "profiles" in fake.attempted, (
        "the recovery path must actually reach the failing write, or this test "
        "passes for the wrong reason")
    assert fake.deleted == [], "the pre-existing account must survive"


def test_a_clean_seed_writes_all_three_rows(manage):
    fake = FakeSupabase()

    uid = manage._create_user(fake, _payload(nisn="12345678"), school_id="s1",
                             class_id="c1")

    assert uid == fake.created[0]["id"]
    assert fake.tables["profiles"][0]["school_id"] == "s1"
    assert fake.tables["profiles"][0]["class_id"] == "c1"
    assert fake.tables["students"][0]["school_id"] == "s1"
    assert fake.tables["students"][0]["nisn"] == "12345678"
    assert fake.deleted == []


def test_a_dead_address_reports_instead_of_deleting(manage):
    """`create_user` refusing for a reason other than "exists" means no account
    was made, so there is nothing to undo."""
    class AlwaysRefuses(FakeSupabase):
        pass

    fake = AlwaysRefuses()
    fake.auth.admin.create_user = lambda attributes: (_ for _ in ()).throw(
        RuntimeError("email address is invalid"))

    assert manage._create_user(fake, _payload(), school_id="s1") is None
    assert fake.deleted == []


# ── the sweep, kept as a test so it cannot drift back ────────────────────────

HELPERS = {"create_student_account", "create_teacher_account"}
UNDO_WORDS = ("discard", "delete_user", "rollback", "undo")
# The rows that make up an account: the auth user is written first, then these.
ACCOUNT_TABLES = {"profiles", "students", "teachers"}


def _name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return getattr(call.func, "id", "")


def _calls_within(node):
    """Every `Call` in `node` -- but not one inside a nested scope.

    Staying out of nested functions matters in both directions: a helper defined
    inside the body would contribute calls that never run, and an undo written
    inside one is not a call the handler makes.
    """
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(current, ast.Call):
            found.append(current)
        stack.extend(ast.iter_child_nodes(current))
    return found


def _undo_in(node) -> str:
    """The name of the first undo call inside `node`, or `""`."""
    for call in _calls_within(node):
        name = _name(call)
        if any(word in name for word in UNDO_WORDS):
            return name
    return ""


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _nearest(node, parents, keep):
    """The nearest ancestor matching `keep`, paired with the child on the path."""
    child, parent = node, parents.get(node)
    while parent is not None:
        if keep(parent):
            return parent, child
        child, parent = parent, parents.get(parent)
    return None, None


def _is_function(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _is_try(node):
    return isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try)))


def _enclosing_try(node, parents):
    """The innermost `try` that *covers* `node`, plus the body statement on the
    path to it.

    "Covers" means down through ``body``: a call sitting in an ``except`` handler
    is not covered by the `try` it belongs to, so it does not satisfy that try's
    own rule.
    """
    child, parent = node, parents.get(node)
    while parent is not None:
        if _is_try(parent) and any(stmt is child for stmt in parent.body):
            return parent, child
        child, parent = parent, parents.get(parent)
    return None, None


def _label(handler) -> str:
    return "bare except" if handler.type is None else f"except {ast.unparse(handler.type)}"


def _tries_within(node):
    """Every `try` inside `node`, without descending into a nested scope."""
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        if current is not node and isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Lambda)):
            continue
        if _is_try(current):
            found.append(current)
        stack.extend(ast.iter_child_nodes(current))
    return found


def _account_writes(stmt):
    """Account tables `stmt` *establishes a row in*, i.e. through insert/upsert.

    An `update` is deliberately not counted: the row already exists, so failing
    one cannot leave the account half-made. Without that distinction the optional
    profile updates in `auth.py:register` -- which swallow their own errors on
    purpose -- would be flagged forever.

    A `table(...)` whose argument is not a literal is treated as one of ours: a
    miss here costs an orphaned account, while a false alarm only costs a look.
    """
    tables, establishes, unresolved = [], False, False
    for call in _calls_within(stmt):
        name = _name(call)
        if name in ("insert", "upsert"):
            establishes = True
        if name != "table" or not call.args:
            continue
        arg = call.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            unresolved = True
        elif arg.value in ACCOUNT_TABLES:
            tables.append(arg.value)
    if not establishes:
        return []
    if unresolved:
        tables.append("<unknown table>")
    return tables


def _audit_create(node, parents):
    """`(ok, reason)` for one `create_user` call."""
    func, _ = _nearest(node, parents, _is_function)
    if func is None:
        return False, "not inside a function, so nothing owns the rollback"

    for call in _calls_within(func):
        if _name(call) in HELPERS:
            return True, f"goes through {_name(call)}(), which owns the rollback"

    try_node, owner = _enclosing_try(node, parents)
    if try_node is None:
        return False, "not inside a try, so a failed write has no branch to undo in"

    # Anything running after the account exists can fail and orphan it. Every
    # call counts, not just the obvious writes: being wrong here costs an account
    # that can sign in and belongs to nothing, so the rule is blunt on purpose.
    body = list(try_node.body)
    after = body[body.index(owner) + 1:]
    fallible = [stmt for stmt in after
                if any(call is not node for call in _calls_within(stmt))]

    if fallible:
        if not try_node.handlers:
            return False, (f"{len(fallible)} statement(s) run after the account is "
                           f"made, but the try has no except to undo in")
        blind = [h for h in try_node.handlers if not _undo_in(h)]
        if blind:
            return False, (f"{len(fallible)} statement(s) run after the account is "
                           f"made, but "
                           + ", ".join(_label(h) for h in blind) + " does not undo")
    else:
        # Nothing here can fail after the account is made, so *this* try owes no
        # undo: the only failure it can see is `create_user`'s own, and that made
        # no account. The tries that follow still have to clean up.
        fallible = []

    # The account's own rows are what complete it, and they are not always written
    # in this try: `auth.py` writes the profile in a second try, and `manage.py`
    # gives each row its own. Whichever branch handles such a write runs while the
    # account already exists, so it has to undo. Writes to anything *else* are out
    # of scope on purpose -- their rollback is a separate product decision (the
    # registration request in `auth.py` is deliberately kept).
    later = []
    for other in _tries_within(func):
        if other is try_node or other.lineno <= node.lineno:
            continue
        writes = [table for stmt in other.body for table in _account_writes(stmt)]
        if not writes:
            continue
        if not other.handlers:
            return False, (f"the {writes[0]} write that follows the create has no "
                           f"except to undo in")
        blind = [h for h in other.handlers if not _undo_in(h)]
        if blind:
            return False, (f"the {writes[0]} write runs after the account is made, "
                           f"but " + ", ".join(_label(h) for h in blind)
                           + " does not undo")
        later.append((writes[0], _undo_in(other.handlers[0])))

    if fallible:
        covered = (f"every except of the enclosing try undoes "
                   f"({len(try_node.handlers)} handler(s))")
        if later:
            covered += f", and so does the later {later[0][0]} write"
        return True, covered
    if later:
        return True, (f"nothing can fail after the create here; {later[0][1]}() "
                      f"undoes in a later handler")
    return False, "the account can be made, but nothing later undoes it"


@dataclass
class Site:
    path: str
    lineno: int
    func: str
    ok: bool
    reason: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno} in {self.func}()"


def audit_create_user_sites():
    sites = []
    for path in sorted(Path(ROOT / "app").rglob("*.py")) + [ROOT / "manage.py"]:
        if "test" in path.name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        parents = _parent_map(tree)
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _name(node) == "create_user"):
                continue
            func, _ = _nearest(node, parents, _is_function)
            ok, reason = _audit_create(node, parents)
            sites.append(Site(rel, node.lineno, func.name if func else "<module>",
                              ok, reason))
    return sites


def _audit_snippet(source: str):
    """Audit a `create_user` written inline, so the rule can be pinned to its
    behaviour instead of to today's files."""
    tree = ast.parse(textwrap.dedent(source))
    parents = _parent_map(tree)
    found = [_audit_create(node, parents) for node in ast.walk(tree)
             if isinstance(node, ast.Call) and _name(node) == "create_user"]
    assert len(found) == 1, f"the snippet needs exactly one create_user, saw {len(found)}"
    return found[0]


def test_no_create_user_site_can_leave_an_account_behind():
    sites = audit_create_user_sites()
    offenders = [f"{s.location} -- {s.reason}" for s in sites if not s.ok]

    assert len(sites) >= 6, f"expected to find the known call sites, saw {len(sites)}"
    assert not offenders, (
        "these can make an auth user and then fail with no undo in the branch that "
        "handles the failure, leaving an account that can sign in and belongs to "
        "nothing:\n  " + "\n  ".join(offenders))


def test_every_site_states_how_it_is_covered():
    for site in audit_create_user_sites():
        assert site.ok and site.reason, f"{site.location} has no stated coverage"


def test_only_the_two_known_sites_ride_the_no_fallible_work_exemption():
    """The exemption is the one place this check can be fooled, so pin it down.

    Two sites put `create_user` in a try whose body then does nothing that can
    raise: `auth.py:register` (the profile write is a second try) and
    `manage.py:_create_user` (each write has its own try). Those tries cannot be
    the ones that orphan an account, so demanding an undo in their handlers would
    be a false positive. A *new* site landing here is worth a look, and this test
    is what makes somebody look -- in either direction.
    """
    exempt = {(s.path, s.func) for s in audit_create_user_sites()
              if "nothing can fail after the create" in s.reason}

    assert exempt == {("app/routes/auth.py", "register"),
                      ("manage.py", "_create_user")}, (
        "the sites relying on the exemption changed:\n  "
        + "\n  ".join(f"{p}::{f}" for p, f in sorted(exempt)))


# ── the rule itself, pinned to behaviour rather than to today's files ────────

def test_a_handler_that_swallows_without_undoing_is_an_offender():
    ok, reason = _audit_snippet("""
        def import_students(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").insert({"id": uid}).execute()
            except Exception as e:
                errors.append(str(e))
            return uid
    """)

    assert not ok and "does not undo" in reason


def test_moving_the_undo_out_of_the_handler_is_an_offender():
    """The weaker rule accepted an undo anywhere later in the function, so this
    relocation -- the shape of a refactor that "tidies up" the rollback -- used to
    pass while leaving the failure branch with nothing to clean up."""
    ok, reason = _audit_snippet("""
        def import_students(supabase):
            uid = None
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").insert({"id": uid}).execute()
            except Exception as e:
                errors.append(str(e))
            discard_partial_account(supabase, uid, "")
            return uid
    """)

    assert not ok and "does not undo" in reason, reason


def test_every_handler_of_the_try_has_to_undo():
    ok, reason = _audit_snippet("""
        def import_students(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").insert({"id": uid}).execute()
            except ValueError:
                log("bad row")
            except Exception:
                discard_partial_account(supabase, uid, "")
                raise
    """)

    assert not ok and "except ValueError" in reason, reason


def test_a_try_with_no_handler_at_all_is_an_offender():
    ok, reason = _audit_snippet("""
        def import_students(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").insert({"id": uid}).execute()
            finally:
                report()
    """)

    assert not ok and "no except to undo in" in reason, reason


def test_a_create_outside_any_try_is_an_offender():
    ok, reason = _audit_snippet("""
        def make(supabase):
            created = supabase.auth.admin.create_user({})
            supabase.table("profiles").insert({"id": created.user.id}).execute()
    """)

    assert not ok and "not inside a try" in reason, reason


def test_undo_dropped_from_a_later_account_write_is_an_offender():
    """`manage.py` shape: the profile is written in its *own* try, so the branch
    that handles that write is the one running while the account already exists."""
    ok, reason = _audit_snippet("""
        def make(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return None
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception as e:
                print(e)
                return None
            return uid
    """)

    assert not ok and "profiles write" in reason and "does not undo" in reason, reason


def test_a_later_account_write_that_undoes_is_fine():
    """The same shape, with the rollback left where it belongs."""
    ok, reason = _audit_snippet("""
        def make(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return None
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception as e:
                print(e)
                _undo_half_made_account(supabase, uid, "a@b.c", True)
                return None
            return uid
    """)

    assert ok and "later handler" in reason, reason


def test_a_later_write_to_something_other_than_the_account_is_out_of_scope():
    """`auth.py:register` ends by recording the registration request and says, in
    a comment, that it deliberately does not roll back. That is a product
    decision about a different row, not a half-made account, so the sweep must
    stay out of it -- otherwise it would flag correct code forever."""
    ok, reason = _audit_snippet("""
        def register(supabase, admin_client):
            try:
                created = admin_client.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return "Gagal membuat akun"
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception:
                admin_client.auth.admin.delete_user(uid)
                return "Gagal menyimpan profil"
            try:
                supabase.table("school_registration_requests").insert({
                    "profile_id": uid}).execute()
            except Exception:
                return "Gagal membuat permohonan"
            return uid
    """)

    assert ok, reason


def test_an_optional_profile_update_is_not_required_to_undo():
    """`auth.py:register` has, inside the profile try, optional-column updates that
    swallow their errors because the column may not exist yet. The profile row is
    already written by then, so failing one cannot half-make the account -- an
    insert/upsert can, an update cannot."""
    ok, reason = _audit_snippet("""
        def register(supabase, admin_client):
            try:
                created = admin_client.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return "Gagal membuat akun"
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
                try:
                    supabase.table("profiles").update({"status": "pending"}).eq(
                        "id", uid).execute()
                except Exception:
                    pass
            except Exception:
                admin_client.auth.admin.delete_user(uid)
                return "Gagal menyimpan profil"
            return uid
    """)

    assert ok, reason


def test_the_register_shape_is_not_a_false_positive():
    """`create_user` in a try that cannot fail afterwards, with the cleanup in the
    *next* try's handler: this is auth.py, and it is correct."""
    ok, reason = _audit_snippet("""
        def register(supabase, admin_client):
            try:
                created = admin_client.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return "Gagal membuat akun"
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception:
                admin_client.auth.admin.delete_user(uid)
                return "Gagal menyimpan profil"
            return uid
    """)

    assert ok and "later handler" in reason, reason


def test_the_exemption_is_about_structure_not_the_function_name():
    """Rename the function and it still passes; add one fallible call after the
    create and it stops passing. That is what makes the exemption safe to grant."""
    renamed, reason = _audit_snippet("""
        def make_a_school_owner(supabase, admin_client):
            try:
                created = admin_client.auth.admin.create_user({})
                uid = created.user.id
            except Exception:
                return "Gagal membuat akun"
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception:
                admin_client.auth.admin.delete_user(uid)
                return "Gagal menyimpan profil"
            return uid
    """)
    assert renamed, f"the exemption must not depend on the function's name: {reason}"

    one_write_later, reason = _audit_snippet("""
        def make_a_school_owner(supabase, admin_client):
            try:
                created = admin_client.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception:
                return "Gagal membuat akun"
            return uid
    """)
    assert not one_write_later, (
        "a write after the create must switch the exemption off: " + reason)


def test_a_call_inside_a_nested_scope_is_not_counted_as_a_write():
    """Without the nested-scope prune this is a false positive: the inner function
    never runs, yet its call would look like work that can fail after the create."""
    ok, reason = _audit_snippet("""
        def register(supabase):
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                def unused():
                    supabase.table("profiles").insert({}).execute()
            except Exception:
                pass
            try:
                supabase.table("profiles").upsert({"id": uid}).execute()
            except Exception:
                discard_partial_account(supabase, uid, "")
            return uid
    """)

    assert ok, reason


def test_an_undo_inside_a_nested_scope_does_not_count_as_the_handler_undoing():
    """A handler that merely *mentions* a cleanup helper has not cleaned up: the
    nested definition is never called from the handler."""
    ok, reason = _audit_snippet("""
        def import_students(supabase):
            def cleanup():
                discard_partial_account(supabase, uid, "")
            try:
                created = supabase.auth.admin.create_user({})
                uid = created.user.id
                supabase.table("profiles").insert({"id": uid}).execute()
            except Exception:
                assert False, cleanup
            return uid
    """)

    assert not ok and "does not undo" in reason, reason


def test_the_dead_auth_service_module_is_gone():
    """It was an unreferenced wrapper that looked like the safe way to create a
    user and had no rollback at all. Nothing imported it, but a decoy primitive
    is worse than no primitive."""
    assert not (ROOT / "app" / "services" / "auth_service.py").exists()

    for path in sorted(Path(ROOT / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        assert "auth_service" not in source, f"{path} still references it"
