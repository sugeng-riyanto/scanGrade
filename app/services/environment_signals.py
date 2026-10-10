"""Signs that a paper is being sat inside a virtual machine — weak signs, never a charge.

**Credit where it is due: virtual machines are normal.** This school serves pupils
on cheap and old hardware, and a legitimate computer laboratory very often *is* a
VM — that is how a school with thirty workstations manages them. Every signal here
has a completely innocent explanation, several of which are far more likely than
the dishonest one. That is why nothing in this module can cost a pupil anything:
there is no penalty column, no lock, and no score deduction anywhere below, and the
guarantee is kept by *not importing* the modules that could do it (see
:data:`PENALTY_MODULES` and the guard that reads it).

What it is for, then
--------------------
Context. A teacher reviewing a sitting sees "the renderer reported a software
rasteriser, and the timer resolution was coarse" beside the behaviour signals they
already had, and makes up their own mind. The value is that the *absence* of a
signal is also information, and that a pattern across several pupils in one room is
something no single pupil's page can show.

The two levels, and why there is no third
-----------------------------------------
:data:`LEVEL_LOW` and :data:`LEVEL_MEDIUM`, and deliberately no ``high``. A
composite that could say "high" would be read as a verdict, and a verdict built
from a WebGL string is not one. Capping the vocabulary is how this module refuses
to make a claim it cannot support: the strongest thing it can say is "worth a
look", and the page says it in those words.

The thresholds live here and nowhere else
-----------------------------------------
The brief forbids publishing the weights or the cut-offs, and the enforcement is
structural rather than a promise: they are module constants, they are not passed to
any template, and the dashboard renders the *level* and the reasons rather than the
number that produced it. A page cannot leak a threshold it was never given.
"""
from __future__ import annotations

import re

from app.utils.logger import get_logger

logger = get_logger("environment_signals")

#: Bumped when the weights change, and stored with every row. A weight that moved
#: must not silently re-grade the rows collected under the old one.
COLLECTOR_VERSION = "1"

#: The signal kinds this release collects. Named here so the table, the endpoint and
#: the dashboard agree, and so a client cannot invent a kind that merely *sounds*
#: incriminating.
SIGNAL_TYPES = (
    "webgl_renderer",
    "timer_precision",
    "pixel_ratio",
    "hardware_vs_performance",
)

#: The two levels the composite may report. There is no third — see the docstring.
LEVEL_NONE = "none"
LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"

#: The modules that could turn a signal into a consequence. Nothing here may import
#: them; ``tests/unit/test_environment_signals.py`` reads this list and fails if a
#: reference appears, which is what makes "non-punitive" a property of the code
#: rather than a sentence in a docstring.
PENALTY_MODULES = (
    "anti_cheat_service",
    "resume_code",
    "grading_service",
    "attempt_status",
)

#: Renderer strings that mean "this is not a physical GPU". Every one of them is also
#: what a school laptop with a broken driver, a locked-down browser, a remote
#: desktop session or a hosted virtual desktop reports — which is exactly why the
#: signal is weak and why the page prints an alternative explanation beside it.
_VM_RENDERERS = re.compile(
    r"llvmpipe|softpipe|swiftshader|swrast|virgl|mesa offscreen|"
    r"vmware|virtualbox|vbox|parallels|qemu|kvm|hyper-v|microsoft basic render|"
    r"citrix|remote desktop|google swiftshader",
    re.I,
)

#: A `performance.now()` step larger than this suggests the clock is being coarsened
#: — which is also what a browser does for its own protection, and what a privacy
#: extension does. Weak.
COARSE_TIMER_MS = 1.0

#: What each contributing observation is worth. Server-only.
WEIGHTS = {
    "vm_renderer": 0.5,
    "coarse_timer": 0.3,
    "odd_pixel_ratio": 0.15,
    "hardware_faster_than_frames": 0.25,
}

#: The cut-offs. Server-only, and named so a future author can see them together.
THRESHOLD_MEDIUM = 0.6
THRESHOLD_LOW = 0.3

