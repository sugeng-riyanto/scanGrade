"""Has this release made the box slower than the release before it?

`claims_gate.py` asks a question about *the page*: does this deployment still
behave the way the published capacity table says? That number is written in an
HTML file, and the question can only be asked at the advertised rung.

This gate asks a different one: **did this release cost us response time?** The
comparison is against the last release that passed — same harness, same box, same
reference load — so a change no static claim can see (the box getting 40% slower
over five releases, each one individually "still inside the published bound") is
visible here. Neither gate can see what the other sees, which is why both exist.

Every design choice below is a failure mode it avoids:

* **A small, fixed reference load.** 20 concurrent students for 20 seconds by
  default. This runs on the 1 vCPU that serves real students, after every
  release: loading it the way the published rung does would cost the students
  the deploy is for. It is also the *right* instrument for a ratio — at 20
  sessions the box is far from saturated, so latency tracks per-request cost
  instead of queueing, and the comparison keeps meaning something. The advertised
  rung is the claims gate's job, and it stays there.

* **The baseline is written only when a release passes.** If a bad release became
  the baseline, the next release would be measured against it and the regression
  would become permanent and invisible. On a regression the baseline is left
  exactly as it was, so the next release is still compared with the last good one.

* **The first run becomes the baseline and passes.** You cannot regress against a
  measurement that does not exist. It says so out loud rather than passing
  quietly, and it self-arms on the first deploy after installation.

* **A changed shape is "could not measure", never a pass.** Sessions, teachers,
  duration and base URL are part of the comparison: a 20-session probe compared
  against a 60-session one reports a regression that is really a different
  experiment. A mismatch is exit 2 — an unanswerable question, not evidence
  against the release.

* **Two strikes.** One probe on a box that is also running nginx, three gevent
  workers and whatever the students are doing is not evidence. A divergent first
  probe is confirmed by a second, and only both diverging refuse the release. A
  confirmation that could not be made is exit 2, and exit 2 never rolls back.

Exit codes are the ones `scangrade-deploy.sh` already knows how to read, because
that script treats this gate exactly like the claims gate:

    0  passed (and the baseline was updated)
    1  confirmed regression — a real rollback when PERF_ENFORCE=true
    2  could not measure — never a rollback
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# claims_gate owns the parts that are expensive to get right twice: the harness
# invocation, the identity/login sanity checks, the two-strike verdict and the
# "is this box quiet enough to measure anything" probe. Reusing them is what
# keeps the two gates from disagreeing about what a measurement means.
sys.path.insert(0, str(HERE))
import claims_gate as cg  # noqa: E402

EXIT_OK = cg.EXIT_OK
EXIT_REGRESSED = cg.EXIT_DIVERGED
EXIT_CANNOT_RUN = cg.EXIT_CANNOT_RUN

DEFAULT_HARNESS = REPO / "loadtest_concurrent.py"
DEFAULT_ROSTER = REPO / ".freebuff" / "lt_roster.json"
DEFAULT_BASELINE = Path("/var/lib/scangrade-deploy/perf/baseline.json")
DEFAULT_EVIDENCE = Path("/var/lib/scangrade-deploy/perf/history.jsonl")

# How much worse a release may get before it counts. These are deliberately
# loose: the noise between two identical runs on this box measured around 1.3x
# on p50, so a tighter bound would reject healthy releases, and a gate that
# rejects healthy releases is a gate somebody switches off.
DEFAULT_LATENCY_SLACK = 1.5     # p50 may be up to 1.5x the baseline
DEFAULT_P95_SLACK = 1.6         # p95 is noisier, so it gets more room
DEFAULT_ERROR_SLACK_PCT = 0.5   # percentage points above the baseline
DEFAULT_SESSIONS = 20
DEFAULT_DURATION = 20.0
DEFAULT_TEACHERS = 2

# The wording claims_gate.verdict() uses for *this* gate's question. Without it,
# a refusal would read "both probes diverged from the published numbers" and send
# whoever opens the journal looking for a claim to correct when the code is what
# changed. A test asserts the phrase and that it does not say "published".
VERDICT_WORDS = {"diverged_from": "the previous release",
                 "ok": "the release is not slower than the previous one",
                 "other": "a regression"}


# ── the baseline ─────────────────────────────────────────────────────────────

def shape_of(sessions: int, teachers: int, duration: float, base: str) -> dict:
    """What has to be identical for two runs to be comparable at all."""
    return {"sessions": int(sessions), "teachers": int(teachers),
            "duration_s": round(float(duration), 3), "base": str(base).rstrip("/")}


def shape_mismatch(baseline: dict, shape: dict) -> str | None:
    """Why these two measurements cannot be compared, or None if they can."""
    old = baseline.get("shape") or {}
    diff = [f"{k}: baseline {old.get(k)!r} vs now {shape[k]!r}"
            for k in sorted(shape) if old.get(k) != shape[k]]
    if not diff:
        return None
    return ("the reference load changed, so there is nothing to compare against "
            "(" + "; ".join(diff) + "). This is not a verdict on the release — either "
            "put the setting back, or re-baseline deliberately with --rebaseline")


def load_baseline(path: Path) -> dict | None:
    """The last release that passed, or None if there is not one yet."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("latency") else None


