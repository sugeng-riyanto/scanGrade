"""Email a human when the deploy runner falls behind the checkout.

Why this exists
---------------
Every other reading on `/super-admin/deploy-status` needs somebody to open the
page. That is the failure mode the page itself was built to fix, one level up: the
arrangement that makes the runner invisible is the same arrangement that makes its
staleness invisible, and a box can drift for months while the site serves perfectly.
Measured on production, that is exactly what happened — a runner from 45 commits
ago deployed all week, and the only trace was a page nobody had a reason to open.

So the reading somebody would have made by hand is made on a timer and emailed to
the people who can act on it.

Why the app sends it, and not the runner
----------------------------------------
The runner is root and runs every two minutes, which makes it the obvious place —
and the wrong one. A *stale copy* is the thing being reported, so a report the
stale copy composes would be the gates of the day it was installed: the alert would
be missing exactly when it matters, or present in a form that predates the change
that made it necessary. The app is the one process on the box guaranteed to be the
new commit (the copy pulls and reloads it), which is the same argument that put the
armament refusal in `app/utils/armament.py`. It also already has SMTP configured,
a scheduler, and the report.

What is worth waking somebody up for
------------------------------------
Four readings, and the first two are the loud ones because they are the ones where
releases are *not* being checked at all:

* **a copy that predates Gate 0** — nothing can refuse it, so every release ships
  with the gates of the day it was installed, silently;
* **a copy that has drifted** — Gate 0 refuses every release with exit 14, so
  nothing deploys at all while the site looks healthy;
* **the runner is N commits behind the checkout** — a launcher rendered from an
  older `entrypoint.sh`, or a copy taken from an older commit;
* **the checkout is N commits behind `origin/main`** — the deploy is not running:
  a failed fetch, a quarantine nobody released, or the pause file.

`DEFAULT_MIN_COMMITS` is the "more than a few" line. It is deliberately small (5):
the timer runs every two minutes, so a healthy box is never more than a commit or
two behind for more than a moment, and a threshold high enough to be quiet is a
threshold high enough to miss the thing this exists to catch.

What it must never do
---------------------
* **Never alert on a reading it could not make.** A runner it cannot classify
  (`unknown`, `absent`, a checkout that is not a git tree) is silence, not an
  incident: an alert nobody can act on is how a channel gets muted. The page still
  says "cannot measure", which is the honest place for it.
* **Never alert on a laptop.** With no launcher and no `/opt/scangrade`, the report
  is `unknown` everywhere and nothing is sent — the tests that prove the sending
  drive the policy with a report they built.
* **Never send twice for the same state, and never go quiet on a growing one.**
  The email carries the count and the revision, so the same problem does not
  re-notify every tick, a worse one does, and a problem that is still there after
  `RENOTIFY_AFTER_SECONDS` gets one reminder a week.
* **Never let three gunicorn workers send three copies.** They start together and
  tick together, so the tick is claimed through an `O_EXCL` file — one winner
  computes and sends, the others return. The claim is released even when sending
  raises, and an abandoned claim (a worker killed mid-send) is stolen after
  `STALE_CLAIM_SECONDS` rather than blocking alerts forever.

Where the state lives
---------------------
`deploy_alerts.json` (the last alert) and `deploy_alerts.lock` (the tick's claim), in
the first directory that can actually be written: `SCANGRADE_ALERT_STATE_DIR` when it
is set, else `/var/lib/scangrade-deploy/alerts` when the installer has created it for
the service user, else Flask's instance folder. Production takes the middle one — the
deploy pulls into `/opt/scangrade` **as root**, so nothing in the checkout is writable
by the user the app runs as. A record that cannot be written is not a missing
convenience: a *missing record means send*, so a box with nowhere to put it would
mail every interval forever about the same stale runner. The directory that answered
travels to the page, so it is visible rather than assumed.

Whichever one is used, the state stays on the box on purpose: an email is a side
effect, and it should not depend on a database being reachable at the moment the box
is unhealthy enough to need one.

Who gets it
-----------
`system_settings` key `deploy_alert_recipients` (comma, semicolon or newline
separated) if it is set; otherwise every active `super_admin`'s email; otherwise the
SMTP account itself, which still reaches a human and is flagged as the fallback it
is. `/super-admin/deploy-status` shows which source was used and the addresses, so
"alerts are armed" is something an operator can check rather than assume.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import pathlib
import threading
import time

#: The deploy-status page's own gate vocabulary. Imported rather than spelled out:
#: the alert and the page must not be able to disagree about what a copy is doing,
#: and the strings come from the service that reads the file.
from app.services.deploy_status_service import GATE0_CANNOT, GATE0_REFUSES  # noqa: E402

logger = logging.getLogger(__name__)

#: "More than a few commits". See the module docstring for why it is this small.
DEFAULT_MIN_COMMITS = 5

#: How often the box is looked at. Cheap enough to be more frequent than the
#: reminder, quiet enough not to matter on a 1 vCPU box (the report is a handful of
#: `git` calls and it is cached for the page).
DEFAULT_INTERVAL_SECONDS = 6 * 3600

#: A problem that is still there a week later gets one reminder. Not a daily one:
#: the point is that it is *noticed*, and a mail every day is a mail nobody reads.
RENOTIFY_AFTER_SECONDS = 7 * 86400

#: A claim older than this is assumed abandoned rather than held.
STALE_CLAIM_SECONDS = 900

#: The alert's own vocabulary. It mirrors the verdict keys the page already shows,
#: so the email and the card name the same thing.
KIND_COPY_PREDATES_GATE = "copy_predates_gate"
KIND_COPY_DRIFTED = "copy_drifted"
KIND_RUNNER_BEHIND = "runner_behind"
KIND_CHECKOUT_BEHIND = "checkout_behind"
ALERT_KINDS = frozenset({KIND_COPY_PREDATES_GATE, KIND_COPY_DRIFTED,
                         KIND_RUNNER_BEHIND, KIND_CHECKOUT_BEHIND})

#: Where the recipients come from, as keys the template can say in either language.
SOURCE_SETTING = "setting"
SOURCE_SUPER_ADMINS = "super_admins"
SOURCE_SMTP = "smtp"
SOURCE_NONE = "none"
RECIPIENT_SOURCES = frozenset({SOURCE_SETTING, SOURCE_SUPER_ADMINS, SOURCE_SMTP,
                               SOURCE_NONE})

#: The one setting this service reads.
RECIPIENTS_KEY = "deploy_alert_recipients"

#: Sent-but-not-checked outcomes, for the page and the tests.
OUTCOME_SENT = "sent"
OUTCOME_NOT_STALE = "not_stale"
OUTCOME_ALREADY_SENT = "already_sent"
OUTCOME_NO_RECIPIENTS = "no_recipients"
OUTCOME_SEND_FAILED = "send_failed"
OUTCOME_CANNOT_READ = "cannot_read"
OUTCOME_UNKNOWN = "unknown"

#: What the "send a test email" button can answer with. The card has a sentence for
#: each of these, and the test compares this set against those sentences, so a new
#: outcome cannot ship as a blank line on the page.
TEST_ALERT_OUTCOMES = frozenset({OUTCOME_SENT, OUTCOME_NO_RECIPIENTS,
                                 OUTCOME_SEND_FAILED})


# ── the policy, as a pure function of the report ─────────────────────────────
#
# Everything here takes the dict `deploy_status_service.report()` returns and
# answers one question. No network, no clock, no files: the interesting decisions
# (what counts as stale, and what stops a repeat) can then be read and tested
# without a box, and a mistake in them is a failing test rather than a silent
# evening of email.

def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def staleness(report: dict | None, *, min_commits: int = DEFAULT_MIN_COMMITS) -> dict | None:
    """The one thing worth saying about this reading, or None.

    The order is the order they bite: a runner that cannot check a release, then
    one that is behind, then a pipeline that has stopped moving. The first match
    wins, so the email names the loudest fact rather than all of them.
    """
    if not report:
        return None
    runner = report.get("runner") or {}
    checkout = report.get("checkout") or {}
    verdict = (report.get("verdict") or {}).get("key")
    kind = runner.get("kind")

    # A reading we could not make is not an incident. `unknown` covers an absent
    # launcher, an unreadable file, a checkout that is not a git tree — states the
    # page reports and a human judges.
    if kind not in ("launcher", "copy"):
        return None

    behind = _int_or_none(runner.get("origin_behind"))
    from_short = runner.get("origin_short")
    anchor = runner.get("origin_commit") or runner.get("sha256")

    if kind == "copy" and runner.get("gate0") == GATE0_CANNOT:
        return {
            "kind": KIND_COPY_PREDATES_GATE, "verdict_key": verdict,
            "commits": behind, "from_short": from_short, "anchor": anchor,
            "detail": {"installed_at": runner.get("installed_at"),
                       "path": runner.get("path"),
                       "origin_subject": runner.get("origin_subject"),
                       "stale_files": runner.get("origin_stale_files")},
        }
    if kind == "copy" and runner.get("gate0") == GATE0_REFUSES:
        return {
            "kind": KIND_COPY_DRIFTED, "verdict_key": verdict,
            "commits": behind, "from_short": from_short, "anchor": anchor,
            "detail": {"installed_at": runner.get("installed_at"),
                       "path": runner.get("path"),
                       "origin_subject": runner.get("origin_subject"),
                       "stale_files": runner.get("origin_stale_files")},
        }

    if behind is not None and behind > min_commits:
        return {
            "kind": KIND_RUNNER_BEHIND, "verdict_key": verdict,
            "commits": behind, "from_short": from_short, "anchor": anchor,
            "detail": {"runner_kind": kind, "installed_at": runner.get("installed_at"),
                       "path": runner.get("path"),
                       "origin_subject": runner.get("origin_subject"),
                       "stale_files": runner.get("origin_stale_files")},
        }

    # A launcher rendered from an older entrypoint: still runs the checkout, so
    # nothing is broken, and whatever that file changed is not in effect. It only
    # reaches here when the commit count could not be worked out.
    if verdict == "launcher_stale":
        return {
            "kind": KIND_RUNNER_BEHIND, "verdict_key": verdict,
            "commits": None, "from_short": from_short, "anchor": anchor,
            "detail": {"runner_kind": kind, "installed_at": runner.get("installed_at"),
                       "path": runner.get("path")},
        }

    behind_checkout = _int_or_none(checkout.get("behind"))
    if behind_checkout is not None and behind_checkout > min_commits:
        held = (report.get("quarantine") or {}).get("held")
        return {
            "kind": KIND_CHECKOUT_BEHIND, "verdict_key": verdict,
            "commits": behind_checkout, "from_short": None,                       # The count `behind` is measured against the fetched ref, so
                       # that is the revision the alert names.
                       "anchor": checkout.get("origin") or checkout.get("head"),
            "detail": {"paused": bool(report.get("paused")),
                       "held": bool(held),
                       "held_since": (report.get("quarantine") or {}).get("refused_at"),
                       "held_gate": (report.get("quarantine") or {}).get("gate"),
                       "branch": checkout.get("branch")},
        }
    return None


def alert_key(descriptor: dict) -> str:
    """A stable name for *this* staleness, including how bad it is.

    The count is in the key on purpose: 5 commits behind and 40 commits behind are
    the same kind of problem and not the same situation, and the second one should
    reach somebody even if the first was reported an hour ago. The revision is in it
    for the sibling reason: a fixed runner that drifts again is a new event, not a
    continuation.
    """
    return ":".join([
        str(descriptor.get("kind") or "?"),
        str(descriptor.get("commits")) if descriptor.get("commits") is not None else "-",
        str(descriptor.get("from_short") or descriptor.get("anchor") or "-")[:12],
    ])


def should_send(descriptor: dict, last: dict | None, *, now: _dt.datetime,
                renotify_after: int = RENOTIFY_AFTER_SECONDS) -> bool:
    """Whether this alert is new, worse, or old enough to be worth repeating.

    `last` is what the previous send recorded. Nothing here looks at the clock
    except to compare against it, so the whole rule is testable at any date.
    """
    key = alert_key(descriptor)
    if not last or last.get("key") != key:
        return True
    sent_at = _parse_iso(last.get("sent_at"))
    if sent_at is None:
        # A record with no readable time must not silence the alert: the wrong
        # answer here is "never again", and the other one is a single extra mail.
        return True
    return (now - sent_at).total_seconds() >= renotify_after


def _parse_iso(value) -> _dt.datetime | None:
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


# ── who gets it ──────────────────────────────────────────────────────────────

def split_recipients(raw: str | None) -> list[str]:
    """The setting is hand-written, so it is separated generously.

    One address per line is how people actually write a list in a shell heredoc;
    commas and semicolons are how they write it in a sentence. Anything without an
    `@` is dropped rather than emailed and bounced.
    """
    if not raw:
        return []
    parts = str(raw).replace(";", ",").replace("\n", ",").split(",")
    seen, out = set(), []
    for part in parts:
        address = part.strip().strip("<>").strip()
        if "@" not in address or address in seen:
            continue
        seen.add(address)
        out.append(address)
    return out


def recipients(*, supabase=None, settings=None) -> dict:
    """The addresses an alert goes to, and which of three rules chose them.

    The three are ordered by how much they assume: what an operator wrote down,
    then who the super admins are, then the account that would send the mail. The
    last one is a fallback rather than a plan — it reaches a person, and the page
    says it is a fallback, which is the difference between a channel that is quiet
    because nothing is wrong and one that is quiet because nobody is listening.
    """
    from app.utils.supabase_client import get_supabase

    client = supabase or get_supabase()

    raw = None
    if settings is not None:
        raw = settings.get(RECIPIENTS_KEY)
    else:
        try:
            rows = (client.table("system_settings").select("key,value")
                    .eq("key", RECIPIENTS_KEY).execute().data) or []
            raw = rows[0].get("value") if rows else None
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            logger.warning("deploy alerts: could not read %s: %s", RECIPIENTS_KEY, exc)

    configured = split_recipients(raw)
    if configured:
        return {"emails": configured, "source": SOURCE_SETTING, "detail": None}

    try:
        profiles = (client.table("profiles").select("id")
                    .eq("role", "super_admin").eq("status", "active").execute().data) or []
        wanted = {row.get("id") for row in profiles if row.get("id")}
        emails = []
        if wanted:
            for user in client.auth.admin.list_users():
                if getattr(user, "id", None) in wanted and getattr(user, "email", None):
                    emails.append(user.email.strip())
        emails = [e for e in dict.fromkeys(emails) if "@" in e]
        if emails:
            return {"emails": emails, "source": SOURCE_SUPER_ADMINS, "detail": None}
    except Exception as exc:                          # noqa: BLE001 - reported, not raised
        logger.warning("deploy alerts: could not list super admins: %s", exc)

    sender = _smtp_account()
    if sender:
        return {"emails": [sender], "source": SOURCE_SMTP, "detail": None}
    return {"emails": [], "source": SOURCE_NONE,
            "detail": "no recipients configured, and no SMTP account to fall back to"}


def _smtp_account() -> str | None:
    from app.services.notification_service import _smtp_settings

    _, _, user, _password, _sender = _smtp_settings()
    return (user or "").strip() or None


#: How long a resolved recipient list is reused. Five minutes: long enough that a
#: page render never pays for the fallback, short enough that arming the channel in
#: the database shows up while the operator is still looking at the page.
PAGE_RECIPIENTS_CACHE_SECONDS = 300

_page_recipients_cache: dict = {"at": 0.0, "value": None}


def _page_recipients(*, fresh: bool = False) -> dict:
    """`recipients()` for a page render, which must not do the expensive part twice.

    The configured setting is one row; the fallback lists every auth user to work
    out which super admins are active, and that is not a call to make on every
    render of a page whose whole job is to answer immediately.
    """
    now = time.monotonic()
    cached = _page_recipients_cache["value"]
    if not fresh and cached is not None \
            and now - _page_recipients_cache["at"] < PAGE_RECIPIENTS_CACHE_SECONDS:
        return cached
    resolved = recipients()
    _page_recipients_cache.update({"at": now, "value": resolved})
    return resolved


# ── the mail itself ──────────────────────────────────────────────────────────

_KIND_SUBJECT = {
    KIND_COPY_PREDATES_GATE:
        "the installed deploy runner predates Gate 0 — releases are unchecked",
    KIND_COPY_DRIFTED:
        "the installed deploy runner is a drifted copy — nothing can deploy",
    KIND_RUNNER_BEHIND:
        "the deploy runner is behind the checkout",
    KIND_CHECKOUT_BEHIND:
        "the checkout is behind origin/main — deploys are not running",
}

_KIND_ACTION = {
    KIND_COPY_PREDATES_GATE:
        "Run the installer once, as root. Until then every release is deployed by "
        "whatever gates existed the day that copy was installed.",
    KIND_COPY_DRIFTED:
        "Gate 0 refuses that copy, so no release can deploy. Run the installer once, "
        "as root, and the drift is gone for good.",
    KIND_RUNNER_BEHIND:
        "Run the installer once, as root, to bring what runs in step with the "
        "repository.",
    KIND_CHECKOUT_BEHIND:
        "Look at the deploy journal (`journalctl -u scangrade-deploy`) for the reason "
        "the checkout stopped moving, then release the held commit or fix the fault.",
}


def render_email(descriptor: dict, report: dict, *, app_url: str | None = None,
                 test: bool = False) -> dict:
    """Subject and body for one alert. English, like the journal it summarises.

    The runner's own output, the deploy journal and `docs/AUTO_DEPLOY.md` are all
    English, and this goes to the same reader — the *page* is the bilingual surface,
    and the mail links to it.

    `test` is the send-a-test-message case: the same letter, with the readings
    replaced by a line saying nothing is wrong. A test mail indistinguishable from
    an incident is a test mail somebody acts on.
    """
    kind = descriptor.get("kind")
    commits = descriptor.get("commits")
    detail = descriptor.get("detail") or {}
    repo = report.get("repo") or "/opt/scangrade"
    if test:
        subject = "[ScanGrade] test alert — deploy-runner notifications work"
    elif kind == KIND_CHECKOUT_BEHIND:
        subject = (f"[ScanGrade] {commits} commit(s) behind origin/main — deploys "
                   f"are not running")
    elif commits is None:
        subject = f"[ScanGrade] {_KIND_SUBJECT.get(kind, 'deploy runner needs attention')}"
    else:
        subject = f"[ScanGrade] {_KIND_SUBJECT.get(kind, 'runner behind')} ({commits} commits)"

    rows = []
    if commits is not None and not test:
        rows.append(("How far behind", f"{commits} commit(s)"))
    if descriptor.get("from_short"):
        rows.append(("Runner was built from", descriptor["from_short"]))
    if detail.get("origin_subject") and not test:
        rows.append(("That commit", str(detail["origin_subject"])))
    if detail.get("stale_files") is not None and not test:
        rows.append(("Files under deploy/ changed since", str(detail["stale_files"])))
    if detail.get("installed_at") and not test:
        rows.append(("Installed at", str(detail["installed_at"])))
    if detail.get("path") and not test:
        rows.append(("Installed path", str(detail["path"])))
    if kind == KIND_CHECKOUT_BEHIND and not test:
        rows.append(("Deploys paused", "yes" if detail.get("paused") else "no"))
        if detail.get("held"):
            rows.append(("Commit held since", str(detail.get("held_since"))))
            rows.append(("Refusing gate", str(detail.get("held_gate"))))

    table = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b">{label}</td>'
        f'<td style="padding:4px 0;color:#0f172a"><strong>{value}</strong></td></tr>'
        for label, value in rows
    )
    link = ""
    if app_url:
        link = (f'<p style="margin:16px 0 0"><a href="{app_url.rstrip("/")}'
                f'/super-admin/deploy-status" style="color:#ea580c;font-weight:600">'
                f'Open the deploy status page</a></p>')

    if test:
        banner = ('<p style="margin:0 0 16px;padding:10px 12px;background:#fef3c7;'
                  'border-left:3px solid #d97706;color:#0f172a;font-size:13px">'
                  'This is a test message sent from the deploy status page. '
                  '<strong>Nothing is wrong.</strong></p>')
        headline = "Test alert: deploy-runner notifications"
        action = "No action needed. This address will receive these alerts."
        footer = "Test message from the deploy status page — no alert has been raised."
    else:
        banner = ""
        headline = _KIND_SUBJECT.get(kind, "the deploy runner needs attention")
        action = _KIND_ACTION.get(kind, "Open the deploy status page for the readings.")
        footer = ("Sent by ScanGrade when the deploy runner falls behind the checkout. "
                  "One mail per change, and at most one reminder a week while it lasts.")

    html = f"""<div style="font-family:Inter,Arial,sans-serif;max-width:640px;padding:20px">
  <h2 style="color:#0f172a;margin:0 0 4px;font-size:18px">Deploy runner: {headline}</h2>
  <p style="color:#64748b;font-size:13px;margin:0 0 16px">Repository: <code>{repo}</code></p>
  {banner}
  <table style="font-size:14px">{table}</table>
  <p style="color:#0f172a;font-size:14px;margin:16px 0 0">
    {action}
  </p>
  {link}
  <p style="color:#94a3b8;font-size:12px;margin:16px 0 0">
    {footer}
  </p>