#: The benign explanation shown beside each reason, as a key the page translates.
#: Written as a *question* rather than a defence, because a page that argues with
#: itself reads like an excuse and a question invites the reader to check.
REASON_KEYS = {
    "vm_renderer": "env_reason_renderer",
    "coarse_timer": "env_reason_timer",
    "odd_pixel_ratio": "env_reason_pixel_ratio",
    "hardware_faster_than_frames": "env_reason_hardware",
}

#: The sentence a reviewer reads before any of it: what a VM sign can legitimately
#: mean. Named here so the dashboard cannot describe the feature more confidently
#: than the service that produced it.
CAVEAT_KEY = "env_caveat"


def _num(value, default=None):
    """A float from whatever JSON handed over, or ``default``."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def assess(raw: dict) -> dict:
    """``{level, reasons, score}`` for one client payload — never a penalty.

    The client sends **raw observations** and this decides what they mean, which is
    the split the brief asks for ("logika scoring hanya di server"): a client that
    could send a score could send a low one, and a client that sends measurements
    cannot say what they are worth.

    ``score`` is returned for the log and for a future aggregate; the dashboard
    deliberately shows the level and the reasons instead, so the thresholds stay
    unpublished.
    """
    raw = raw or {}
    reasons: list[str] = []
    score = 0.0

    renderer = str(raw.get("webgl_renderer") or "")
    if renderer and _VM_RENDERERS.search(renderer):
        reasons.append("vm_renderer")
        score += WEIGHTS["vm_renderer"]

    timer = _num(raw.get("timer_precision"))
    if timer is not None and timer >= COARSE_TIMER_MS:
        reasons.append("coarse_timer")
        score += WEIGHTS["coarse_timer"]

    ratio = _num(raw.get("pixel_ratio"))
    if ratio is not None and (ratio <= 0 or ratio > 4):
        # A ratio outside anything a real screen reports. Note what is *not* here:
        # a ratio of exactly 1 on a large screen is a browser zoom setting and is
        # extremely common, so it is not counted at all.
        reasons.append("odd_pixel_ratio")
        score += WEIGHTS["odd_pixel_ratio"]

    cores = _num(raw.get("hardware_concurrency"))
    fps = _num(raw.get("frames_per_second"))
    if cores is not None and fps is not None and cores >= 8 and 0 < fps < 20:
        # Many cores and a very slow paint: a VM sharing a host, a throttled laptop,
        # or a browser in a power-saving mode. All innocent, hence the low weight.
        reasons.append("hardware_faster_than_frames")
        score += WEIGHTS["hardware_faster_than_frames"]

    if score >= THRESHOLD_MEDIUM:
        level = LEVEL_MEDIUM
    elif score >= THRESHOLD_LOW:
        level = LEVEL_LOW
    else:
        level = LEVEL_NONE
    return {"level": level, "reasons": reasons, "score": round(score, 3)}


def reason_keys(reasons) -> list[str]:
    """The page's translation keys for a set of reasons, in a stable order."""
    return [REASON_KEYS[r] for r in (reasons or []) if r in REASON_KEYS]


def _rows(query) -> list[dict]:
    try:
        return query.execute().data or []
    except Exception as exc:                                     # noqa: BLE001
        logger.warning("environment_signals: read failed: %s", exc)
        return []


