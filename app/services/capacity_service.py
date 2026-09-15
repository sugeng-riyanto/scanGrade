"""What this deployment has actually been measured to carry, read from the files.

`app/templates/landing.html` prints a capacity table and every row of it is
recomputed from `docs/measurements/` by `tests/unit/test_landing_claims.py`. This
module is the reader for the `/capacity` page, and it parses those artifacts and
nothing else. The guard in `tests/unit/test_capacity_page.py` deliberately keeps
its own parser rather than calling this one: a bug shared by both would then cancel
out, whereas two readers that must agree turn a bug on either side into a
disagreement — which is exactly what
`test_the_two_public_pages_agree_about_the_same_files` asserts.

**Adding a measurement is the whole update procedure.** Drop a new artifact in the
directory, commit it, and the page carries its row — there is no number in this
file to edit. That is the point: the 46,698 and 74,923 figures on the marketing
page survived for months because the template was the *only* place they existed,
and nothing could produce them.

Two honest gaps are kept visible rather than smoothed over:

* A measurement's date comes from the artifact itself when it carries one, from
  its git commit date when it does not, and is reported as *unknown* when neither
  is available. The file's mtime is deliberately not used: every fresh clone
  writes today's date onto every file, so a mtime would advertise month-old
  evidence as measured this morning.
* The two deploy gates leave their own measurements on the box
  (`/var/lib/scangrade-deploy/{claims,perf}/history.jsonl`). When those are not
  readable — a development machine, or a deploy that has never passed a gate —
  the page says so instead of pretending the section does not exist.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEASUREMENTS = REPO_ROOT / "docs" / "measurements"
DEFAULT_STATE = Path("/var/lib/scangrade-deploy")

# The box these numbers were taken on. Prose on the landing page states the same
# configuration, and `tests/unit/test_capacity_page.py` holds the two together —
# a capacity figure without its configuration is the original claim again.
MACHINE = {
    "host": "scangrade.web.id",
    "vcpu": 1,
    "memory_mb": 957,
    "workers": 3,
    "worker_class": "gevent",
    "region": "ap-southeast-1 (Singapore)",
    "database": "Supabase, same region",
    "measured_on": "2026-09-14",
    "ceiling_rps": 20,
    "bottleneck": "cpu",
}

# The largest measured rung that still holds 0% errors and a comfortable p95: the
# 50-student row (worst p95 3.7 s). At 100 the p95 rises to 4.2 s and at 150 to
# 15.2 s. This is a *recommendation derived from a row*, so the guard checks that
# the row exists, that it really is 0% errors, and that the landing page states
# the same limit — the two pages are not allowed to disagree.
COMFORTABLE_STUDENTS = 50

# Only student pages are what "N concurrent students" means. A login burst is a
# different claim and is compared separately (never folded in: it made a healthy
# app fail its own claims gate).
STUDENT_PAGE = re.compile(r"^GET /student/")

# `rung-050.json`, `locust-050.json`, `rung-500-endurance.txt` … The rung is in
# the file name because the harness's own JSON does not carry it.
RUNG_IN_NAME = re.compile(r"(?:^|[-_])(\d{3})(?:[-_.]|$)")

TOOL_NAMES = {
    "loadtest_concurrent.py": "loadtest_concurrent.py",
    "locust": "locustfile.py",
    "endurance": "loadtest_concurrent.py --duration",
}


def measurements_dir() -> Path:
    override = os.environ.get("SCANGRADE_MEASUREMENTS_DIR")
    return Path(override) if override else DEFAULT_MEASUREMENTS


def state_dir() -> Path:
    override = os.environ.get("SCANGRADE_DEPLOY_STATE_DIR")
    return Path(override) if override else DEFAULT_STATE


# ── formatting: the exact strings the landing page publishes ────────────────

def fmt_ms_range(values) -> str:
    """Milliseconds below a second, seconds above — the page's two formats."""
    if not values:
        return "-"
    lo, hi = min(values), max(values)
    if hi < 1000:
        return f"{lo:.0f}\u2013{hi:.0f} ms"
    return f"{lo / 1000:.1f}\u2013{hi / 1000:.1f} s"


def fmt_p95(value_ms) -> str:
    return "\u2264 -" if value_ms is None else f"\u2264 {value_ms / 1000:.1f} s"


def fmt_seconds(value_ms) -> str:
    return "-" if value_ms is None else f"{value_ms / 1000:.2f} s"


# ── reading one artifact ────────────────────────────────────────────────────