</div>"""
    return {"subject": subject, "html": html}


# ── the tick ─────────────────────────────────────────────────────────────────

#: Where `deploy/install-auto-deploy.sh` creates the directory the app may write,
#: beside the deploy's own state. Not created by the app: its parent is root-owned
#: (mode 0750, group traverse only), which is exactly what keeps the quarantine
#: record unwritable by the process that must not be able to erase one.
DEPLOY_STATE_DIR = "/var/lib/scangrade-deploy/alerts"


def _usable(path: pathlib.Path, *, create: bool) -> bool:
    """Can this directory hold the record? Creating it is the caller's decision."""
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            return False
    except OSError:
        return False
    return os.access(path, os.W_OK | os.X_OK)


def state_dir(app=None) -> pathlib.Path:
    """The first directory that can actually be written, in order of preference.

    An explicit `SCANGRADE_ALERT_STATE_DIR` is used **alone**: falling through to
    another directory would quietly ignore what an operator asked for, and a
    misconfigured path is a thing the page should say rather than paper over.
    Otherwise the installer's directory is preferred when it exists and is
    writable, and the app's own instance folder is the last resort (a dev
    checkout). Only the instance folder is created here — anything under
    `/var/lib/scangrade-deploy` belongs to root and to the installer.

    Raises `OSError` when none of them works, because the callers report that: a
    box with nowhere to record an alert cannot keep the "one mail per problem"
    promise, and that is worth a visible error rather than a silent one.
    """
    configured = os.getenv("SCANGRADE_ALERT_STATE_DIR") or None
    if app is not None:
        configured = app.config.get("DEPLOY_ALERT_STATE_DIR") or configured
    if configured:
        path = pathlib.Path(configured)
        if _usable(path, create=True):
            return path
        raise OSError(f"SCANGRADE_ALERT_STATE_DIR={configured} is not a writable "
                      f"directory, so the alert record cannot be kept")

    if _usable(pathlib.Path(DEPLOY_STATE_DIR), create=False):
        return pathlib.Path(DEPLOY_STATE_DIR)

    if app is not None:
        fallback = pathlib.Path(app.instance_path)
    else:
        fallback = pathlib.Path(__file__).resolve().parents[2] / "instance"
    if _usable(fallback, create=True):
        return fallback
    raise OSError(f"no writable directory for the alert record (tried "
                  f"{DEPLOY_STATE_DIR} and {fallback}); set "
                  f"SCANGRADE_ALERT_STATE_DIR to one the service user can write")


