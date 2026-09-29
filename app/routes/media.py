"""One question's media, served by the app under a link that belongs to a sitting.

The URL a page is handed looks like ``/media/<token>``, and the token is a signed
claim over (storage path, the user it was minted for, the paper, an expiry). This
route is the only thing that turns such a claim into bytes, and it is deliberately
the *only* place that does, because everything the arrangement is for is decided
here:

* **the claim is verified, never merely read.** ``verify_media_token`` refuses a
  malformed, unsigned, edited or expired token before its payload is looked at, so
  a caller cannot choose which object to fetch;
* **the sitting is checked.** A token minted for one user answers 403 for any other,
  which is what makes a URL copied out of DevTools worth nothing in a classmate's
  browser. The check is against ``g.user_id`` — the subject the *server* resolved
  from the session, never a value from the request;
* **the bytes never come from a public URL.** The app mints a short-lived signed
  Storage URL for its own request and streams the result, forwarding the caller's
  ``Range`` so seeking inside a track does not re-download it.

What this route deliberately does not do is re-check the exam's visibility on every
media request: the token could only have been minted by a page that had already
passed that check, it names the paper, and a database read per audio element is a
cost a 1-vCPU box should not pay for information the signature already carries.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request, current_app

from app.services import exam_media
from app.utils.auth import get_supabase, login_required

media_bp = Blueprint("media", __name__)


@media_bp.route("/media/<token>")
@login_required
def serve_media(token: str):
    claims = exam_media.verify_media_token(token)
    if not claims:
        # One answer for every way a token can be wrong — malformed, forged, edited
        # or expired. Naming which would tell a prober whether a payload they hold
        # is nearly a real one.
        return jsonify({"error": "Media link is invalid or has expired"}), 403

    subject = str(getattr(g, "user_id", "") or "")
    if not subject or claims["s"] != subject:
        return jsonify({"error": "This media link belongs to another sitting"}), 403

    try:
        return exam_media.stream_media(get_supabase(), claims["p"],
                                       request.headers.get("Range"))
    except Exception as exc:
        # Storage unreachable, object moved, or the signed URL refused. The pupil
        # gets a readable failure rather than a 500, and the reason reaches the log.
        current_app.logger.error("media fetch failed for %s: %s", claims["p"], exc)
        return jsonify({"error": "Media is temporarily unavailable"}), 502
