"""Produce deterministic, structurally valid but unusable Supabase JWT fixtures.

CI uses them only with https://example.invalid: no production/staging access,
no real key, no signing material and no JWT literal committed to Git history.
The values are written to GitHub Actions' environment-file for later steps.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def b64url(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def main() -> None:
    target = os.environ.get("GITHUB_ENV")
    if not target:
        raise SystemExit("Only GitHub Actions may generate these test-only credentials")
    if os.environ.get("SUPABASE_URL") != "https://example.invalid":
        raise SystemExit("Refusing to make CI JWT for a non-offline Supabase URL")
    token = ".".join(
        (b64url({"alg": "HS256"}), b64url({"sub": "ci-test"}), "test-only-signature")
    )
    with Path(target).open("a", encoding="utf-8") as output:
        for key in ("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY"):
            output.write(f"{key}={token}\n")


if __name__ == "__main__":
    main()