def save_baseline(path: Path, record: dict) -> str:
    """Write the new baseline, atomically, and never fatal on failure.

    Atomic because this file is read by the next deploy while it is written by
    this one: a half-written baseline would be read as "no baseline", silently
    disarming the gate for a release.
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(Path(path).parent),
                                         prefix=".baseline-", delete=False) as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
            tmp = fh.name
        os.replace(tmp, path)
        return f"baseline updated: {path}"
    except OSError as e:
        return f"could not write the baseline to {path} ({e}) — not fatal"


def baseline_record(commit: str, shape: dict, measured: dict, latency) -> dict:
    return {
        "commit": commit,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shape": shape,
        "latency": {"p50_ms": round(latency.p50_ms, 1),
                    "p95_ms": round(latency.p95_ms, 1),
                    "endpoints": latency.endpoints,
                    "samples": latency.samples},
        "error_pct": float(measured.get("error_rate_pct") or 0.0),
        "server_errors_5xx": int(measured.get("server_errors_5xx") or 0),
        "requests_total": int(measured.get("requests_total") or 0),
        "throughput_rps": float(measured.get("throughput_rps") or 0.0),
    }


# ── the comparison ───────────────────────────────────────────────────────────

def regression(baseline: dict, measured: dict,
               latency_slack: float = DEFAULT_LATENCY_SLACK,
               p95_slack: float = DEFAULT_P95_SLACK,
               error_slack: float = DEFAULT_ERROR_SLACK_PCT) -> list[str]:
    """Why this release is slower than the baseline. [] means it is not.

    A ratio, not an absolute: the baseline is this same box on this same load, so
    the numbers cancel and what is left is what the release changed.
    """
    reasons: list[str] = []
    old = baseline.get("latency") or {}
    new = cg.claim_latency(measured)
    if new is None or not old:
        return reasons

    base_p50, base_p95 = float(old.get("p50_ms") or 0.0), float(old.get("p95_ms") or 0.0)
    commit = baseline.get("commit") or "the previous release"

    if base_p50 > 0:
        ratio = new.p50_ms / base_p50
        if ratio > latency_slack:
            reasons.append(
                f"slowest page p50 {new.p50_ms:.0f} ms against {base_p50:.0f} ms on {commit} "
                f"({ratio:.2f}x, allowed {latency_slack:.2f}x) — worst endpoint(s): "
                + ", ".join(new.endpoints[:4]))
    if base_p95 > 0:
        ratio = new.p95_ms / base_p95
        if ratio > p95_slack:
            reasons.append(
                f"slowest page p95 {new.p95_ms:.0f} ms against {base_p95:.0f} ms on {commit} "
                f"({ratio:.2f}x, allowed {p95_slack:.2f}x)")

    # A release that answers 500s is worse than any latency number, whatever the
    # latency says: those requests produced no page at all.
    fivexx = int(measured.get("server_errors_5xx") or 0)
    if fivexx > 0:
        reasons.append(f"{fivexx} server error(s) (5xx) — the baseline run had none")

    rate = float(measured.get("error_rate_pct") or 0.0)
    base_rate = float(baseline.get("error_pct") or 0.0)
    if rate > base_rate + error_slack:
        reasons.append(
            f"error rate {rate:.2f}% against {base_rate:.2f}% on {commit} "
            f"(allowed +{error_slack:.2f}pp); 429={measured.get('rate_limited_429')}, "
            f"5xx={measured.get('server_errors_5xx')}, "
            f"transport={measured.get('transport_errors')}")
    return reasons


def describe(measured: dict, baseline: dict | None = None) -> str:
    new = cg.claim_latency(measured)
    err = float(measured.get("error_rate_pct") or 0.0)
    if new is None:
        return (f"{measured.get('sessions_launched')} sessions: no signed-in page load was "
                f"recorded; errors {err:.2f}%")
    line = (f"{measured.get('sessions_launched')} sessions, worst of {len(new.endpoints)} page "
            f"endpoints (n={new.samples}): p50 {new.p50_ms:.0f} ms, p95 {new.p95_ms:.0f} ms, "
            f"errors {err:.2f}%")
    old = (baseline or {}).get("latency") or {}
    if old.get("p50_ms"):
        p50 = float(old["p50_ms"])
        p95 = float(old["p95_ms"] or 0.0)
        delta = f"; against {baseline.get('commit') or 'baseline'}: p50 {p50:.0f} ms"
        if p95:
            delta += f", p95 {p95:.0f} ms"
        line += delta + f" ({new.p50_ms / p50:.2f}x p50)"
    return line


# ── CLI ──────────────────────────────────────────────────────────────────────

def env_default(name: str, fallback):
    """The setting from the environment, for the deploy's /etc/scangrade-perf.conf.

    The deploy sources that file and passes each PERF_* setting as an environment
    variable, so a gate that only reads argv silently ignores every word of it and
    runs on its argparse defaults. That is not a theoretical hazard: it is how the
    claims gate shipped — /etc/scangrade-claims.conf is generated with CLAIMS_BASE_URL
    and read into the environment, and claims_gate.py never looked, so every run
    ended at "no --base URL" and exit 2. A gate that can only say "could not
    measure" is a gate that is off, and it looks exactly like one that is on.
    """
    value = os.environ.get(name)
    return value if value not in (None, "") else fallback


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Refuse a release that is slower than the previous one at a reference load.")
    ap.add_argument("--harness", default=env_default("PERF_HARNESS", str(DEFAULT_HARNESS)))
    ap.add_argument("--roster", default=env_default("PERF_ROSTER", str(DEFAULT_ROSTER)))
    ap.add_argument("--base", default=env_default("PERF_BASE_URL", ""),
                    help="base URL to measure (must be https in production)")
    ap.add_argument("--sessions", type=int,
                    default=env_default("PERF_SESSIONS", DEFAULT_SESSIONS),
                    help="concurrent students at the reference load (small on purpose)")
    ap.add_argument("--teachers", type=int, default=env_default("PERF_TEACHERS", DEFAULT_TEACHERS))
    ap.add_argument("--duration", type=float,
                    default=env_default("PERF_DURATION", DEFAULT_DURATION))
    ap.add_argument("--baseline-file",
                    default=env_default("PERF_BASELINE", str(DEFAULT_BASELINE)))
    ap.add_argument("--evidence-file",
                    default=env_default("PERF_EVIDENCE", str(DEFAULT_EVIDENCE)))
    ap.add_argument("--latency-slack", type=float, default=DEFAULT_LATENCY_SLACK)
    ap.add_argument("--p95-slack", type=float, default=DEFAULT_P95_SLACK)
    ap.add_argument("--error-slack", type=float, default=DEFAULT_ERROR_SLACK_PCT)
    ap.add_argument("--quiet-ms", type=float, default=cg.BOX_QUIET_MS)
    ap.add_argument("--commit", default="", help="the release being measured (for the record)")
    ap.add_argument("--rebaseline", action="store_true",
                    help="measure and replace the baseline without comparing (deliberate reset)")
    ap.add_argument("--check", action="store_true",
                    help="validate the plumbing only: no load, no network")
    ap.add_argument("--json-out", default="", help="write the raw measurement here")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    # run_probe() in claims_gate reads these fields off a namespace; this is the
    # whole of the contract between the two modules.
    args.probe = argparse.Namespace(harness=args.harness, teachers=args.teachers,
                                    roster=args.roster, base=args.base,
                                    duration=args.duration)
    baseline_path = Path(args.baseline_file)
    baseline = load_baseline(baseline_path)

    if not Path(args.harness).exists():
        print(f"perf gate: CANNOT MEASURE — harness {args.harness} is missing")
        return EXIT_CANNOT_RUN
    students, teachers = cg.roster_supply(Path(args.roster))
    if students < args.sessions or teachers < args.teachers:
        print(f"perf gate: CANNOT MEASURE — {args.roster} holds {students} murid / "
              f"{teachers} guru, but {args.sessions} murid / {args.teachers} guru are needed. "
              "One account per session is required: reusing logins turns per-identity rate "
              "limiting into errors that look like the server's fault.")
        return EXIT_CANNOT_RUN
    if args.check:
        where = (f"baseline {baseline_path} from {baseline.get('commit')}"
                 if baseline else f"no baseline yet at {baseline_path}")
        print(f"perf gate: CHECK OK — harness present, roster holds {students} murid / "
              f"{teachers} guru, {where}")
        return EXIT_OK
    if not args.base:
        print("perf gate: CANNOT MEASURE — no --base URL. It must be https in production, "
              "because SESSION_COOKIE_SECURE means a plain-HTTP login cannot keep its cookie.")
        return EXIT_CANNOT_RUN

    shape = shape_of(args.sessions, args.teachers, args.duration, args.base)
    if baseline and not args.rebaseline:
        mismatch = shape_mismatch(baseline, shape)
        if mismatch:
            print(f"perf gate: CANNOT MEASURE — {mismatch}")
            return EXIT_CANNOT_RUN

    quiet, why = cg.box_is_quiet(args.base, args.quiet_ms)
    if not quiet:
        print(f"perf gate: CANNOT MEASURE — {why}")
        return EXIT_CANNOT_RUN

    print(f"perf gate: {why}; probing {args.sessions} concurrent students for "
          f"{args.duration:.0f}s" + ("" if baseline else " (no baseline yet)"))

    measured, log, rc = cg.run_probe(args.probe, args.sessions)
    if log.strip():
        print(log.rstrip())
    if measured is None:
        print(f"perf gate: CANNOT MEASURE — the harness did not complete (exit {rc})")
        return EXIT_CANNOT_RUN
    if cg.claim_latency(measured) is None:
        print("perf gate: CANNOT MEASURE — the run recorded no signed-in page load, so there "
              "is nothing to compare against the baseline")
        return EXIT_CANNOT_RUN
    unusable = cg.unusable_reason(measured, args.sessions)
    if unusable:
        print(f"perf gate: CANNOT MEASURE — {unusable}")
        return EXIT_CANNOT_RUN
    if args.json_out:
        try:
            Path(args.json_out).write_text(json.dumps(measured, indent=2) + "\n",
                                           encoding="utf-8")
        except OSError as e:
            print(f"perf gate: could not write {args.json_out} ({e}) — not fatal")

    latency = cg.claim_latency(measured)
    record = baseline_record(args.commit, shape, measured, latency)

    if baseline is None or args.rebaseline:
        note = "no baseline existed" if baseline is None else "--rebaseline was asked for"
        print(f"perf gate: OK — {note}, so this release IS the baseline: {describe(measured)}")
        print("perf gate: " + save_baseline(baseline_path, record))
        print("perf gate: " + cg.append_evidence(Path(args.evidence_file),
                                                 dict(record, verdict="baseline")))
        return EXIT_OK

    reasons = regression(baseline, measured, args.latency_slack, args.p95_slack, args.error_slack)
    confirmed = None
    if reasons:
        print("perf gate: first probe is slower than the baseline —")
        for r in reasons:
            print(f"    - {r}")
        print("perf gate: confirming with a second probe before refusing the release ...")
        again, log2, rc2 = cg.run_probe(args.probe, args.sessions)
        if again is None:
            print(f"perf gate: confirmation run did not complete (exit {rc2})")
        else:
            confirmed = regression(baseline, again, args.latency_slack, args.p95_slack,
                                   args.error_slack)
            print("perf gate: confirmation probe — " + describe(again, baseline))

    code, why_code = cg.verdict(reasons, confirmed, **VERDICT_WORDS)

    if code == EXIT_OK:
        print(f"perf gate: OK — {describe(measured, baseline)}")
        print("perf gate: " + save_baseline(baseline_path, record))
        print("perf gate: " + cg.append_evidence(Path(args.evidence_file), dict(record,
                                                                               verdict="pass")))
        return EXIT_OK

    if code == EXIT_CANNOT_RUN:
        print(f"perf gate: CANNOT MEASURE — {why_code}")
        # The baseline is untouched on purpose: an unconfirmed measurement must not
        # become the thing the next release is judged against.
        cg.append_evidence(Path(args.evidence_file), dict(record, verdict="unconfirmed"))
        return EXIT_CANNOT_RUN

    print(f"perf gate: REGRESSED — {why_code}")
    print("perf gate: the baseline is left as it was, so this does not become the new normal.")
    print("perf gate: find what the release changed that costs response time. If the change was "
          "deliberate (more work per page, a new feature worth its cost), re-baseline it on "
          "purpose with --rebaseline and say so in the commit message.")
    cg.append_evidence(Path(args.evidence_file), dict(record, verdict="regressed", reasons=reasons))
    return EXIT_REGRESSED


if __name__ == "__main__":
    sys.exit(main())
