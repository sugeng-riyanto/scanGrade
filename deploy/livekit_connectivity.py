"""Read-only LiveKit connection probe for an isolated ScanMeet staging host.

Run from the repository root after installing requirements-live.txt:
  python -m deploy.livekit_connectivity --env /private/staging.env

This validates configuration and credentials without creating/deleting rooms.
Never prints endpoint keys, secrets, access tokens or room participant data.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.services.live_policy import server_api_url
from deploy.livekit_preflight import audit, parse_env


async def probe(settings: dict[str, str]) -> None:
    """List only a deliberately nonexistent room; no writes to LiveKit."""
    from livekit import api

    client = api.LiveKitAPI(
        url=server_api_url(settings),
        api_key=settings["LIVEKIT_API_KEY"],
        api_secret=settings["LIVEKIT_API_SECRET"],
    )
    try:
        await client.room.list_rooms(
            api.ListRoomsRequest(names=["scangrade-staging-connectivity-probe"])
        )
    finally:
        await client.aclose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, type=Path,
                        help="private staging env (not committed)")
    args = parser.parse_args(argv)
    try:
        settings = parse_env(args.env.read_text(encoding="utf-8"))
    except OSError:
        print("FAIL: cannot read private staging configuration")
        return 2
    issues = audit(settings, pilot=True)
    if issues:
        for issue in issues:
            print("FAIL: " + issue)
        return 1
    try:
        asyncio.run(probe(settings))
    except Exception:
        # Avoid accidentally logging tokens or URLs from SDK exception messages.
        print("FAIL: LiveKit server connectivity/authorization; inspect private host diagnostics")
        return 1
    print("PASS: LiveKit RoomService is reachable and authenticated (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
