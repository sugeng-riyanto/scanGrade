from flask import Blueprint, render_template, current_app, flash, redirect
from app.utils.auth import login_required
from app.services.anti_cheat_service import (
    PENALIZED_VIOLATION_TYPES,
    calculate_graduated_penalty,
)

guide_bp = Blueprint("guide", __name__)

#: The settings the page documents. The defaults `calculate_graduated_penalty`
#: itself falls back to, here as explicit input, because a guide has to state the
#: numbers it is describing rather than inherit whatever the defaults happen to be.
GUIDE_PENALTY_SETTINGS = {
    "anti_cheat_enabled": True,
    "penalty_per_violation": 5,
    "max_violations": 5,
    "auto_submit_on_max": True,
}


def penalty_schedule(count: int = 5) -> list[dict]:
    """The graduated penalty, tabulated from the function that charges it.

    This page described a schedule the app no longer used — the old MCQ/Esai pool
    model, a first-violation figure of zero, a maximum that had moved — for a
    release after the change. Copying the numbers into the template again would
    recreate exactly that, so the table is *computed*: `--verify` on the guide is
    now a comparison of the page with `calculate_graduated_penalty`, which is the
    same function the student's submission is scored by.
    """
    rows = []
    for n in range(1, count + 1):
        result = calculate_graduated_penalty(n, GUIDE_PENALTY_SETTINGS)
        rows.append({
            "n": n,
            "cut": result["current_penalty_this_violation"],
            "total": result["penalty"],
            # The violation that ends the sitting, which is a property of the
            # settings and not of the count.
            "submits": bool(result.get("auto_submit")) and n == count,
        })
    return rows


@guide_bp.route("/skor")
@login_required
def guide_skor():
    try:
        return render_template("guide/skor.html",
                               penalty_schedule=penalty_schedule(),
                               charged_violations=len(PENALIZED_VIOLATION_TYPES))
    except Exception as e:
        current_app.logger.error("Guide skor error: %s", str(e), exc_info=True)
        flash("Terjadi kesalahan: " + str(e)[:100], "error")
        return redirect("/")
