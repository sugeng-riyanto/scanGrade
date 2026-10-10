"""Every door into the SEB feature, in one blueprint and behind one rule.

Why one blueprint with three audiences
--------------------------------------
This surface has a teacher's panel, a pupil's download and a public guide, so a
single URL prefix would mean either three blueprints or paths that lie about what
they are. It is registered without a prefix and each route carries its own path —
``/teacher/…``, ``/student/…``, ``/panduan/…`` — which is the shape the app's own
conventions already give those audiences.

The rule is one call
--------------------
Every route that touches a password **or** the ``.seb`` file asks
:func:`app.services.seb_access.decide` and then calls
:func:`app.services.seb_access.record`. There is no route here that reads a
credential without both, which is what makes the brief's "setiap tampilan password
dan unduhan file dicatat" a property of the module rather than a promise about
each handler.

Two of the brief's guards live here rather than in a template
-------------------------------------------------------------
The confirmation checkbox on the SEB toggle is **enforced on the server** — the
checkbox is a message to the teacher, and a message that only exists in HTML is one
a crafted POST skips. And the proactive warning about pupils who have been seen on
Android is computed from what the app has actually recorded, not from a guess about
the class.
"""
from __future__ import annotations

import io
import re
from datetime import datetime, timezone

from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for)

from app.services import (seb_access, seb_config_key, seb_crypto, seb_door_log,
                          seb_service)
from app.utils.auth import get_supabase, login_required
from app.utils.helpers import row_or_none
from app.utils.logger import get_logger

seb_bp = Blueprint("seb", __name__)
logger = get_logger("seb")

#: The columns a panel, a file and a door all need. One select, so a route cannot
#: fetch a column the decision never saw — and `end_at`/`duration_minutes` are here
#: because the invigilator's window is derived from the exam's own clock.
EXAM_COLUMNS = ("id, school_id, teacher_id, title, require_seb, seb_config_key, "
                "duration_minutes, start_at, end_at, auto_submit_on_window_end, "
                # Not a column, and it was one here for a while: the staff unlock
                # is `resume_code.manual_unlock`, a *function*. Nothing reads an
                # exam's `manual_unlock`, no migration creates it, and naming it in
                # this select made the whole SEB panel answer PostgREST 42703 on the
                # live database — every route 500ed while no test could see it, since
                # the tests drive a fake client. Every name in this tuple must exist
                # in `supabase/migrations/`, and `deploy/schema_contract.py` now reads
                # a select written as a constant precisely so that cannot recur.
                "max_violations, lock_pending_resume, target_mode, "
                "class_ids, is_published, status, "
                # The switches the generated file mirrors. Listed here because a
                # column left out of a select arrives *absent*, never as an error —
                # and absent means "blocked", so a missing one would be a switch the
                # teacher turned off that the file silently kept enforcing. See
                # `seb_service.EXAM_OWNS`.
                "block_right_click, block_screenshot")

#: The checkbox Fase 6 requires before the toggle may be turned on. Named once: the
#: route and its test must not be able to disagree about the field's name.
CONFIRM_FIELD = "confirm_no_android"

#: The static URL the test ``.seb`` sends a client to. It exists so a school can
#: prove an installation works *before* the day of the exam, which is the only
#: moment a broken install is cheap to find.
TEST_CONFIG_PATH = "/panduan/seb/berhasil"


def _rate_limit(limit):
    """The app's limiter when it has one — a signal endpoint must not be free to hammer."""
    from app.utils.rate_limiter import limiter
    return limiter.limit(limit) if limiter else (lambda f: f)


def _supabase():
    return get_supabase()


def _load_exam(exam_id: str) -> dict:
    """The exam, or a 404. Loaded once, so the decision sees what the route saw."""
    exam = row_or_none(_supabase().table("exams").select(EXAM_COLUMNS)
                       .eq("id", exam_id).maybe_single().execute())
    if not exam:
        abort(404)
    return exam