def read_state(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("deploy alerts: state %s unreadable: %s", path, exc)
        return None


def write_state(path: pathlib.Path, state: dict) -> None:
    """Write beside the target and rename, so a reader never sees half a record."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class _Claim:
    """One tick, claimed across processes with a file that cannot be created twice.

    Three gunicorn workers start together and therefore tick together. Without this
    the same outage produces three identical emails, which is how an alert channel
    teaches people to filter it.
    """

    def __init__(self, path: pathlib.Path, *, now: _dt.datetime | None = None):
        self.path = path
        self._now = now
        self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False

    def acquire(self) -> bool:
        now = self._now or _dt.datetime.now(_dt.timezone.utc)
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                age = self._age()
                if attempt == 2 or age is None or age < STALE_CLAIM_SECONDS:
                    return False
                # Abandoned: whoever held it is gone (a worker killed mid-send), and
                # a claim that outlives its holder would silence every later alert.
                try:
                    self.path.unlink()
                except OSError:
                    return False
                continue
            except OSError as exc:
                logger.warning("deploy alerts: cannot claim %s: %s", self.path, exc)
                return False
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(now.isoformat())
                self.held = True
                return True
        return False

    def _age(self) -> float | None:
        try:
            written = _parse_iso(self.path.read_text(encoding="utf-8").strip())
        except OSError:
            return None
        if written is None:
            return None
        now = self._now or _dt.datetime.now(_dt.timezone.utc)
        return (now - written).total_seconds()

    def release(self) -> None:
        if not self.held:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.held = False


def check(*, report: dict | None = None, now: _dt.datetime | None = None,
          send=None, state: pathlib.Path | None = None,
          min_commits: int | None = None, recipients_info: dict | None = None) -> dict:
    """One look at the box: claim the tick, read, decide, maybe send, record.

    Returns an outcome key plus what it did, which is what the page and the tests
    read instead of parsing a log line.
    """
    from app.services import deploy_status_service

    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        directory = state.parent if state else state_dir()
    except OSError as exc:
        logger.warning("deploy alerts: no writable state directory: %s", exc)
        return {"outcome": OUTCOME_CANNOT_READ, "detail": str(exc), "sent": False}

    with _Claim(directory / "deploy_alerts.lock", now=now) as acquired:
        if not acquired:
            return {"outcome": OUTCOME_UNKNOWN, "sent": False,
                    "detail": "another worker holds this tick"}

        reading = report if report is not None else deploy_status_service.report()
        descriptor = staleness(reading, min_commits=min_commits or DEFAULT_MIN_COMMITS)
        if descriptor is None:
            return {"outcome": OUTCOME_NOT_STALE, "sent": False, "descriptor": None}

        record = read_state(state or directory / "deploy_alerts.json")
        if not should_send(descriptor, record, now=now):
            return {"outcome": OUTCOME_ALREADY_SENT, "sent": False,
                    "descriptor": descriptor, "last": record}

        who = recipients_info or recipients()
        if not who.get("emails"):
            # Nothing to send to. Recorded as its own outcome rather than failing
            # quietly, so the page can say the channel has nowhere to go.
            return {"outcome": OUTCOME_NO_RECIPIENTS, "sent": False,
                    "descriptor": descriptor, "recipients": who}

        message = render_email(descriptor, reading, app_url=_app_url())
        sender = send or _default_send
        sent = False
        for address in who["emails"]:
            sent = sender(address, message["subject"], message["html"]) or sent

        if sent:
            write_state(state or directory / "deploy_alerts.json", {
                "key": alert_key(descriptor),
                "kind": descriptor["kind"],
                "commits": descriptor.get("commits"),
                "sent_at": now.isoformat(),
                "to": list(who["emails"]),
                "subject": message["subject"],
            })
        return {"outcome": OUTCOME_SENT if sent else OUTCOME_SEND_FAILED,
                "sent": bool(sent), "descriptor": descriptor,
                "recipients": who, "subject": message["subject"]}


def _default_send(address: str, subject: str, html: str) -> bool:
    from app.services.notification_service import send_email

    try:
        return bool(send_email(address, subject, html))
    except Exception as exc:                          # noqa: BLE001 - never fatal
        logger.error("deploy alerts: sending to %s failed: %s", address, exc)
        return False


def _app_url() -> str | None:
    from flask import current_app

    try:
        configured = current_app.config.get("APP_URL")
    except RuntimeError:
        configured = None
    return configured or os.getenv("APP_URL")


def send_test(*, send=None, recipients_info: dict | None = None) -> dict:
    """The button: prove the channel works, without waiting for an incident.

    This is the only thing in the module that sends when nothing is wrong, and it
    is deliberate: the failure it prevents is a mail path that has never once been
    exercised being needed at the moment the box is broken.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    who = recipients_info or recipients()
    if not who.get("emails"):
        return {"outcome": OUTCOME_NO_RECIPIENTS, "sent": False, "recipients": who}
    message = render_email(
        {"kind": KIND_RUNNER_BEHIND, "commits": None, "detail": {}, "from_short": None},
        {"repo": _repo_hint()}, app_url=_app_url(), test=True)
    sender = send or _default_send
    sent = False
    for address in who["emails"]:
        sent = sender(address, message["subject"], message["html"]) or sent
    return {"outcome": OUTCOME_SENT if sent else OUTCOME_SEND_FAILED,
            "sent": bool(sent), "recipients": who, "at": now.isoformat()}


def _repo_hint() -> str:
    return os.getenv("SCANGRADE_REPO") or "/opt/scangrade"


def summary(*, state: pathlib.Path | None = None, recipients_info: dict | None = None,
            interval: int | None = None, fresh: bool = False) -> dict:
    """What the page needs: where alerts go, when the last one left, why not more.

    Never sends and never reads the report — the card has to render on a box whose
    runner is unreadable, which is exactly when an operator opens it.
    """
    try:
        directory = state.parent if state else state_dir()
        unreadable = None
    except OSError as exc:
        directory, unreadable = None, str(exc)
    record = read_state(state or (directory / "deploy_alerts.json")) if directory else None
    who = recipients_info or _page_recipients(fresh=fresh)
    return {
        "armed": True,
        "to": who.get("emails") or [],
        "source": who.get("source") or SOURCE_NONE,
        "source_detail": who.get("detail"),
        "interval_seconds": interval or _interval_seconds(),
        "min_commits": _min_commits(),
        "last": record,
        "state_dir": str(directory) if directory else None,
        "state_error": unreadable,
    }


def _interval_seconds() -> int:
    try:
        from flask import current_app

        return int(current_app.config.get("DEPLOY_ALERT_INTERVAL_SECONDS")
                   or DEFAULT_INTERVAL_SECONDS)
    except (RuntimeError, TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


def _min_commits() -> int:
    try:
        from flask import current_app

        return int(current_app.config.get("DEPLOY_ALERT_MIN_COMMITS")
                   or DEFAULT_MIN_COMMITS)
    except (RuntimeError, TypeError, ValueError):
        return DEFAULT_MIN_COMMITS


# ── the scheduler ────────────────────────────────────────────────────────────
#
# The same shape as the cleanup and retention loops: a daemon thread that pushes an
# application context each pass, started from `create_app` only when background
# work is wanted. The first pass is deliberately *not* immediate — the app has just
# started, and an alert about a runner that was already stale belongs on the page,
# not in an inbox the moment the service restarts.

_thread = None
_stop = threading.Event()


def _loop(app, interval: int) -> None:
    while not _stop.wait(interval):
        try:
            with app.app_context():
                outcome = check()
                if outcome.get("sent"):
                    logger.info("deploy alerts: %s", outcome.get("subject"))
        except Exception as exc:                      # noqa: BLE001 - a loop must not die
            logger.error("deploy alerts: check failed: %s", exc)


def start_deploy_alert_scheduler(interval: int | None = None, app=None) -> None:
    """Start the alert loop (safe to call more than once)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    if app is None:
        from flask import current_app

        app = current_app._get_current_object()
    _stop.clear()
    _thread = threading.Thread(target=_loop,
                               args=(app, interval or _interval_seconds()),
                               name="deploy-alerts", daemon=True)
    _thread.start()
    logger.info("deploy alert scheduler started (interval=%ss)", interval or _interval_seconds())


def stop_deploy_alert_scheduler() -> None:
    global _thread
    _stop.set()
    _thread = None


#: The module's public surface, so the page, the scheduler and the tests all name
#: the same things.
__all__ = [
    "DEFAULT_MIN_COMMITS", "DEFAULT_INTERVAL_SECONDS", "RENOTIFY_AFTER_SECONDS",
    "ALERT_KINDS", "OUTCOME_SENT", "OUTCOME_NOT_STALE", "OUTCOME_ALREADY_SENT",
    "OUTCOME_NO_RECIPIENTS", "OUTCOME_SEND_FAILED", "OUTCOME_CANNOT_READ",
    "TEST_ALERT_OUTCOMES", "RECIPIENT_SOURCES", "RECIPIENTS_KEY",
    "staleness", "alert_key", "should_send", "split_recipients", "recipients",
    "render_email", "check", "send_test", "summary", "state_dir",
    "start_deploy_alert_scheduler", "stop_deploy_alert_scheduler",
]
