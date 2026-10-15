"""Which NPSN a teacher is working in — and the one door that changes it.

`app.utils.auth` resolves the school of **every** request from the verified active
membership (`school_membership.resolve_for_request`), and that resolution honours a
per-token choice. Every other part of the feature was already here: the membership
rows, the resolution that re-checks a closed row on the next request, the storage for
the choice, and the carry-over that keeps it across the hourly token refresh. What
did not exist was a **writer**: `set_active_school` had no caller anywhere in the
app, so the choice could never be made.

The consequence was not cosmetic. A teacher whose second NPSN had accepted them
stayed resolved into the school their profile names, so the destination school could
not find them on its roster, could not give them a class, and their lessons, marks
and papers never followed the school that had accepted them. Being treated as acting
elsewhere required *leaving* the home school — changing `profiles.school_id`, which
moves every row they ever wrote. This route is what makes choosing unnecessary to be
a one-way act.

Two rules, and both are here rather than in the template, because a form is a claim:

* a school the caller is not an **active** member of is refused, so a stale page
  cannot move a teacher into a school they were removed from, and a closure cannot be
  walked around by switching back into it;
* a post that names no school — or the word `home` — **clears** the choice instead of
  setting one. Going back to the school one's own profile names has to be possible
  without knowing which school that is, and it may not be a school id the client can
  invent.

The choice itself is server-side and per session token (`ACTIVE_SCHOOL_KEY`), so
nothing about it travels in a cookie or a form the browser can edit: this route
writes it, `resolve_for_request` is the only thing that reads it, and the membership
row is re-checked on every request that uses it.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, g, redirect, request, url_for

from app.services import school_membership as membership
from app.services.audit_service import log_activity
from app.utils.auth import get_supabase, login_required

logger = logging.getLogger(__name__)

school_switch_bp = Blueprint("school_switch", __name__)

#: What the control posts to go back to the school the account's own profile names.
#: A word and not a school id: the school a teacher belongs to by default is the one
#: thing this form does not need to be told, and a token like this cannot be invented
#: into a different school.
HOME = "home"


def _safe_next(raw) -> str:
    """A local path to return to after a switch, or ``\"\"``.

    The header's control posts the page it was used from, so a teacher who changes
    school while reading their exam list is returned to that list. Only a
    **single-slash local path** is accepted: `//evil.example` is protocol-relative and
    a browser follows it off-site, and a backslash is read as a slash by more than one
    client — so a switch could otherwise be turned into an open redirect.

    Tab, carriage return and newline are refused for the same reason and are the one
    shape that looks local and is not: a browser **strips them out of a URL before
    parsing it**, so `/<TAB>/evil.example` reaches the parser as `//evil.example` —
    protocol-relative, off-site, past a check that only ever saw one leading slash.
    """
    value = (raw or "").strip()
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ch in value for ch in "\t\r\n")):
        return ""
    return value


def _home_for_role(role) -> str:
    """Where a switch lands when the control posted no `next` at all.

    The three roles that may hold two memberships have three different dashboards,
    and a redirect to a page the reader cannot open would look exactly like the
    switch having failed.
    """
    if role in ("principal", "vice_principal"):
        endpoint = ("principal.vice_principal_dashboard" if role == "vice_principal"
                    else "principal.principal_dashboard")
    else:
        endpoint = "teacher.dashboard"
    try:
        return url_for(endpoint)
    except Exception:
        logger.debug("no %s endpoint; landing on the root", endpoint, exc_info=True)
        return "/"


@school_switch_bp.route("/teacher/school/switch", methods=["POST"])
@login_required
def switch_school():
    """Work in one of the schools this account is an active member of.

    The refusal is a 403 and not a quiet redirect back: a write that was rejected
    used to look exactly like a write that did nothing on this site (the anti-cheat
    event log 403'd on every send and nothing surfaced it), so an impossible choice
    states itself. The control can only *offer* schools read from the caller's own
    active rows in the request that drew it, which means this page is reached by a
    hand-made post or by a row that was closed between the two requests — both of
    which deserve to be told.
    """
    if not membership.is_cross_school_role(g.get("user_role")):
        # A pupil or a school admin has one school, decided by their profile, and no
        # choice to make — the same partition every other membership path draws.
        abort(403)
    token = g.get("user_token")
    back = _safe_next(request.form.get("next"))
    target = (request.form.get("school_id") or "").strip()
    if not target or target == HOME:
        membership.clear_active_school(token)
        log_activity("update", "active_school", g.get("user_id"),
                     new_data={"school_id": None, "choice": HOME},
                     user_id=g.get("user_id"))
        return redirect(back or _home_for_role(g.get("user_role")), code=303)
    if not membership.is_active_member(get_supabase(), g.get("user_id"), target):
        # A school id off a form is a claim; the membership row is the answer. Asking
        # here as well as at resolution is not redundant: this is the door, and a row
        # that is gone must not be written into a session that outlives it.
        logger.warning("school switch refused: %s is not an active membership of %s",
                       target, g.get("user_id"))
        abort(403)
    membership.set_active_school(token, target)
    log_activity("update", "active_school", g.get("user_id"),
                 new_data={"school_id": target}, user_id=g.get("user_id"))
    return redirect(back or _home_for_role(g.get("user_role")), code=303)