def _verdict(exam: dict, access: str) -> dict:
    """``seb_access.decide`` with this request's own identity, then the audit row.

    The recording happens **once**, here, on a granted decision — so no route can
    grant access and forget to log it, and no route logs a refusal as though it had
    been a release.
    """
    out = seb_access.decide(
        _supabase(), exam,
        user_id=g.get("user_id"), user_role=g.get("user_role"),
        user_school_id=g.get("user_school_id"), access=access)
    if out.get("ok"):
        seb_access.record(_supabase(), exam,
                          user_id=g.get("user_id"), role=out.get("role") or "",
                          access=access, on_behalf_of=out.get("on_behalf_of"),
                          ip=request.remote_addr,
                          user_agent=request.headers.get("User-Agent", ""))
    return out


def _refuse(out: dict):
    """A refusal, in the shape each audience can read."""
    reason = out.get("reason") or "not_owner"
    if request.path.startswith("/student/"):
        # A pupil is told to find a supervisor, never why the rule matched: the
        # reason would be a map of the policy to anybody who probed it.
        abort(403, description="no_seb_access")
    return abort(403, description=reason)


def _start_url(exam_id: str) -> str:
    """The absolute exam URL that goes into the config and into the hash.

    Delegated to :func:`app.services.seb_service.start_url`, which the exam door
    also calls — so the string the file carries and the string the door hashes are
    the same function, not two copies of the same idea. Two copies is how a school
    ends up with a file nobody can open, and the failure is invisible until a pupil
    is refused on the day of the paper.
    """
    return seb_service.start_url(exam_id)


# ── the teacher's panel ─────────────────────────────────────────────────────

@seb_bp.route("/teacher/exams/<exam_id>/seb")
@login_required
def panel(exam_id):
    """The SEB page for one paper: state, the guards, the file and the passwords."""
    exam = _load_exam(exam_id)
    out = seb_access.decide(
        _supabase(), exam, user_id=g.get("user_id"), user_role=g.get("user_role"),
        user_school_id=g.get("user_school_id"), access=seb_access.ACCESS_VIEW_PASSWORD)
    if not out.get("ok"):
        # The panel itself is a management surface — it *contains* the reveal button,
        # so being refused here is not "no password for you", it is "not your paper".
        #
        # One exception, and it is the state the page exists to leave: **SEB is off**
        # (or was never issued). The switch that turns it on and the Android warning
        # that decides whether it should be are both *on this page*, so refusing the
        # owner here made the whole feature unreachable — the only way in was to type
        # a URL that answered 403, which is exactly the dead end this page is meant
        # to remove. That state is judged by ownership alone, and it is judged
        # narrowly: no credential is shown, no file, and nobody but the owner gets
        # past this line.
        if not (out.get("reason") in ("not_gated", "no_credential")
                and _may_configure(exam)):
            abort(403)
    credential = seb_service.credentials_for(_supabase(), exam_id)
    # Live means: the matrix allowed this reader *and* there is something to show.
    # While the feature is off, a credential row that was left behind by a disable is
    # deliberately not enough — its passwords open a door nothing is checking.
    live = bool(out.get("ok")) and bool(credential and exam.get("seb_config_key"))
    refusals = seb_door_log.refusals_for_exam(_supabase(), exam_id)
    return render_template(
        "teacher/seb_panel.html", exam=exam, verdict=out, credential=credential,
        has_credential=live,
        # The file is generated from the exam's switches, so an exam edited after
        # the file was issued no longer generates its own stored key. The panel has
        # to *say* that: the download would refuse, and a teacher who cannot see why
        # reads it as "the file is broken".
        file_is_current=live and seb_service.file_is_current(exam, credential),
        can_manage=_may_configure(exam),
        confirm_field=CONFIRM_FIELD,
        mobile_risk=_mobile_risk(_supabase(), exam),
        access_log=seb_access.log_for_exam(_supabase(), exam.get("school_id"), exam_id),
        # Who could not get in, and when. Read once, and the tally taken from the
        # very rows the table lists, so the number and the rows cannot disagree.
        # `refusal_summary` is called here rather than in the template because it is
        # arithmetic over two facts (distinct pupils, attempts) that a second
        # implementation in Jinja would get to do differently.
        refusals=refusals, refusal_tally=seb_door_log.refusal_summary(refusals))