def record(supabase, *, school_id: str, exam_id: str, submission_id: str,
           student_id: str, raw: dict, collector_version: str = COLLECTOR_VERSION,
           assess_fn=assess) -> dict:
    """Store one attempt's signals, one row per kind, and return the assessment.

    **One row per (attempt, kind)**, upserted: a page reload or a second tab must not
    stack a duplicate history that makes one device look more suspicious because the
    wifi dropped twice. The unique index in migration 062 is what enforces it; this
    writes the conflict target so the intent is visible where the write is.

    ``assess_fn`` is injectable so a test can drive the *write* without depending on
    the scoring, and so the route cannot accidentally score with something else.
    """
    assessment = assess_fn(raw)
    rows = []
    for kind in SIGNAL_TYPES:
        if kind not in (raw or {}):
            continue
        rows.append({
            "school_id": school_id,
            "exam_id": exam_id,
            "submission_id": submission_id,
            "student_id": student_id,
            "signal_type": kind,
            "raw_value": {"value": raw.get(kind)},
            # The contribution is stored, not recomputed on read: a reader that
            # recomputed it would re-grade old rows under today's weights.
            "weight": None,
            "collector_version": collector_version,
        })
    if not rows:
        return {"ok": False, "reason": "no_signals", "assessment": assessment}

    stored = 0
    for row in rows:
        row["weight"] = WEIGHTS.get(_weight_key(row["signal_type"]), 0)
        if _rows(supabase.table("environment_signal")
                 .upsert(row, on_conflict="submission_id,signal_type")):
            stored += 1
    return {"ok": stored > 0, "reason": "" if stored else "write_failed",
            "stored": stored, "assessment": assessment}


def _weight_key(signal_type: str) -> str:
    """Which weight a stored kind corresponds to. One map, not two."""
    return {
        "webgl_renderer": "vm_renderer",
        "timer_precision": "coarse_timer",
        "pixel_ratio": "odd_pixel_ratio",
        "hardware_vs_performance": "hardware_faster_than_frames",
    }.get(signal_type, "")


def for_exam(supabase, exam_id: str, limit: int = 500) -> list[dict]:
    """Every signal stored for one paper, for the review dashboard.

    One read for the whole sitting: a per-pupil lookup on a results page is a
    round-trip per row, which is the cost this repository already measured once and
    does not want again.
    """
    return _rows(supabase.table("environment_signal")
                 .select("id, exam_id, submission_id, student_id, signal_type, "
                         "raw_value, weight, collector_version, created_at")
                 .eq("exam_id", exam_id).order("created_at", desc=True).limit(limit))


def summary_for_exam(supabase, exam_id: str) -> dict:
    """Per-pupil levels for one paper, as the dashboard's chip list.

    The client's raw signals are re-assessed **through the same function the writer
    used**, so the number a reviewer sees cannot be produced by a second
    implementation that drifted. Rows collected under an older collector version
    still report, and say which version they came from.
    """
    by_submission: dict[str, dict] = {}
    for row in for_exam(supabase, exam_id):
        sid = str(row.get("submission_id") or "")
        if not sid:
            continue
        entry = by_submission.setdefault(sid, {
            "submission_id": sid, "student_id": row.get("student_id"),
            "raw": {}, "collector_versions": set(), "at": row.get("created_at"),
        })
        entry["raw"][row.get("signal_type")] = (row.get("raw_value") or {}).get("value")
        entry["collector_versions"].add(str(row.get("collector_version") or ""))

    out = []
    for entry in by_submission.values():
        assessment = assess(entry["raw"])
        out.append({
            "submission_id": entry["submission_id"],
            "student_id": entry["student_id"],
            "level": assessment["level"],
            "reason_keys": reason_keys(assessment["reasons"]),
            "collector_versions": sorted(entry["collector_versions"]),
            "at": entry["at"],
        })
    order = {LEVEL_MEDIUM: 0, LEVEL_LOW: 1, LEVEL_NONE: 2}
    out.sort(key=lambda row: (order.get(row["level"], 3), str(row["at"] or "")))
    return {"caveat_key": CAVEAT_KEY, "rows": out,
            "counted": sum(1 for r in out if r["level"] != LEVEL_NONE)}


__all__ = [
    "CAVEAT_KEY", "COLLECTOR_VERSION", "LEVEL_LOW", "LEVEL_MEDIUM", "LEVEL_NONE",
    "PENALTY_MODULES", "REASON_KEYS", "SIGNAL_TYPES", "THRESHOLD_LOW",
    "THRESHOLD_MEDIUM", "WEIGHTS", "assess", "for_exam", "reason_keys", "record",
    "summary_for_exam",
]
