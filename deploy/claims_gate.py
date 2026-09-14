#!/usr/bin/env python3
"""The claims gate — do the numbers on the landing page still hold?

The landing page publishes a capacity table (concurrent students -> p50, p95,
error rate) measured against this deployment. Nothing kept those numbers true.
They were written by hand, and the page went on advertising them while the box
that was supposed to deliver them changed underneath: a slower release, a
noisier database, another process on the same 1 vCPU. The claim and the machine
had no connection at all, which is the same defect that let 46,698 requests and
74,923 requests sit on the page with nothing in the repository able to produce
either figure.

So this gate re-measures the page's own lowest advertised rung — the number of
concurrent students the page says it comfortably carries — and refuses the
release when the box no longer behaves the way the page says it does. It reads
the claim out of the template rather than from a constant here, so editing the
page into a bigger promise is what has to be defended, not a copy of it.

Exit codes (the same vocabulary the readability gate uses, and for the same
reason — a broken checker must never be able to take the site down):

    0   measured, and the page still describes this box
    1   measured, and the page no longer describes this box
    2   could not measure — no roster, no reachable base URL, a box already
        busy with real students, a page whose claim cannot be parsed. Never a
        rollback: an absent measurement is not evidence of a bad release.

What this gate does **not** verify, and says so in its own output:

  * the requests-per-second ceiling. The harness paces itself with 1-3 second
    waits, so it can never reach the box's ceiling; it can only show that the
    box handles the rungs the page advertises at that pace.
  * the rungs above the lowest one. Loading 500 sessions at deploy time would
    cost the students we are deploying for. The lowest advertised rung is the
    conservative test: if the box cannot hold the smallest promise on the page,
    the rest of the table is not worth measuring.

Usage:
    python deploy/claims_gate.py --check          # plumbing only, no load
    python deploy/claims_gate.py --roster R --base https://scangrade.web.id
    python deploy/claims_gate.py --sessions 10 --duration 8   # a cheap probe
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_CANNOT_RUN = 2

# --sessions 0 means "the rung the page advertises", so the probe is the claim
# and not a smaller, easier version of it. Kept as a named constant because the
# tests assert it: a default that quietly probed less than the page claims would
# turn a lower-bound measurement into a verdict.
DEFAULT_SESSIONS_FROM_PAGE = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_PAGE = REPO / "app" / "templates" / "landing.html"
DEFAULT_HARNESS = REPO / "loadtest_concurrent.py"
DEFAULT_ROSTER = REPO / ".freebuff" / "lt_roster.json"

# "Different far" is a judgement, so it is written down rather than implied.
#
# A measurement is not a fact about the code alone: it moves with whatever else
# the box is doing. These slacks exist so a busy neighbour is reported, not
# punished. They are deliberately generous — the gate is here to catch a page
# that promises something the machine stopped doing (the observed gap was 3-30x,
# not 10%), not to police a percent of jitter.
# The latency slack started at 3x and was wrong: a 50-session probe measured a
# worst-page p50 of 1464 ms against a published 620 ms — 2.4x — and 3x waved it
# through. 2x is still generous for a shared box, and tighter than a gap that has
# actually been observed here. If this ever needs loosening, loosen the page's
# claim first and measure it.
LATENCY_SLACK = 2.0        # measured may be up to 2x the advertised bound
ERROR_SLACK_PCT = 1.0      # advertised 0% may measure up to 1% before it counts
MIN_LOGIN_SUCCESS = 0.80   # below this we have no measurement, only a login problem
MIN_IDENTITY_CHECKS = 0.80 # sessions must be shown to be their own account
BOX_QUIET_MS = 2000.0      # a /health slower than this means the box is not idle


class ClaimsError(Exception):
    """The page does not state a claim this gate can measure."""


@dataclass
class Rung:
    """One row of the published table, in the units the comparison needs."""

    students: int
    p50_high_ms: float
    p95_ms: float
    error_pct: float
    raw: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (f"{self.students} students: p50 <= {self.p50_high_ms:.0f} ms, "
                f"p95 <= {self.p95_ms:.0f} ms, errors <= {self.error_pct:.2f}%")


# ── reading the claim out of the page ────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _text(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment)).strip()


def _numbers_ms(text: str) -> list[float]:
    """Every number in a cell, in milliseconds. '1.7 s' -> [1700.0]."""
    raw = re.findall(r"\d+(?:[.,]\d+)?", text)
    if not raw:
        return []
    # 'ms' contains an 's', so the unit test has to come first.
    seconds = "ms" not in text.lower() and "s" in text.lower()
    out = []
    for token in raw:
        value = float(token.replace(",", "."))
        out.append(value * 1000.0 if seconds else value)
    return out


def _percent(text: str) -> float:
    found = re.findall(r"\d+(?:[.,]\d+)?", text)
    return float(found[0].replace(",", ".")) if found else 0.0


def parse_claims(path: Path) -> tuple[int, list[Rung]]:
    """Return (comfortable limit, table rungs) as the page states them.

    Raises ClaimsError rather than returning empty, because a silent parse
    failure is indistinguishable from a page with nothing to check — and that
    is exactly how a gate stops running while still looking green.
    """
    body = path.read_text(encoding="utf-8")

    limit = None
    # English is the default; the Indonesian half is the fallback, so the gate
    # keeps working whichever half of the pair a future edit happens to change.
    for pattern in (r"~(\d+)\s+concurrent students per exam session",
                    r"~(\d+)\s+murid serentak per sesi ujian"):
        m = re.search(pattern, body, re.I)
        if m:
            limit = int(m.group(1))
            break
    if limit is None:
        raise ClaimsError(
            f"{path.name} states no comfortable limit ('~N concurrent students "
            "per exam session'). The gate cannot guess which rung the page means."
        )

    rungs: list[Rung] = []
    for row in _ROW.findall(body):
        cells = [_text(c) for c in _CELL.findall(row)]
        # The peak row is starred ('500*') to mark it as a different shape of
        # run, so the count has to be read past the footnote marker. Requiring
        # a bare digit silently dropped that row from the table here -- and the
        # gate then claimed the page published no row for it.
        count = re.fullmatch(r"(\d+)\*?", cells[0]) if cells else None
        if len(cells) < 4 or not count:
            continue
        p50 = _numbers_ms(cells[1])
        p95 = _numbers_ms(cells[2])
        if not p50 or not p95:
            continue
        rungs.append(Rung(
            students=int(count.group(1)),
            # The pessimistic end of a published range: a page may not advertise
            # its best sample as the number.
            p50_high_ms=max(p50),
            p95_ms=max(p95),
            error_pct=_percent(cells[3]),
            raw=cells,
        ))

    if not rungs:
        raise ClaimsError(
            f"{path.name} has no capacity table row the gate can read. Either the "
            "table changed shape (update _ROW/_CELL here) or it was removed — and a "
            "page that publishes a limit with no table cannot be checked at all."
        )
    return limit, rungs


def rung_for(limit: int, rungs: list[Rung]) -> Rung:
    for r in rungs:
        if r.students == limit:
            return r
    raise ClaimsError(
        f"the page states a comfortable limit of {limit} concurrent students, but "
        f"the table has no row for it (rows: {[r.students for r in rungs]}). A "
        "measurement with nothing to compare against is not a check."
    )


# ── comparing a measurement with the claim ───────────────────────────────────

# What the published table is actually about: the pages a signed-in student or
# teacher opens. A 50-way login burst is measured too, but it is a different
# claim — and letting it into the median turns this gate into a login test that
# the smoke test already runs, and fails a perfectly healthy app whose logins
# happen to queue. The comparison is against the *worst* page endpoint, which is
# stricter than a median of them all and says something specific: no page is
# slower than the page promises.
CLAIM_ENDPOINT = re.compile(r"^GET /(student|teacher)/")


@dataclass
class ClaimLatency:
    p50_ms: float
    p95_ms: float
    endpoints: list[str]
    samples: int


def claim_latency(measured: dict) -> ClaimLatency | None:
    """Worst page-load p50/p95 across the signed-in pages, or None if none ran."""
    eps = {k: v for k, v in (measured.get("per_endpoint") or {}).items()
           if CLAIM_ENDPOINT.match(k) and v.get("n")}
    if not eps:
        return None
    return ClaimLatency(
        p50_ms=max(float(v["p50"]) for v in eps.values()),
        p95_ms=max(float(v["p95"]) for v in eps.values()),
        endpoints=sorted(eps),
        samples=sum(int(v["n"]) for v in eps.values()),
    )


def unusable_reason(measured: dict, sessions: int) -> str | None:
    """Why this run cannot be used as a verdict, or None if it can.

    These are kept apart from `compare` on purpose. A roster whose passwords
    stopped working, a login rate limit, or a session that answered as somebody
    else are all failures of the *measurement*, and treating them as failures of
    the *release* would roll back good code for a reason that has nothing to do
    with it. Login health is the smoke test's job; it is armed separately, and
    only after the accounts were proven to sign in.
    """
    logins = int(measured.get("logins_ok") or 0)
    if sessions and logins < sessions * MIN_LOGIN_SUCCESS:
        return (f"only {logins}/{sessions} sessions signed in — the page's claim is about "
                "students who are signed in. A stale roster or a login rate limit is not a "
                "capacity verdict")

    checked = int(measured.get("identity_checked") or 0)
    ok = int(measured.get("identity_ok") or 0)
    if checked < sessions * MIN_IDENTITY_CHECKS:
        return (f"identity was confirmed for only {checked}/{sessions} sessions. The harness "
                "checks each one through /auth/me, and a run whose sessions are not shown to "
                "be their own accounts may be measuring somebody else's")
    if ok < checked:
        return (f"{checked - ok} session(s) were answered as a different account — session "
                "leakage, so these numbers would describe the wrong users")

    if not measured.get("requests_total"):
        return "the run made no requests at all"
    return None


def compare(rung: Rung, measured: dict, sessions: int,
            latency_slack: float = LATENCY_SLACK,
            error_slack: float = ERROR_SLACK_PCT) -> list[str]:
    """Why the measurement fails to support the published rung. [] means it does.

    The probe runs at the advertised rung, so the demand is exactly what the
    page promises — not a scaled-down version of it that could pass by being
    easier than the claim.
    """
    reasons: list[str] = []
    claim = claim_latency(measured)

    if claim is not None:
        p50_ceiling = rung.p50_high_ms * latency_slack
        if claim.p50_ms > p50_ceiling:
            reasons.append(
                f"slowest page p50 {claim.p50_ms:.0f} ms, but the page advertises "
                f"{rung.p50_high_ms:.0f} ms for {rung.students} students "
                f"({claim.p50_ms / max(rung.p50_high_ms, 1):.1f}x the claim; allowed "
                f"{latency_slack:.1f}x = {p50_ceiling:.0f} ms) — worst endpoint: "
                + ", ".join(claim.endpoints[:4])
            )

        p95_ceiling = rung.p95_ms * latency_slack
        if claim.p95_ms > p95_ceiling:
            reasons.append(
                f"slowest page p95 {claim.p95_ms:.0f} ms, but the page advertises "
                f"{rung.p95_ms:.0f} ms (allowed {p95_ceiling:.0f} ms)"
            )

    rate = float(measured.get("error_rate_pct") or 0.0)
    rate_ceiling = rung.error_pct + error_slack
    if rate > rate_ceiling:
        reasons.append(
            f"error rate {rate:.2f}%, but the page advertises {rung.error_pct:.2f}% "
            f"(allowed {rate_ceiling:.2f}%); "
            f"429={measured.get('rate_limited_429')}, 5xx={measured.get('server_errors_5xx')}, "
            f"transport={measured.get('transport_errors')}"
        )

    return reasons


def verdict(first: list[str], second: list[str] | None) -> tuple[int, str]:
    """Two strikes, because one measurement on a shared box is not evidence.

    `second` is None when the confirmation run could not be made (no roster,
    unreachable base) — then the divergence is reported but not enforced.
    """
    if not first:
        return EXIT_OK, "the published numbers still describe this deployment"
    if second is None:
        return EXIT_CANNOT_RUN, ("the first probe diverged but a confirmation run could not be "
                                 "made — not rolling back on a single measurement")
    if second:
        return EXIT_DIVERGED, "both probes diverged from the published numbers"
    return EXIT_OK, ("the first probe diverged, the confirmation run did not — treating it as "
                     "contention on a shared box, not as a stale claim")


# ── running the harness ──────────────────────────────────────────────────────

def box_is_quiet(base: str, quiet_ms: float, samples: int = 4,
                 gap_s: float = 1.0) -> tuple[bool, str]:
    """Is the box idle enough for a measurement to mean anything?

    The deploy runs every couple of minutes on the same 1 vCPU that serves real
    students. Loading it during a live exam would both disturb the exam and
    produce a measurement that says nothing about the release. So a box that is
    already slow is left alone, and that is a "could not measure", never a
    rollback.

    It judges the *best* of several samples, not the first. One slow response
    means the app was still warming (a reload, a template cache, a cold worker)
    — the normal state of a box that has just been restarted, which is exactly
    when this runs. A box that is genuinely busy is slow every time. Using the
    first sample would therefore turn the gate off on every deploy, which is
    the silent failure it is meant to prevent.
    """
    try:
        import httpx
    except ImportError as e:  # pragma: no cover - the venv always has httpx
        return False, f"httpx is not importable: {e}"

    url = base.rstrip("/") + "/health"
    taken: list[float] = []
    with httpx.Client(timeout=20.0, verify=False, follow_redirects=True) as c:
        for i in range(max(1, samples)):
            try:
                t0 = time.perf_counter()
                resp = c.get(url)
                ms = (time.perf_counter() - t0) * 1000
            except Exception as e:
                return False, f"{url} is unreachable ({type(e).__name__}: {e})"
            if resp.status_code != 200:
                return False, f"{url} answered {resp.status_code}"
            taken.append(ms)
            if ms <= quiet_ms:
                break               # one good sample is enough to proceed
            if i + 1 < samples:
                time.sleep(gap_s)

    shown = "/".join(f"{m:.0f}" for m in taken)
    if min(taken) > quiet_ms:
        return False, (f"{url} answered in {shown} ms (best {min(taken):.0f} ms, limit "
                       f"{quiet_ms:.0f} ms) — the box is already carrying traffic; a probe now "
                       "would measure that, not this release")
    return True, f"{url} answered in {shown} ms (limit {quiet_ms:.0f} ms)"


def run_probe(args, sessions: int) -> tuple[dict | None, str, int]:
    """Run the harness once at `sessions` concurrent students. (summary, log, rc)."""
    with tempfile.TemporaryDirectory(prefix="claims-gate-") as tmp:
        out = Path(tmp) / "summary.json"
        cmd = [sys.executable, str(args.harness), str(sessions), str(args.teachers),
               "--roster", str(args.roster), "--base", args.base,
               "--duration", str(args.duration), "--json", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 or not out.exists():
            return None, log, proc.returncode or 1
        try:
            return json.loads(out.read_text(encoding="utf-8")), log, 0
        except (OSError, ValueError) as e:
            return None, log + f"\nsummary unreadable: {e}", 1


def roster_supply(path: Path) -> tuple[int, int]:
    """(students, teachers) the harness will refuse to draw more than."""
    try:
        accounts = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (0, 0)
    students = sum(1 for a in accounts if a.get("role") == "murid")
    teachers = sum(1 for a in accounts if a.get("role") == "guru")
    return students, teachers


# ── evidence ─────────────────────────────────────────────────────────────────

def append_evidence(path: Path, record: dict) -> str:
    """Best-effort one-line-per-run history. Never fatal."""
    if not path:
        return ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return f"evidence appended: {path}"
    except OSError as e:
        return f"could not append evidence to {path} ({e}) — not fatal"


def _fmt_line(measured: dict, rung: Rung) -> str:
    err = float(measured.get("error_rate_pct") or 0)
    claim = claim_latency(measured)
    if claim is None:
        return (f"{measured.get('sessions_launched')} sessions: no signed-in page load was "
                f"recorded; errors {err:.2f}% (page <= {rung.error_pct:.2f}%)")
    return (f"{measured.get('sessions_launched')} sessions, worst of {len(claim.endpoints)} "
            f"page endpoints (n={claim.samples}): p50 {claim.p50_ms:.0f} ms "
            f"(page <= {rung.p50_high_ms:.0f} ms), p95 {claim.p95_ms:.0f} ms "
            f"(page <= {rung.p95_ms:.0f} ms), errors {err:.2f}% "
            f"(page <= {rung.error_pct:.2f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-measure the landing page's capacity claim.")
    ap.add_argument("--page", default=str(DEFAULT_PAGE))
    ap.add_argument("--harness", default=str(DEFAULT_HARNESS))
    ap.add_argument("--roster", default=str(DEFAULT_ROSTER))
    ap.add_argument("--base", default="", help="base URL to measure (must be https in production)")
    ap.add_argument("--sessions", type=int, default=0,
                    help="override the number of concurrent students to launch")
    ap.add_argument("--max-sessions", type=int, default=60,
                    help="never load more than this, whatever the page claims")
    ap.add_argument("--teachers", type=int, default=1)
    # 30s, not 12: a probe shorter than this is dominated by the login burst at
    # the start (50 logins compete for the same box) and measures the wrong
    # thing. The published rows are 60-second runs; this stays short because it
    # runs on the box that is serving students.
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--latency-slack", type=float, default=LATENCY_SLACK)
    ap.add_argument("--error-slack", type=float, default=ERROR_SLACK_PCT)
    ap.add_argument("--quiet-ms", type=float, default=BOX_QUIET_MS)
    ap.add_argument("--check", action="store_true",
                    help="validate the plumbing only: no load, no network")
    ap.add_argument("--json-out", default="", help="write the measurement here")
    ap.add_argument("--evidence-file", default="")
    args = ap.parse_args()

    page = Path(args.page)
    if not page.exists():
        print(f"claims gate: CANNOT MEASURE — {page} is missing")
        return EXIT_CANNOT_RUN
    try:
        limit, rungs = parse_claims(page)
        rung = rung_for(limit, rungs)
    except ClaimsError as e:
        print(f"claims gate: CANNOT MEASURE — {e}")
        return EXIT_CANNOT_RUN

    print(f"claims gate: page advertises {rung.describe()}")

    if not Path(args.harness).exists():
        print(f"claims gate: CANNOT MEASURE — harness {args.harness} is missing")
        return EXIT_CANNOT_RUN

    sessions = args.sessions or rung.students
    if sessions > args.max_sessions:
        # Not a rollback: the gate declined a load it was not willing to place
        # on a box that is serving students. Loud, because a silently skipped
        # check is how this kind of gate dies.
        print(f"claims gate: CANNOT MEASURE — the page's rung is {sessions} sessions, above "
              f"this gate's cap of {args.max_sessions}. Either the claim is larger than this "
              f"gate will load, or --max-sessions should be raised deliberately.")
        return EXIT_CANNOT_RUN

    roster = Path(args.roster)
    students, teachers = roster_supply(roster)
    if students < sessions or teachers < args.teachers:
        # --check exists to prove the gate *can* run, so a roster too small for
        # the probe is exactly what it must fail on. It used to report CHECK OK
        # here, which would arm a gate that could never measure anything.
        print(f"claims gate: CANNOT MEASURE — {roster} holds {students} murid / {teachers} guru, "
              f"but {sessions} murid / {args.teachers} guru are needed. One account per session "
              "is required: reusing logins turns per-identity rate limiting into fake errors.")
        print("   provision them with: (.venv/bin/python provision_loadtest.py "
              f"{sessions + 5} {args.teachers + 1})")
        return EXIT_CANNOT_RUN

    if args.check:
        print(f"claims gate: CHECK OK — page parseable, harness present, roster "
              f"{roster} holds {students} murid / {teachers} guru")
        return EXIT_OK

    if not args.base:
        print("claims gate: CANNOT MEASURE — no --base URL. It must be https in production, "
              "because SESSION_COOKIE_SECURE means a plain-HTTP login cannot keep its cookie.")
        return EXIT_CANNOT_RUN

    quiet, why = box_is_quiet(args.base, args.quiet_ms)
    if not quiet:
        print(f"claims gate: CANNOT MEASURE — {why}")
        return EXIT_CANNOT_RUN

    print(f"claims gate: {why}; probing {sessions} concurrent students for "
          f"{args.duration:.0f}s (cap {args.max_sessions})")
    if sessions < rung.students:
        # An override below the claim must not read as a verdict on the claim.
        print(f"claims gate: note — probing {sessions} of the advertised {rung.students} "
              "sessions. This is a lower-bound check: passing it says the box is not "
              "already dead, not that the published rung holds.")

    measured, log, rc = run_probe(args, sessions)
    if log.strip():
        print(log.rstrip())
    if measured is None:
        print(f"claims gate: CANNOT MEASURE — the harness did not complete (exit {rc})")
        return EXIT_CANNOT_RUN
    if claim_latency(measured) is None:
        print("claims gate: CANNOT MEASURE — the run recorded no signed-in page load, so "
              "there is nothing to compare the published latency against")
        return EXIT_CANNOT_RUN
    unusable = unusable_reason(measured, sessions)
    if unusable:
        print(f"claims gate: CANNOT MEASURE — {unusable}")
        return EXIT_CANNOT_RUN

    reasons = compare(rung, measured, sessions, args.latency_slack, args.error_slack)
    confirmed = None
    if reasons:
        print("claims gate: first probe diverged —")
        for r in reasons:
            print(f"    - {r}")
        print("claims gate: confirming with a second probe before refusing the release ...")
        again, log2, rc2 = run_probe(args, sessions)
        if again is None:
            print(f"claims gate: confirmation run did not complete (exit {rc2})")
        else:
            confirmed = compare(rung, again, sessions, args.latency_slack, args.error_slack)
            print("claims gate: confirmation probe — " + _fmt_line(again, rung))

    code, why_code = verdict(reasons, confirmed)
    fixable = reasons if code == EXIT_DIVERGED else []

    if code == EXIT_OK:
        print(f"claims gate: OK — {_fmt_line(measured, rung)}")
        print(f"claims gate: {why_code}")
    else:
        print(f"claims gate: DIVERGED — {_fmt_line(measured, rung)}")
        print(f"claims gate: {why_code}")

    print("  not verified by this gate: the requests-per-second ceiling (the harness paces "
          "itself with 1-3s waits, so it cannot reach the box's ceiling); any row above "
          f"{sessions} students (loading more than the lowest advertised rung would cost the "
          "students this deploy is for); and endurance — the published run lasted minutes, "
          f"this probe {args.duration:.0f}s, so it sees a distribution and not a degradation "
          "over time.")

    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": {EXIT_OK: "ok", EXIT_DIVERGED: "diverged"}.get(code, "cannot_measure"),
        "reason": why_code,
        "advertised": {"students": rung.students, "p50_ms": rung.p50_high_ms,
                       "p95_ms": rung.p95_ms, "error_pct": rung.error_pct},
        "sessions_probed": sessions,
        "base": args.base,
        "measured": measured,
        "divergences": fixable,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, indent=2, sort_keys=True),
                                       encoding="utf-8")
        print(f"  measurement written: {args.json_out}")
    note = append_evidence(Path(args.evidence_file) if args.evidence_file else None, record)
    if note:
        print(f"  {note}")

    if code == EXIT_DIVERGED:
        print("""
The page promises what this box no longer does. Two ways to make them agree, and
only one of them is honest in the dark:

  * the claim is now wrong — re-measure properly (loadtest_concurrent.py
    --duration) and publish the numbers you actually get, or lower the rung;
  * the box regressed — find out what changed. A release that made rendering
    slower is the likely cause, and rolling it back is the point of this gate.

Do not raise --latency-slack to make this pass. That number is the difference
between "this box is a bit busier than when we measured" and "the page is
advertising something we cannot do".
""")
    return code


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")  # verify=False against the public certificate
    sys.exit(main())