@seb_bp.route("/teacher/exams/<exam_id>/seb/enable", methods=["POST"])
@login_required
def enable(exam_id):
    """Turn ``require_seb`` on — but only with the confirmation the brief demands.

    The checkbox is checked **here**, on the server. It is the difference between a
    guard and a suggestion: the field a crafted POST simply omits is the one that
    protects the pupils a wrong toggle would lock out of their own exam.
    """
    exam = _load_exam(exam_id)
    if not _may_configure(exam):
        abort(403)
    if not (request.form.get(CONFIRM_FIELD) or "").strip():
        flash("seb_confirm_required", "error")
        return redirect(url_for("seb.panel", exam_id=exam_id))

    supabase = _supabase()
    issued = seb_service.issue(supabase, exam, start_url=_start_url(exam_id),
                               actor_id=g.get("user_id"))
    if not issued.get("ok"):
        flash("seb_issue_failed", "error")
        return redirect(url_for("seb.panel", exam_id=exam_id))
    _write_exam(supabase, exam_id, {"require_seb": True,
                                    "seb_config_key": issued["config_key"]})
    logger.info("SEB enabled for exam %s by %s", exam_id, g.get("user_id"))
    flash("seb_enabled", "success")
    return redirect(url_for("seb.panel", exam_id=exam_id))


@seb_bp.route("/teacher/exams/<exam_id>/seb/disable", methods=["POST"])
@login_required
def disable(exam_id):
    """Turn it off. The credential row survives, so turning it back on is one click.

    Nothing is deleted, and that is on purpose: the audit log references the paper,
    and a school that switches SEB off for one sitting and back on for the next
    should not have to re-issue (and therefore re-print) anything.
    """
    exam = _load_exam(exam_id)
    if not _may_configure(exam):
        abort(403)
    _write_exam(_supabase(), exam_id, {"require_seb": False})
    logger.info("SEB disabled for exam %s by %s", exam_id, g.get("user_id"))
    flash("seb_disabled", "success")
    return redirect(url_for("seb.panel", exam_id=exam_id))


@seb_bp.route("/teacher/exams/<exam_id>/seb/reissue", methods=["POST"])
@login_required
def reissue(exam_id):
    """Mint new passwords and a new Config Key, and hand back the new file."""
    exam = _load_exam(exam_id)
    if not _may_configure(exam):
        abort(403)
    issued = seb_service.issue(_supabase(), exam, start_url=_start_url(exam_id),
                               actor_id=g.get("user_id"))
    flash("seb_reissued" if issued.get("ok") else "seb_issue_failed",
          "success" if issued.get("ok") else "error")
    return redirect(url_for("seb.panel", exam_id=exam_id))


@seb_bp.route("/teacher/exams/<exam_id>/seb/password", methods=["POST"])
@login_required
def reveal_password(exam_id):
    """Read one password aloud — the only place a plaintext SEB secret leaves the server.

    A POST, not a GET: a GET can be reached by a link, an image tag or a browser
    prefetch, and this response is a secret. The audit row is written *before* the
    value is returned, so the log cannot be missing for a disclosure that happened.
    """
    exam = _load_exam(exam_id)
    out = _verdict(exam, seb_access.ACCESS_VIEW_PASSWORD)
    if not out.get("ok"):
        return _refuse_json(out)
    kind = (request.form.get("kind") or "").strip()
    credential = seb_service.credentials_for(_supabase(), exam_id)
    if not credential:
        return jsonify({"ok": False, "reason": "no_credential"}), 404
    try:
        password = seb_service.reveal(credential, kind)
    except seb_crypto.SebCryptoError as exc:
        logger.warning("SEB password reveal failed for exam %s: %s", exam_id, exc)
        return jsonify({"ok": False, "reason": "unreadable"}), 409
    return jsonify({"ok": True, "kind": kind, "password": password,
                    "role": out.get("role"),
                    "on_behalf_of": out.get("on_behalf_of")})


@seb_bp.route("/teacher/exams/<exam_id>/seb/file")
@login_required
def download_file(exam_id):
    """The ``.seb`` file, for a teacher, an invigilator on duty or the school's staff."""
    exam = _load_exam(exam_id)
    out = _verdict(exam, seb_access.ACCESS_DOWNLOAD_FILE)
    if not out.get("ok"):
        return _refuse(out)
    return _seb_response(exam, exam_id)