def _rung_from_name(name: str):
    match = RUNG_IN_NAME.search(Path(name).stem)
    return int(match.group(1)) if match else None


def _git_date(path: Path) -> str:
    """When this artifact entered the repository, from git.

    Not the mtime: git does not preserve it, so a fresh clone stamps every file
    with the checkout time and the page would claim the evidence was measured
    today. A missing git (or a file outside the repository) is not an error —
    the caller reports the date as unknown.
    """
    try:
        rel = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%cs", "--", rel],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git date for %s failed: %s", rel, e)
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _measurement(name: str, path: Path):
    """One artifact as a dict, or None when it is not a measurement of a rung.

    A file counts as a measurement only if it yields per-page figures. Everything
    else in `docs/measurements/` (`README.md`, the console transcript, the box's
    own samples over loopback) is listed as a supporting file instead of being
    silently dropped: a reader can see the whole set.
    """
    rung = _rung_from_name(name)
    if not rung:
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("capacity: cannot read %s: %s", name, e)
        return None

    data = None
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return None

    if isinstance(data, dict) and data.get("per_endpoint"):
        return _from_json(name, rung, data)
    if path.suffix == ".txt":
        return _from_endurance_text(name, rung, text)
    return None


def _from_json(name: str, rung: int, data: dict):
    pages = {k: v for k, v in (data.get("per_endpoint") or {}).items()
             if STUDENT_PAGE.match(k)}
    if not pages:
        return None
    latency = data.get("latency_ms") or {}
    return {
        "name": name,
        "rung": rung,
        "tool": TOOL_NAMES.get(data.get("tool") or "", data.get("tool")
                               or "loadtest_concurrent.py"),
        "at": str(data.get("measured_at") or "")[:10],
        "at_source": "artifact" if data.get("measured_at") else "",
        "base": data.get("base") or "",
        "duration_s": data.get("duration_s") or data.get("wall_s") or 0,
        "sessions": int(data.get("logins_ok") or data.get("sessions_spawned") or 0),
        "requests_total": int(data.get("requests_total") or 0),
        "logins_ok": int(data.get("logins_ok") or 0),
        "logins_failed": int(data.get("logins_failed") or 0),
        "identity_checked": int(data.get("identity_checked") or 0),
        "identity_ok": int(data.get("identity_ok") or 0),
        "rate_limited_429": int(data.get("rate_limited_429") or 0),
        "server_errors_5xx": int(data.get("server_errors_5xx") or 0),
        "error_rate_pct": float(data.get("error_rate_pct") or 0.0),
        "p50_ms": [float(v["p50"]) for v in pages.values()],
        "p95_ms": [float(v["p95"]) for v in pages.values()],
        "overall_p50_ms": float(latency.get("p50") or 0.0) or None,
        "overall_p95_ms": float(latency.get("p95") or 0.0) or None,
        "pages": sorted(k.replace("GET ", "") for k in pages),
    }


ENDURANCE_ROW = re.compile(r"^(GET /student/\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s")


def _footer_number(text: str, label: str):
    """A number off the run's own summary footer (`sessions launched : 500`).

    Read from the summary rather than reconstructed from the per-endpoint tables:
    the footer is the run's own account of itself, and it is where `logins
    succeeded / failed` and `429 rate-limited` are stated in one place.
    """
    match = re.search(rf"^{re.escape(label)}\s*:\s*([\d.]+)", text, re.MULTILINE)
    return float(match.group(1)) if match else None


def _from_endurance_text(name: str, rung: int, text: str):
    """The 500* row: a 10-minute run whose artifact *is* the printed table."""
    p50, p95 = [], []
    for line in text.splitlines():
        match = ENDURANCE_ROW.match(line)
        if match:
            p50.append(float(match.group(3)))
            p95.append(float(match.group(4)))
    if not p50:
        return None

    errors = re.search(r"error rate\s+:.*=\s*([\d.]+)%", text)
    identity = re.search(r"identity verified\s*:\s*(\d+)\s*/\s*(\d+)", text)

    def num(label):
        value = _footer_number(text, label)
        return int(value) if value is not None else 0

    return {
        "name": name,
        "rung": rung,
        "tool": TOOL_NAMES["endurance"],
        "at": "",
        "at_source": "",
        "base": "https://scangrade.web.id",
        "duration_s": _footer_number(text, "wall clock") or 0.0,
        "sessions": num("logins succeeded") or num("sessions launched"),
        "requests_total": num("TOTAL requests"),
        "logins_ok": num("logins succeeded"),
        "logins_failed": _footer_failed(text),
        "identity_checked": int(identity.group(2)) if identity else 0,
        "identity_ok": int(identity.group(1)) if identity else 0,
        "rate_limited_429": num("429 rate-limited"),
        "server_errors_5xx": num("5xx server errors"),
        "error_rate_pct": float(errors.group(1)) if errors else 0.0,
        "p50_ms": p50,
        "p95_ms": p95,
        "overall_p50_ms": None,
        "overall_p95_ms": None,
        "pages": [],
    }


