"""The two school officials can be written to, and can write — and pupils cannot.

`principal` and `vice_principal` have existed since migration 038, but the messaging
system never learned them: `_get_allowed_recipient_roles` returned `[]` for both, so
a head of school could not send anything, no role could address an announcement to
them, and there was no page to open even if a route had existed. A role that exists
in the schema and nowhere in the app is the shape this file pins shut.

Three things are checked here, and each one is a way the gap could come back:

* **the table covers every role** — read from `DASHBOARD_FOR_ROLE`, so a seventh role
  fails here instead of silently having no channels;
* **the pairing is real** — `_get_allowed_recipient_roles` is one-directional, while
  a conversation needs both sides (`api_create_percakapan` demands the reverse
  entry), so the *conversation* list is derived and every pair in it is asserted to
  be mutual. That is also what keeps the officials out of a pupil's contact list
  while still letting them announce to pupils;
* **the page and its dropdown exist** — the routes are registered GET-only, the
  chrome links them with a literal path (a URL that only exists at render time is
  invisible to the reachability guard), and the dropdown in `shared/comms.html`
  mirrors the server's table instead of remembering its own.

`tests/unit/test_role_matrix_docs.py` holds the other half: the matrix in `AGENTS.md`
must print these lists, and these lists must be the ones this file asserts.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "routes" / "api.py"
PRINCIPAL = ROOT / "app" / "routes" / "principal.py"
BASE = ROOT / "app" / "templates" / "base.html"
COMMS = ROOT / "app" / "templates" / "shared" / "comms.html"

OFFICIALS = ("principal", "vice_principal")


def _text(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(ROOT)} is part of this contract"
    return path.read_text(encoding="utf-8")


def _all_roles() -> list[str]:
    from app.utils.auth import DASHBOARD_FOR_ROLE
    return sorted(DASHBOARD_FOR_ROLE)


def _view_source(fn) -> str:
    """The view's own source, decorators unwrapped.

    The blueprint wraps every view in a role guard, so the source of the wrapper
    alone would not show the `render_template` call inside.
    """
    src, seen, level = "", set(), fn
    for _ in range(8):
        if level is None or id(level) in seen:
            break
        seen.add(id(level))
        try:
            src += inspect.getsource(level)
        except (OSError, TypeError):
            pass
        level = getattr(level, "__wrapped__", None)
    return src


class TestTheTableCoversEveryRole:
    def test_every_role_has_a_recipient_list(self):
        from app.routes.api import _ALLOWED_RECIPIENT_ROLES

        missing = [r for r in _all_roles() if r not in _ALLOWED_RECIPIENT_ROLES]
        assert not missing, (
            f"{missing} exist as roles but have no entry in _ALLOWED_RECIPIENT_ROLES, "
            "which is how the officials spent their whole life unable to send anything"
        )

    def test_the_table_names_no_role_that_does_not_exist(self):
        from app.routes.api import _ALLOWED_RECIPIENT_ROLES

        known = set(_all_roles())
        strangers = {r: [t for t in targets if t not in known]
                     for r, targets in _ALLOWED_RECIPIENT_ROLES.items() if r not in known
                     or any(t not in known for t in targets)}
        assert not strangers, f"a list names a role the app does not have: {strangers}"


class TestTheOfficialsAreReachable:
    @pytest.mark.parametrize("official", OFFICIALS)
    def test_an_official_can_write_to_the_staff_they_supervise(self, official):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        for staff in ("super_admin", "admin_sekolah", "guru") + OFFICIALS:
            if staff == official:
                continue
            assert staff in allowed(official), (
                f"{official} cannot write to {staff} — the role is back to sending nothing"
            )

    @pytest.mark.parametrize("official", OFFICIALS)
    def test_the_staff_can_write_to_an_official(self, official):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        for staff in ("super_admin", "admin_sekolah", "guru"):
            assert official in allowed(staff), (
                f"{staff} cannot write to {official}, so the school cannot reach its "
                "head of school"
            )

    def test_the_two_officials_can_reach_each_other(self):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        assert "vice_principal" in allowed("principal")
        assert "principal" in allowed("vice_principal")


class TestAConversationIsOnlyOfferedWhenBothSidesAgree:
    def test_every_conversation_pair_is_mutual(self):
        """The list the contact list is built from never offers a 403."""
        from app.routes.api import (_conversation_roles, _get_allowed_recipient_roles)
        from app.utils.auth import DASHBOARD_FOR_ROLE

        for role in DASHBOARD_FOR_ROLE:
            for other in _conversation_roles(role):
                assert role in _get_allowed_recipient_roles(other), (
                    f"{role} is offered a conversation with {other}, but {other} may not "
                    f"write to {role}: clicking that contact answers 403"
                )

    def test_the_conversation_list_never_exceeds_the_send_list(self):
        from app.routes.api import (_conversation_roles, _get_allowed_recipient_roles)
        from app.utils.auth import DASHBOARD_FOR_ROLE

        for role in DASHBOARD_FOR_ROLE:
            assert set(_conversation_roles(role)) <= set(_get_allowed_recipient_roles(role))


class TestAPupilsChannelsDoNotChange:
    def test_a_pupil_still_writes_to_two_roles(self):
        from app.routes.api import (_conversation_roles, _get_allowed_recipient_roles)

        assert _get_allowed_recipient_roles("murid") == ["guru", "admin_sekolah"]
        assert _conversation_roles("murid") == ["guru", "admin_sekolah"]

    def test_a_pupil_gets_no_extra_contact(self):
        from app.routes.api import _conversation_roles

        assert _conversation_roles("murid") == ["guru", "admin_sekolah"], (
            "the pupil's contact list is the one surface this change deliberately "
            "leaves alone"
        )

    @pytest.mark.parametrize("official", OFFICIALS)
    def test_no_one_opens_a_conversation_with_a_pupil(self, official):
        """Announcing *to* a role is not the same act as holding a chat with it."""
        from app.routes.api import (_conversation_roles, _get_allowed_recipient_roles)

        assert "murid" in _get_allowed_recipient_roles(official), (
            "an official should be able to announce to pupils"
        )
        assert "murid" not in _conversation_roles(official), (
            "a 1-on-1 with a pupil is refused by the pupil's own list, so it must not "
            "be offered as a contact"
        )


class TestThePageExistsAndIsLinked:
    def test_both_routes_are_registered_get_only(self, app):
        rules = {r.rule: r for r in app.url_map.iter_rules()}
        for official in OFFICIALS:
            prefix = "/vice-principal" if official == "vice_principal" else "/principal"
            rule = rules.get(f"{prefix}/comms")
            assert rule is not None, f"{prefix}/comms is not registered"
            assert rule.methods == {"GET", "HEAD", "OPTIONS"}, (
                f"{prefix}/comms must be a read: the officials are read-only, and a "
                "guard refuses a POST under /principal/*"
            )

    def test_the_read_only_guard_still_refuses_a_write(self):
        """The rule `test_invigilation` enforces, restated where the page is added."""
        source = _text(PRINCIPAL)
        writes = re.findall(
            r'@principal_bp\.route\("(/principal[^"]*)",\s*methods=\["POST"\]\)', source)
        assert not writes, f"a POST was registered under the head's prefix: {writes}"

    def test_both_views_render_the_shared_page(self, app):
        for official in OFFICIALS:
            prefix = "/vice-principal" if official == "vice_principal" else "/principal"
            view = app.view_functions[
                next(r.endpoint for r in app.url_map.iter_rules()
                     if r.rule == f"{prefix}/comms")]
            assert "shared/comms.html" in _view_source(view)

    def test_the_chrome_links_both_pages_with_a_literal_path(self):
        text = _text(BASE)
        for path in ("/principal/comms", "/vice-principal/comms"):
            assert path in text, (
                f"{path} is written in the chrome as a rendered expression, so no "
                "reader follows it and the reachability guard cannot see it"
            )


class TestTheDropdownMirrorsTheServer:
    def _client_map(self) -> dict[str, list[str]]:
        body = re.search(r"const allowed = \{(.*?)\};", _text(COMMS), re.S)
        assert body, "the broadcast target map is gone from shared/comms.html"
        return {role: [t.strip().strip("'\"") for t in names.split(",") if t.strip()]
                for role, names in re.findall(r"'([a-z_]+)':\[([^\]]*)\]", body.group(1))}

    def test_the_dropdown_lists_exactly_what_the_server_accepts(self):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        client = self._client_map()
        for role, targets in client.items():
            expected = allowed(role)
            # Reaching your own role is a *group* — a teacher announcing to `guru`
            # means every teacher in the school, which is the normal way to address
            # the staff. The one exception is the platform operator: there is one,
            # not a group of them, so it is the one role not offered to itself.
            if role == "super_admin":
                expected = [t for t in expected if t != role]
            assert set(targets) == set(expected), (
                f"{role}: the dropdown offers {sorted(targets)}, the server accepts "
                f"{sorted(expected)}"
            )

    def test_every_role_that_may_announce_has_a_target_list(self):
        client = self._client_map()
        missing = [r for r in _all_roles() if r != "murid" and r not in client]
        assert not missing, f"{missing} may announce but the page offers no target list"

    def test_the_pupil_row_is_absent_because_a_pupil_may_not_announce(self):
        from app.routes.api import _get_allowed_recipient_roles as allowed

        assert allowed("murid"), (
            "a pupil's list is the roles it may *write to*; it is not empty, which is "
            "why an absence from the dropdown has to be asserted rather than inferred"
        )
        assert "murid" not in self._client_map(), (
            "a pupil may not announce — api_create_pengumuman refuses the role before "
            "it looks at the target — so the page must not offer the form's list"
        )

    def test_every_role_the_dropdown_can_show_has_a_label(self):
        body = re.search(r"roleLabel\(role\) \{\s*const labels = \{(.*?)\};",
                         _text(COMMS), re.S)
        assert body, "roleLabel's table is gone from shared/comms.html"
        named = set(re.findall(r"([a-z_]+):", body.group(1)))
        missing = [r for targets in self._client_map().values()
                   for r in targets if r not in named]
        assert not missing, f"a role is offered with no name for it: {sorted(set(missing))}"


class TestThePageRenders:
    """A 200 for the role that was missing, without a database.

    The page takes no route data at all — it is the same `shared/comms.html` the
    other four roles open — so a session is the whole requirement. Rendering it here
    is also what lets the deploy's smoke gate list the path: an entry there is a
    promise that a seeded account answers 200, and this is the promise's evidence.
    """

    @pytest.mark.parametrize("role,path", [
        ("principal", "/principal/comms"),
        ("vice_principal", "/vice-principal/comms"),
    ])
    def test_an_official_gets_the_page(self, app, monkeypatch, role, path):
        from app.utils import auth as authmod

        monkeypatch.setattr(authmod, "_session_for", lambda token: {
            "user_id": "official-1", "email": f"{role}@x", "name": "Kepala",
            "role": role, "school_id": "school-1", "status": "active",
        })
        client = app.test_client()
        client.set_cookie("access_token", "tok")
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"{path} answered {resp.status_code} for a {role} session"
        )
        body = resp.get_data(as_text=True)
        # The page is really the messages page, and really this reader's: both are
        # what a white page or a redirect would also "pass" without.
        assert "Pengumuman" in body

    def test_a_pupil_is_turned_away_from_the_officials_page(self, app, monkeypatch):
        from app.utils import auth as authmod

        monkeypatch.setattr(authmod, "_session_for", lambda token: {
            "user_id": "pupil-1", "email": "p@x", "name": "Murid",
            "role": "murid", "school_id": "school-1", "status": "active",
        })
        client = app.test_client()
        client.set_cookie("access_token", "tok")
        resp = client.get("/principal/comms")
        assert resp.status_code in (302, 403), (
            "the officials' page is not behind its role guard"
        )


class TestTheDeploySmokeGateKnowsThePage:
    def test_both_paths_are_in_the_smoke_list(self):
        source = (ROOT / "deploy" / "smoke_test.py").read_text(encoding="utf-8")
        for path in ("/principal/comms", "/vice-principal/comms"):
            assert f'"{path}"' in source, (
                f"{path} is not in ROLE_PAGES, so the deploy never opens the page it "
                "was added for"
            )


class TestTheAnnouncementIsScopedToTheSendersSchool:
    def test_the_school_comes_from_the_session_for_every_scoped_role(self):
        source = _text(API)
        assert 'if role in ("admin_sekolah", "guru"):' not in source, (
            "the school scoping is an enumeration again — an official would write a "
            "broadcast under whatever school_id the request body named"
        )
        assert 'if role != "super_admin":' in source