@seb_bp.route("/student/exams/<exam_id>/seb-file")
@login_required
def student_file(exam_id):
    """The same file for the pupil it belongs to — the hash, never the password."""
    exam = _load_exam(exam_id)
    out = _verdict(exam, seb_access.ACCESS_DOWNLOAD_FILE)
    if not out.get("ok"):
        return _refuse(out)
    return _seb_response(exam, exam_id)


# ── the JavaScript API door: the same key, on a client that cannot send it ───
#
# SEB for macOS/iOS 3.0+ runs on WKWebView, which cannot attach the Config Key to
# an HTTP header at all — the project's own documentation says it never will. Those
# clients have to prove themselves the other way: the page asks the client's own
# JavaScript API for the value and sends it here. The value is the *same* value
# (documented as "identical to the ones send in the HTTP request header"), which is
# why this route ends in :func:`seb_config_key.header_matches` rather than in a
# comparison of its own: one verification, two transports.

#: The page a client without the header is sent to. Named here so the route and the
#: door cannot disagree about it.
JS_CLAIM_TEMPLATE = "student/seb_claim.html"


@seb_bp.route("/student/exams/<exam_id>/seb-claim", methods=["GET", "POST"])
@login_required
@_rate_limit("30 per minute")
def js_claim(exam_id):
    """GET the handshake page, POST the value the client's own API reported.

    Why a round trip instead of a header check: there is no header to check. The
    client is asked on the page, and the answer comes back as a POST — which is why
    this route is CSRF-protected like any other write, even though it stores nothing
    beyond a signed claim.

    The URL the value is verified against is :func:`seb_service.claim_url`, i.e. **the
    URL of this page** — not the exam's ``startURL``. The client hashes the page it
    is on, and on this transport that page is the handshake page; verifying against
    the exam's address would refuse every honest client, which is the failure mode
    this whole route exists to avoid.
    """
    exam = _load_exam(exam_id)
    if not exam:
        abort(404)
    if not seb_service.gated(exam):
        # Nothing to claim. A paper nobody gated must be untouched by this feature,
        # and answering a claim request for one would also be a way to *ask* whether
        # a paper is gated without being able to open it.
        return redirect(url_for("student.take_exam", exam_id=exam_id))

    from app.utils.exam_access import exam_sitting_allowed
    allowed, _why = exam_sitting_allowed(_supabase(), exam, exam_id, g.get("user_id"))
    if not allowed:
        # The same participation rule the door applies before it sends anyone here,
        # so a claim cannot be minted for a paper that is not this pupil's.
        if request.method == "POST":
            return jsonify({"ok": False, "reason": "not_allowed"}), 403
        abort(403)

    if request.method == "GET":
        return render_template(JS_CLAIM_TEMPLATE, exam=exam,
                               exam_url=url_for("student.take_exam", exam_id=exam_id),
                               guide_url=url_for("seb.guide"),
                               # The pupil's own copy of the file, and the reason it is
                               # offered *here*: this page is where a pupil is told
                               # "open the .seb file from your school" — and the two
                               # refusals it shows are the ordinary browser (no file at
                               # all) and a stale file (the key no longer matches).
                               # Handing over the file this exam's key actually opens
                               # turns the commonest dead end into one press, and it is
                               # the access `decide(murid, download_file)` already
                               # grants and `seb_access_log` already records.
                               file_url=url_for("seb.student_file", exam_id=exam_id))

    payload = request.get_json(silent=True) or request.form
    claimed = payload.get("config_key")
    if seb_config_key.header_matches(seb_service.claim_url(exam_id), claimed,
                                     exam.get("seb_config_key")):
        session[seb_service.JS_CLAIM_SESSION_KEY] = {
            "exam_id": str(exam_id),
            "method": seb_service.JS_CLAIM_METHOD,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return jsonify({"ok": True, "reason": ""})
    # Refused, and logged with the exam only: the claim itself is a secret-derived
    # value and has no business in a log line.
    #
    # This is also *the* place a refusal is decided for a client that answered, so it
    # is where the record is written — see `app/services/seb_door_log.py` for why the
    # exam door is a router and not the decision. The write is best-effort: a pupil
    # whose key does not match still gets their answer, recorded or not.
    logger.info("SEB JavaScript API claim refused: exam %s", exam_id)
    seb_door_log.record(_supabase(), exam, student_id=g.get("user_id"),
                        reason=seb_door_log.REASON_KEY_MISMATCH,
                        user_agent=request.headers.get("User-Agent", ""))
    return jsonify({"ok": False, "reason": "config_key_mismatch"}), 403


@seb_bp.route("/student/exams/<exam_id>/seb-claim/refused", methods=["POST"])
@login_required
@_rate_limit("30 per minute")
def refusal_report(exam_id):
    """The handshake page telling the server it found nothing to prove itself with.

    Why this endpoint has to exist at all: the most common refusal in this school is
    a pupil who opened the exam link in Chrome. That client has no
    ``SafeExamBrowser`` JavaScript API, so the handshake page knows it is refused and
    the *server never sees the attempt* — the pupil simply stops at a page that says
    so. Without a report from the page, the commonest way a class is locked out is
    the one refusal with no record, which is the shape this whole feature exists to
    remove.

    Only the page can see it, so the page reports it, the same arrangement the exam
    page uses for a violation. The reason is checked against
    :data:`seb_door_log.CLIENT_REASONS` rather than taken on trust: the page is the
    only caller and it sends one of two words, and an arbitrary reason arriving here
    would be a row a teacher cannot read (there is no `abort(400)` in the page's own
    script, so a refusal of it costs an honest pupil nothing).

    The two checks above the write are the ones the claim route makes, for the same
    reason: a report is only believed for a paper that is gated and for a pupil this
    page already admitted, so nobody can file a refusal against a paper that is not
    theirs — or against one that is not gated at all, which would also be a way to
    ask whether it is.
    """
    exam = _load_exam(exam_id)
    if not seb_service.gated(exam):
        abort(404)

    from app.utils.exam_access import exam_sitting_allowed
    allowed, _why = exam_sitting_allowed(_supabase(), exam, exam_id, g.get("user_id"))
    if not allowed:
        abort(403)

    payload = request.get_json(silent=True) or request.form or {}
    reason = str(payload.get("reason") or "").strip()
    if reason not in seb_door_log.CLIENT_REASONS:
        abort(400)

    recorded = seb_door_log.record(
        _supabase(), exam, student_id=g.get("user_id"), reason=reason,
        user_agent=request.headers.get("User-Agent", ""))
    # `recorded`, not `ok`: the page shows the pupil the same sentence either way, and
    # telling it the write failed would invite it to retry a thing a pupil cannot fix.
    return jsonify({"ok": True, "recorded": recorded})


def _seb_response(exam: dict, exam_id: str):
    """Stream the generated file. The settings are rebuilt, never stored as a blob.

    Rebuilding is what keeps the file and the key from drifting: the same function
    that computed the stored Config Key produces the bytes, from the same rows — the
    exam's switches and the credential's hashes — so a file that exists cannot
    describe a config the door would refuse. It is also why the credential's hashes,
    not the plaintext, are read here: the file has no way to contain a readable
    password because this function never asks for one.

    One consequence of the exam being an input, and it is the reason the comparison
    below is not merely a sanity check any more: a teacher who edits a switch the
    file mirrors *after* the file was issued makes those two rows disagree, and the
    honest answer is to refuse — the alternative is streaming a file whose key does
    not match the one the door checks. The panel says so on screen, and re-issuing is
    one press; files already distributed keep working until then.
    """
    credential = seb_service.credentials_for(_supabase(), exam_id)
    if not credential or not exam.get("seb_config_key"):
        abort(409, description="no_credential")
    settings = seb_service.settings_for(
        exam, start_url=_start_url(exam_id),
        quit_hash=credential.get("quit_password_hash") or "",
        admin_hash=credential.get("admin_password_hash") or "")
    if seb_config_key.config_key(settings) != exam.get("seb_config_key"):
        # The file would not open the exam. Refusing is the honest answer: handing
        # it over would be a pupil sent home with a paper that cannot start.
        logger.error("SEB config for exam %s no longer matches its stored key", exam_id)
        abort(409, description="stale_config")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(exam.get("title") or "")).strip("-") or "ujian"
    return send_file(io.BytesIO(seb_service.seb_file_bytes(settings)),
                     mimetype="application/octet-stream", as_attachment=True,
                     download_name=f"{slug}-{str(exam_id)[:8]}.seb")