def _footer_failed(text: str) -> int:
    """`logins succeeded   : 174   failed: 326` — two numbers on one line."""
    match = re.search(r"^logins succeeded\s*:\s*\d+\s+failed:\s*(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else 0


# ── the report ──────────────────────────────────────────────────────────────

def _deploy_history():
    """The last measurement each deploy gate recorded on this box, if readable.

    These are the machine-local counterparts of `docs/measurements/`: the perf
    gate runs a small reference load **on every release**, so this is what
    updates itself without anyone running a test.
    """
    out = {"available": False, "claims": None, "perf": [], "note": ""}
    claims = state_dir() / "claims" / "history.jsonl"
    perf = state_dir() / "perf" / "history.jsonl"
    found = False

    try:
        if claims.is_file():
            last = [json.loads(l) for l in claims.read_text(encoding="utf-8").splitlines() if l.strip()]
            if last:
                record = last[-1]
                measured = record.get("measured") or {}
                advertised = record.get("advertised") or {}
                latency = measured.get("latency_ms") or {}
                out["claims"] = {
                    "at": str(record.get("at") or "").replace("T", " ")[:19],
                    "verdict": record.get("verdict") or "",
                    "reason": (record.get("reason") or "")[:160],
                    "students": advertised.get("students"),
                    "published_p50_ms": advertised.get("p50_ms"),
                    "published_p95_ms": advertised.get("p95_ms"),
                    "probed_sessions": record.get("sessions_probed"),
                    "p50_ms": latency.get("p50"),
                    "p95_ms": latency.get("p95"),
                    "error_rate_pct": measured.get("error_rate_pct"),
                    "rates": len(measured.get("per_endpoint") or {}),
                }
                found = True
    except (OSError, ValueError) as e:
        logger.debug("capacity: claims history unreadable: %s", e)

    try:
        if perf.is_file():
            rows = [json.loads(l) for l in perf.read_text(encoding="utf-8").splitlines() if l.strip()]
            for record in rows[-6:]:
                latency = record.get("latency") or {}
                shape = record.get("shape") or {}
                out["perf"].append({
                    "commit": (record.get("commit") or "")[:7],
                    "at": str(record.get("measured_at") or "").replace("T", " ")[:19],
                    "sessions": shape.get("sessions"),
                    "duration_s": shape.get("duration"),
                    "p50_ms": latency.get("p50_ms"),
                    "p95_ms": latency.get("p95_ms"),
                    "endpoints": latency.get("endpoints"),
                    "samples": latency.get("samples"),
                    "error_pct": record.get("error_pct"),
                    "requests_total": record.get("requests_total"),
                    "server_errors_5xx": record.get("server_errors_5xx"),
                })
            found = found or bool(out["perf"])
    except (OSError, ValueError) as e:
        logger.debug("capacity: perf history unreadable: %s", e)

    out["available"] = found
    if not found:
        out["note"] = (f"no gate history at {state_dir()} — this box has not run a "
                       f"deploy gate since the gates started recording")
    return out


def build() -> dict:
    """Parse the directory into rows, grouped by rung. No I/O beyond that."""
    directory = measurements_dir()
    measurements, supporting = [], []

    try:
        files = sorted(p for p in directory.iterdir() if p.is_file())
    except OSError as e:
        logger.warning("capacity: %s is unreadable: %s", directory, e)
        files = []

    for path in files:
        if path.name == "README.md":
            supporting.append({"name": path.name, "bytes": path.stat().st_size})
            continue
        read = _measurement(path.name, path)
        if read is None:
            supporting.append({"name": path.name, "bytes": path.stat().st_size})
            continue
        if not read["at"]:
            git_date = _git_date(path)
            read["at"] = git_date
            read["at_source"] = "git" if git_date else "unknown"
        read["refused"] = max(0, read["rung"] - read["logins_ok"]) if read["rung"] else 0
        # Formatted here, once, so the page prints the same strings the guards
        # compare against instead of re-deriving a range in Jinja.
        read["p50_text"] = fmt_ms_range(read["p50_ms"])
        read["p95_text"] = fmt_p95(max(read["p95_ms"]) if read["p95_ms"] else None)
        measurements.append(read)

    # A rung may have several independent measurements of the same load
    # (`rung-050.json` and `locust-050.json`). The published bound is the worst
    # figure any of them produced — never the better one.
    rungs = []
    for students in sorted({m["rung"] for m in measurements}):
        group = [m for m in measurements if m["rung"] == students]
        p50 = [v for m in group for v in m["p50_ms"]]
        p95 = [v for m in group for v in m["p95_ms"]]
        worst_p95 = max(p95) if p95 else None
        rungs.append({
            "students": students,
            "p50_text": fmt_ms_range(p50),
            "p95_text": fmt_p95(worst_p95),
            "worst_p95_ms": worst_p95,
            "error_text": f"{max(m['error_rate_pct'] for m in group):g}%",
            "error_rate_pct": max(m["error_rate_pct"] for m in group),
            "artifacts": [m["name"] for m in group],
            "harnesses": sorted({m["tool"] for m in group}),
            "dates": sorted({m["at"] for m in group if m["at"]}),
            "refused": max(m["refused"] for m in group),
            "server_errors_5xx": max(m["server_errors_5xx"] for m in group),
            "requests_total": sum(m["requests_total"] for m in group),
            "p95_samples": len(p95),
        })

    newest = max((m["at"] for m in measurements if m["at"]), default="")
    # The row the recommendation is derived from, so the page can print the figure
    # next to it instead of restating it (a restated number is a number that can
    # drift away from the file).
    comfort = next((r for r in rungs if r["students"] == COMFORTABLE_STUDENTS), None)
    return {
        "machine": MACHINE,
        "comfortable": COMFORTABLE_STUDENTS,
        "comfortable_row": comfort,
        "rungs": rungs,
        "measurements": sorted(measurements, key=lambda m: (m["rung"], m["name"])),
        "supporting": supporting,
        "deploy": _deploy_history(),
        "measured_at": newest,
        "directory": directory.name,
        "count": len(measurements),
    }


REPORT_KEY = "capacity:report:v1"


def invalidate() -> None:
    """Drop the cached report.

    For a measurement committed while the app is serving (the TTL would otherwise
    hide the new row for up to five minutes) and for tests that point the reader
    at a fixture directory.
    """
    from app.utils.kv_cache import cache_delete

    cache_delete(REPORT_KEY)


def report() -> dict:
    """The report, cached for a few minutes.

    The page is public and the box that serves it has one vCPU, so the parse (six
    files, plus a `git log` per artifact that needs a date) is shared across
    requests rather than repeated for every visitor. The artifacts only change
    when someone commits, and the TTL is short enough that a committed run shows
    up while the person who ran it is still looking at the page.
    """
    from app.utils.req_cache import ttl

    def load():
        try:
            return build()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("capacity: report failed: %s", e)
            return {"machine": MACHINE, "comfortable": COMFORTABLE_STUDENTS,
                    "rungs": [], "measurements": [], "supporting": [],
                    "deploy": {"available": False, "claims": None, "perf": [],
                               "note": str(e)[:120]},
                    "measured_at": "", "directory": measurements_dir().name, "count": 0}

    return ttl(REPORT_KEY, 300, load)


def artifact_path(name: str):
    """The path of one named artifact, or None.

    Two locks, and the mutation run on the guard showed which one is load-bearing:
    the same call with only the first still refuses `../rung-777.json`, and with
    *neither* it serves a file from the parent directory. The whitelist keeps the
    route serving exactly what the page displays; the resolve check is what makes
    that a boundary rather than a string comparison.

    (A hostile name rarely reaches here through the URL at all — Werkzeug
    normalises the path — which is why the guard tests this function directly as
    well as the route.)
    """
    data = report()
    known = {m["name"] for m in data["measurements"]}
    known |= {s["name"] for s in data["supporting"]}
    if name not in known:
        return None
    path = measurements_dir() / name
    try:
        if not path.resolve().is_relative_to(measurements_dir().resolve()):
            return None
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def published_rungs() -> dict:
    """{students: (p50, p95, errors)} — what the artifacts say, as text."""
    return {r["students"]: (r["p50_text"], r["p95_text"], r["error_text"])
            for r in report()["rungs"]}
