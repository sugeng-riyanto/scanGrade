"""Probe a deployed ScanMeet webhook with genuinely signed LiveKit deliveries.

This is the staging instrument, not a unit test: it talks to one running
deployment over HTTP and judges what that deployment answers. Every "valid"
request below is signed the way LiveKit signs one — an HS256 JWT whose `sha256`
claim is the base64 digest of the exact body, `iss` the API key, `exp` in the
future — so a PASS means the run's own `WebhookReceiver` accepted a delivery no
part of this script could have faked for free.

What it checks, and why each one is a real failure rather than a nuance:

    missing-signature   a delivery with no Authorization is 401
    tampered-body       a valid signature over a *different* body is 401
    wrong-secret        a signature from another secret is 401
    expired             a signature that expired an hour ago is 401
    oversized           a body over the route's 64 KiB cap is 413
    valid               a correct delivery is accepted (200 or 204)
    replay              the same delivery again is accepted idempotently, not 5xx

204 is a legitimate answer for `valid`/`replay`: it is what the route returns
for a well-signed event about a room this deployment has no session for, which
is still proof that the signature was verified (a bad one never reaches the
database at all). `--room` makes the probe use a real room name so the event is
expected to be *stored*, and then 204 is a finding.

Exit codes follow the other tools here: 0 every probe answered as expected,
1 one disagreed, 2 could not probe (missing URL, key or secret).

Usage:
    python -m deploy.scanmeet_webhook_probe --url https://staging.example.edu
    python -m deploy.scanmeet_webhook_probe --url ... --room sg_XXXX
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time

# The signing half of the optional staging set (`requirements-live.txt` pulls
# PyJWT in with `livekit-api`). Kept optional so this module still imports on a
# production venv: a traceback there names `jwt` and not the file that installs
# it, which is how a missing package reads as a broken probe.
try:  # pragma: no cover - both branches meet in `main()`
    import jwt
except ImportError:  # pragma: no cover
    jwt = None

try:
    import requests
except ImportError:  # offline environments may not install HTTP extras
    requests = None

TIMEOUT = 20
MAX_BODY = 65536


def body_for(kind: str, room: str, event_id: str) -> bytes:
    """A delivery shaped like the ones LiveKit sends."""
    event = {
        "event": kind,
        "id": event_id,
        "createdAt": int(time.time()),
        "room": {"name": room, "sid": "RM_probe_" + event_id[-6:]},
    }
    if kind.startswith("participant_"):
        event["participant"] = {"identity": "p_probe", "sid": "PA_probe"}
    return json.dumps(event, separators=(",", ":")).encode()


def sign(api_key: str, api_secret: str, body: bytes, *,
         expires_in: int = 300, digest_of: bytes | None = None) -> str:
    """The header LiveKit itself would send for ``body``."""
    hashed = digest_of if digest_of is not None else body
    return jwt.encode(
        {
            "iss": api_key,
            "exp": int(time.time()) + expires_in,
            "sha256": base64.b64encode(hashlib.sha256(hashed).digest()).decode(),
        },
        api_secret,
        algorithm="HS256",
    )


def post(url: str, body: bytes, *, auth: str | None, session=None) -> int:
    headers = {"Content-Type": "application/webhook+json"}
    if auth is not None:
        headers["Authorization"] = auth
    try:
        response = (session or requests).post(url, data=body, headers=headers,
                                              timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ProbeError(f"could not reach {url}: {exc}") from exc
    return response.status_code


class ProbeError(RuntimeError):
    """The deployment could not be asked, which is not the same as failing."""


def probes(url: str, api_key: str, api_secret: str, room: str | None,
           session=None) -> list[tuple[str, bool, str]]:
    """Run every probe; ``(name, ok, detail)`` per row."""
    endpoint = url.rstrip("/") + "/webhook/livekit"
    event_id = "evt_probe_" + secrets.token_hex(6)
    target_room = room or ("sg_probe_" + secrets.token_hex(4))
    accept = {200} | (set() if room else {204})
    rows: list[tuple[str, bool, str]] = []

    def record(name, code, expected, note=""):
        ok = code in expected
        rows.append((name, ok, f"HTTP {code} (expected {sorted(expected)})"
                               + (f" — {note}" if note else "")))

    # 1. No signature at all.
    code = post(endpoint, body_for("room_started", target_room, event_id),
                auth=None, session=session)
    record("missing-signature", code, {401})

    # 2. A signature that is real, over a body that is not the one sent.
    body = body_for("room_started", target_room, event_id)
    auth = sign(api_key, api_secret, body, digest_of=body + b" ")
    record("tampered-body", post(endpoint, body, auth=auth, session=session), {401})

    # 3. A signature from a different secret.
    auth = sign(api_key, api_secret + "-not-the-real-one", body)
    record("wrong-secret", post(endpoint, body, auth=auth, session=session), {401})

    # 4. A signature that expired (leeway is seconds, not hours).
    auth = sign(api_key, api_secret, body, expires_in=-3600)
    record("expired", post(endpoint, body, auth=auth, session=session), {401})

    # 5. A body past the route's cap, correctly signed: size is refused first.
    big = json.dumps({"event": "room_started", "id": event_id,
                      "padding": "x" * (MAX_BODY + 2048)}).encode()
    auth = sign(api_key, api_secret, big)
    record("oversized", post(endpoint, big, auth=auth, session=session), {413})

    # 6. A delivery that should be accepted.
    body = body_for("participant_joined", target_room, event_id)
    auth = sign(api_key, api_secret, body)
    first = post(endpoint, body, auth=auth, session=session)
    if first == 404:
        rows.append(("valid", False,
                     "HTTP 404 — Live is disabled on this deployment "
                     "(SCANGRADE_LIVE_ENABLED / SCANGRADE_LIVE_EXAM_ENABLED)"))
    elif first == 503:
        rows.append(("valid", False,
                     "HTTP 503 — the route answered 'Live not configured' or the "
                     "audit write failed; check the staging .env and the log"))
    else:
        record("valid", first, accept,
               "" if room else "204 is a known-room miss, signature still verified")

    # 7. The same delivery again: idempotent, never an error.
    record("replay", post(endpoint, body, auth=auth, session=session), accept,
           "the route's event_key makes a redelivery a no-op")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("SCANMEET_STAGING_URL", ""),
                        help="base URL of the deployment (no /webhook path)")
    parser.add_argument("--api-key", default=os.environ.get("LIVEKIT_API_KEY", ""))
    parser.add_argument("--api-secret", default=os.environ.get("LIVEKIT_API_SECRET", ""))
    parser.add_argument("--room", default="",
                        help="a room this deployment has an active session for; "
                             "without it 204 is the expected answer")
    args = parser.parse_args(argv)

    if jwt is None:
        print("COULD NOT PROBE: PyJWT is not installed in this interpreter.")
        print("Install the staging set: python -m pip install -r requirements-live.txt")
        return 2

    if requests is None:
        print("COULD NOT PROBE: requests is not installed; install requirements.txt")
        return 2

    missing = [name for name, value in (("--url", args.url),
                                        ("--api-key", args.api_key),
                                        ("--api-secret", args.api_secret))
               if not value]
    if missing:
        print("COULD NOT PROBE: " + ", ".join(missing) + " is empty.")
        print("The secret is the staging LiveKit API secret; it is never printed "
              "and never needed in the same command twice.")
        return 2

    try:
        rows = probes(args.url, args.api_key, args.api_secret, args.room or None)
    except ProbeError as exc:
        print("COULD NOT PROBE: " + str(exc))
        return 2

    failed = [r for r in rows if not r[1]]
    for name, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:18} {detail}")
    if failed:
        print(f"\n{len(rows) - len(failed)}/{len(rows)} probes answered as expected.")
        print("A signature probe failing means the deployment is not verifying what "
              "it thinks it is: do not open the room to pupils until it passes.")
        return 1
    print(f"\nPASS: {len(rows)}/{len(rows)} webhook probes answered as expected "
          f"on {args.url.rstrip('/')}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a script
    sys.exit(main())