def _may_configure(exam: dict) -> bool:
    """Whether this caller owns the paper — the toggle is the owner's to flip.

    Deliberately narrower than the read matrix: an invigilator may *read* a password
    to help a pupil, and must not be able to turn the requirement off, on, or
    re-issue the file that every other pupil is holding.
    """
    from app.utils.exam_access import can_manage_exam
    if g.get("user_role") != "guru":
        # Admins are not offered the toggle: they cannot run the paper, and the
        # brief gives them visibility, not authorship.
        return False
    return can_manage_exam(g.get("user_id"), g.get("user_role"),
                           g.get("user_school_id"), exam)


def _write_exam(supabase, exam_id: str, payload: dict) -> bool:
    """One scoped update. A failed write is reported, never assumed."""
    try:
        rows = (supabase.table("exams").update(payload).eq("id", exam_id)
                .execute().data or [])
    except Exception as exc:                                     # noqa: BLE001
        logger.exception("SEB: exam update failed for %s: %s", exam_id, exc)
        return False
    return bool(rows)


def _refuse_json(out: dict):
    return (jsonify({"ok": False, "reason": out.get("reason") or "not_allowed"}), 403)


# ── the proactive warning: what the school has actually seen ────────────────

def _mobile_risk(supabase, exam: dict) -> dict:
    """How many of this paper's pupils have been seen signing in from a phone.

    The brief asks for a *concrete number* before a teacher flips the toggle, and
    the honest source is what the app already recorded: the most recent login user
    agent for each pupil the paper is for. Nothing is inferred from the class name
    and nothing is estimated — a count of zero means "not seen", which is a weaker
    statement than "nobody uses a phone", and the panel says it that way.

    Best effort: an unreadable history returns zeros rather than blocking a page a
    teacher has to open before they can configure anything.
    """
    empty = {"pupils": 0, "android": 0, "ios": 0, "known": 0}
    try:
        from app.services import exam_targets
        student_ids = sorted(exam_targets.included_student_ids(supabase, exam.get("id")))
        if not student_ids:
            return empty
        rows = (supabase.table("audit_logs")
                .select("user_id, user_agent, created_at")
                .in_("user_id", student_ids[:500]).eq("action", "login")
                .order("created_at", desc=True).limit(1000).execute().data or [])
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("SEB: could not read device history: %s", exc)
        return empty

    android = ios = known = 0
    seen: set[str] = set()
    for row in rows:                      # newest first: the first row per pupil wins
        uid = str(row.get("user_id"))
        if not uid or uid in seen:
            continue
        seen.add(uid)
        agent = str(row.get("user_agent") or "")
        if not agent:
            continue
        known += 1
        if "Android" in agent:
            android += 1
        elif "iPhone" in agent or "iPad" in agent or "iOS" in agent:
            ios += 1
    return {"pupils": len(student_ids), "android": android, "ios": ios, "known": known}


# ── the weak environment signals (Fase 2/3), collected once per sitting ───────

@seb_bp.route("/api/seb/environment", methods=["POST"])
@login_required
@_rate_limit("6 per minute")
def environment():
    """Take a pupil's raw environment observations once, at the start of a sitting.

    Deliberately **not** a scored endpoint and deliberately not part of the anti-cheat
    ladder: it writes context and returns nothing a client can act on. The scoring
    happens server-side in :mod:`app.services.environment_signals`, and the response
    says only whether the observation was stored — a client that learned its own
    score could learn the thresholds from it.

    A teacher is refused even though they hold a login: these are measurements about
    *a pupil's device*, and the only person who may submit them is the pupil sitting
    the paper. The check is the app's own ``exam_sitting_allowed``, so a pupil who is
    not on the paper cannot attach signals to somebody else's sitting.
    """
    if g.get("user_role") != "murid":
        return jsonify({"ok": False, "reason": "not_a_pupil"}), 403
    payload = request.get_json(silent=True) or request.form
    exam_id = str(payload.get("exam_id") or "").strip()
    if not exam_id:
        return jsonify({"ok": False, "reason": "no_exam"}), 400

    supabase = _supabase()
    exam = row_or_none(supabase.table("exams")
                       .select("id, school_id, class_ids, target_mode, "
                               "is_published, status")
                       .eq("id", exam_id).maybe_single().execute())
    if not exam:
        return jsonify({"ok": False, "reason": "no_exam"}), 404

    from app.utils.exam_access import exam_sitting_allowed
    allowed, _why = exam_sitting_allowed(supabase, exam, exam_id, g.get("user_id"))
    if not allowed:
        return jsonify({"ok": False, "reason": "not_allowed"}), 403

    submission = row_or_none(supabase.table("submissions").select("id")
                             .eq("exam_id", exam_id)
                             .eq("student_id", g.get("user_id"))
                             .maybe_single().execute())
    if not submission:
        # No sitting yet: nothing to attach a signal to, and inventing one would put
        # a measurement on a paper the pupil has not opened.
        return jsonify({"ok": False, "reason": "no_sitting"}), 409

    from app.services import environment_signals
    raw = {kind: payload.get(kind) for kind in environment_signals.SIGNAL_TYPES
           if payload.get(kind) is not None}
    out = environment_signals.record(
        supabase, school_id=str(exam.get("school_id") or ""), exam_id=exam_id,
        submission_id=str(submission.get("id")), student_id=g.get("user_id"), raw=raw)
    # The *assessment* is never returned: see the docstring.
    return jsonify({"ok": bool(out.get("ok")), "reason": out.get("reason") or "",
                    "stored": out.get("stored") or 0}), (200 if out.get("ok") else 409)


# ── the public guide (Fase 10): no login, bilingual, shareable as-is ─────────

#: Where a school downloads SEB. **Never** a copy of our own: the brief forbids
#: hosting the installer, and a stale mirror of a security tool is a worse problem
#: than a broken link. One place, so the page and its test cannot drift.
OFFICIAL_DOWNLOADS = (
    ("Windows", "https://safeexambrowser.org/download_en.html"),
    ("macOS", "https://safeexambrowser.org/download_en.html"),
    ("iPadOS / iOS", "https://safeexambrowser.org/ios/download_ios_en.html"),
)


@seb_bp.route("/panduan/seb")
def guide():
    """The public page. No login, no token — the URL *is* the thing to share."""
    return render_template("seb_guide.html", downloads=OFFICIAL_DOWNLOADS,
                           test_config_path=TEST_CONFIG_PATH)


def _test_settings() -> dict:
    """The generic test config: a real config that gates nothing.

    It carries a real Config Key like any other, and points at the static
    verification page. Its passwords are fixed and published — they guard a page
    whose whole purpose is to be reachable — which is exactly why this config is
    never used for an exam.
    """
    return seb_service.settings_for(
        None,      # no exam, so no switches to honour: this file gates nothing
        start_url=request.url_root.rstrip("/") + TEST_CONFIG_PATH,
        quit_hash=seb_crypto.sha256_hex("SEB-TEST-QUIT"),
        admin_hash=seb_crypto.sha256_hex("SEB-TEST-ADMIN"))


@seb_bp.route("/panduan/seb/uji.seb")
def test_config():
    """The installation test file — "open me and see whether SEB works"."""
    return send_file(io.BytesIO(seb_service.seb_file_bytes(_test_settings())),
                     mimetype="application/octet-stream", as_attachment=True,
                     download_name="ScanGrade-SEB-Uji.seb")


@seb_bp.route(TEST_CONFIG_PATH)
def test_verified():
    """Two different answers for two different ways of arriving.

    Opened by SEB with our config, the client's Config Key matches and the page says
    the installation works. Opened by an ordinary browser there is no header, so the
    page says what is true: this is not SEB. Both are the *same* page, which is what
    makes it a test rather than a confirmation screen.
    """
    settings = _test_settings()
    key = seb_config_key.config_key(settings)
    verified = seb_service.verify({"require_seb": True, "seb_config_key": key},
                                  request.url,
                                  request.headers.get(seb_config_key.CONFIG_KEY_HEADER))
    return render_template("seb_verified.html", verified=verified)


__all__ = ["CONFIRM_FIELD", "EXAM_COLUMNS", "OFFICIAL_DOWNLOADS", "TEST_CONFIG_PATH",
           "seb_bp"]
