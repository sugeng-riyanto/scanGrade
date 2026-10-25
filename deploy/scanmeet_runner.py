#!/usr/bin/env python3
"""Run ScanMeet's acceptance gates from a workstation, in one command.

Why this exists
---------------
The twelve media rows need three browsers and the webhook rows need an HTTP
client, and neither belongs on the box that serves live exams: staging's own host
is the production VPS, measured at 1 CPU and ~470 MB free during the run. So both
gates are taken **elsewhere** — and "elsewhere" used to mean a hand-written shell
script that named a temp directory, a hard-coded Windows Chrome path and a
`fixture.out` on the staging box. That is not reproducible by a second machine and
not something a reviewer can run.

This tool is that script, with the parts a script cannot do for you:

* **one env file** (`--env`) holds the URL, the exam, the four identities and the
  LiveKit pair. Nothing is read from a repository file, nothing is prompted for and
  no value is ever printed — the report names a key and whether it was present.
* **it refuses production by name.** The media gate has its own refusal; the
  webhook probe does not, and a signed webhook is a write to whatever host it is
  given. A URL under this repository's production domain is refused here unless
  `--allow-production` says otherwise.
* **it finds a browser.** `--chrome`, else `SG_CHROME`, else the platform's own
  install paths (Windows `Program Files`, macOS app bundle, the usual Linux
  binaries). A machine with none exits **2** and says so, rather than reporting a
  media gate that could never have measured anything.
* **one verdict.** Each gate's own exit code is kept (0 pass · 1 disagreed ·
  2 could not measure) and the worst wins, so `--webhook --media` answers with a
  single code and a line per gate.

Usage, from the repository root on any machine::

    python -m deploy.scanmeet_runner --env ~/scanmeet-staging.env --all --check
    python -m deploy.scanmeet_runner --env ~/scanmeet-staging.env --webhook --media --signoff

`--check` validates the environment and prints the plan: no browser, no request,
no subprocess. It is a modifier, not a gate, so it is written beside the gates the
run would take (`--all --check`), never on its own — a bare `--check` has nothing
to validate against and answers `nothing to run`. That is the mode a second machine should run first, because every
refusal below is cheaper to read than a three-browser run that was never going to
start.

Exit codes follow the other instruments here: 0 every gate passed · 1 a gate
disagreed · 2 could not measure (a missing key, no browser, or a gate that answered
2 itself).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from deploy.livekit_preflight import parse_env

#: The gates this runner drives, by the module that implements each.
GATES = {"webhook": "deploy.scanmeet_webhook_probe",
         "media": "deploy.scanmeet_media_gate"}

#: What every run needs, whatever gate is asked for.
REQUIRED = ("SCANMEET_STAGING_URL", "SCANMEET_EXAM_ID")

#: The four accounts the media rows are about (`email:password`).
IDENTITIES = ("SCANMEET_TEACHER", "SCANMEET_STUDENT", "SCANMEET_STUDENT2",
              "SCANMEET_OUTSIDER")

#: What the webhook probe signs with.
SIGNING = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")

#: Refused unless `--allow-production`: a signed webhook is a write, and the
#: media gate's own refusal is the other half of the same rule.
PRODUCTION_HOSTS = ("scangrade.web.id",)

#: Where a browser usually is, per platform. `SG_CHROME` and `--chrome` win over
#: these; this list only spares a second machine from having to discover it.
CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

#: The binaries a `PATH` lookup may find, in the order a browser is trusted.
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium",
                "chromium-browser", "chrome")


def host_of(url: str) -> str:
    """Return a normalized HTTPS hostname, or empty for unsafe/invalid URLs."""
    text = (url or "").strip()
    try:
        parts = urlsplit(text)
        if (parts.scheme.lower() != "https" or not parts.netloc
                or parts.username is not None or parts.password is not None
                or any(char.isspace() for char in text)):
            return ""
        # urlsplit.hostname handles ports, IPv6 and trailing DNS dots correctly.
        host = (parts.hostname or "").rstrip(".").lower()
        if not host or parts.port is None and parts.netloc.endswith(":"):
            return ""
        return host
    except ValueError:
        return ""


def production_url(url: str) -> bool:
    """Whether this host is one this repository calls production."""
    host = host_of(url)
    return any(host == name or host.endswith("." + name)
               for name in PRODUCTION_HOSTS)


def split_identity(value: str) -> tuple[str, str] | None:
    """`email:password` → its two halves, or `None` when it is not one.

    Split on the **first** colon: the fixture's password is
    `secrets.token_urlsafe`, which never emits one, so a second colon is a sign
    the value was pasted wrong rather than a password containing punctuation.
    """
    email, sep, password = (value or "").partition(":")
    if not sep or "@" not in email or not password:
        return None
    return email.strip(), password


def problems(settings: dict[str, str], gates: list[str],
             allow_production: bool = False) -> list[str]:
    """Everything that would stop this run, named before it starts.

    Returned rather than raised so `--check` can print all of them at once: a
    second machine usually has more than one thing missing.
    """
    found = []
    for key in REQUIRED:
        if not (settings.get(key) or "").strip():
            found.append(f"{key} is missing")
    url = settings.get("SCANMEET_STAGING_URL", "")
    if url and not host_of(url):
        found.append("SCANMEET_STAGING_URL must be a valid HTTPS URL without userinfo")
    if url and production_url(url) and not allow_production:
        found.append("SCANMEET_STAGING_URL names production — add --allow-production "
                     "only if you mean to write to it")
    if "media" in gates:
        for key in IDENTITIES:
            if not split_identity(settings.get(key, "")):
                found.append(f"{key} is not an email:password identity")
    if "webhook" in gates:
        for key in SIGNING:
            if not (settings.get(key) or "").strip():
                found.append(f"{key} is missing, and the probe signs with it")
    return found


def browser(settings: dict[str, str], explicit: str = "", *,
            which=shutil.which, exists=os.path.exists) -> str:
    """The browser to drive: the flag, then the environment, then the usual paths."""
    for candidate in (explicit, settings.get("SG_CHROME", "")):
        if candidate and exists(candidate):
            return candidate
    for candidate in CHROME_PATHS:
        if exists(candidate):
            return candidate
    for name in CHROME_NAMES:
        found = which(name)
        if found:
            return found
    return ""


def child_env(settings: dict[str, str]) -> dict[str, str]:
    """The environment a gate is handed: this process's, plus the run's values.

    Secrets travel here and never on a command line — an argument is visible in
    `ps` and in every log that records the call, and these are the credentials of
    accounts that can open a room.
    """
    env = dict(os.environ)
    for key in REQUIRED + IDENTITIES + SIGNING + ("SG_CHROME", "LIVEKIT_URL"):
        value = settings.get(key)
        if value:
            env[key] = value
    return env


def command(gate: str, settings: dict[str, str],
            extra: list[str] | None = None) -> list[str]:
    """The command for one gate: its module, its subjects, and this run's flags."""
    base = [sys.executable, "-m", GATES[gate]]
    if gate == "webhook":
        return base + ["--url", settings["SCANMEET_STAGING_URL"]]
    return base + ["--url", settings["SCANMEET_STAGING_URL"],
                   "--exam", settings["SCANMEET_EXAM_ID"]] + list(extra or [])


def run(gates: list[str], settings: dict[str, str], extra: list[str] | None = None, *,
        spawn=subprocess.run, root: Path | None = None) -> list[tuple[str, int]]:
    """Run the gates in order, keeping each one's own exit code."""
    env = child_env(settings)
    here = str(root or Path(__file__).resolve().parents[1])
    results = []
    for gate in gates:
        argv = command(gate, settings, extra)
        # ASCII only, deliberately: this prints on whatever console the runner
        # happens to have, and a box-drawing character raised UnicodeEncodeError
        # on a cp1252 one before the first gate had started. The same rule the
        # doc-link report learned the same way.
        print(f"-- {gate}: {' '.join(argv[2:])}")
        completed = spawn(argv, cwd=here, env=env)
        code = int(getattr(completed, "returncode", 2))
        results.append((gate, code))
    return results


def verdict(results: list[tuple[str, int]]) -> int:
    """The worst answer wins: could-not-measure is not a pass, and not a fail."""
    codes = [code for _gate, code in results]
    if not codes:
        return 2
    if 2 in codes:
        return 2
    if 1 in codes:
        return 1
    return 0


def main(argv=None, *, spawn=subprocess.run, which=shutil.which,
         exists=os.path.exists) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", required=True, type=Path,
                        help="private file with the URL, the exam, the identities "
                             "and the LiveKit pair")
    parser.add_argument("--webhook", action="store_true",
                        help="run the seven signed-webhook rows")
    parser.add_argument("--media", action="store_true",
                        help="run the twelve media rows (needs a browser)")
    parser.add_argument("--all", action="store_true", help="both gates")
    parser.add_argument("--check", action="store_true",
                        help="validate and print the plan; launches nothing")
    parser.add_argument("--allow-production", action="store_true",
                        help="permit a production host (the media gate still refuses)")
    parser.add_argument("--chrome", default="", help="path to the browser binary")
    # Passed straight through to the media gate: the runner adds no meaning to
    # them, and re-describing each one here is how the two drift apart.
    parser.add_argument("--signoff", action="store_true",
                        help="media: print the pasteable sign-off sheet")
    parser.add_argument("--json-out", default="",
                        help="media: write the reading to this file")
    parser.add_argument("--no-fake-devices", action="store_true",
                        help="media: use this machine's real camera")
    parser.add_argument("--keep-room", action="store_true",
                        help="media: leave the room open for inspection")
    parser.add_argument("--insecure", action="store_true",
                        help="skip certificate verification (self-signed staging)")
    args = parser.parse_args(argv)
    extra = []
    for flag in ("--signoff", "--no-fake-devices", "--keep-room", "--insecure"):
        if getattr(args, flag[2:].replace("-", "_")):
            extra.append(flag)
    if args.json_out:
        extra += ["--json-out", args.json_out]

    # `--all` runs the cheap gate first: a webhook refusal costs a second, and a
    # media run costs three browsers.
    gates = [name for name, wanted in (("webhook", args.webhook or args.all),
                                       ("media", args.media or args.all))
             if wanted]
    gates = list(dict.fromkeys(gates))
    if not gates:
        print("nothing to run: pass --webhook, --media or --all")
        return 2

    try:
        settings = parse_env(args.env.read_text(encoding="utf-8"))
    except OSError:
        print(f"cannot read {args.env} - the run's values live there, not in this "
              "repository")
        return 2

    found = problems(settings, gates, allow_production=args.allow_production)
    binary = browser(settings, args.chrome, which=which, exists=exists) \
        if "media" in gates else ""
    if "media" in gates and not binary:
        found.append("no Chrome/Chromium found — pass --chrome, or set SG_CHROME")

    print("ScanMeet acceptance runner")
    print(f"  gates    {', '.join(gates)}")
    print(f"  url      {settings.get('SCANMEET_STAGING_URL', '(missing)')}")
    print(f"  exam     {settings.get('SCANMEET_EXAM_ID', '(missing)')}")
    for key in IDENTITIES:
        present = "present" if split_identity(settings.get(key, "")) else "missing"
        print(f"  {key:<20} {present}")
    if "media" in gates:
        print(f"  browser  {binary or '(none)'}")

    if found:
        for problem in found:
            print(f"FAIL: {problem}")
        return 2
    if args.check:
        print("OK: every value this run needs is present; nothing was launched")
        return 0

    if binary:
        settings = dict(settings, SG_CHROME=binary)
    results = run(gates, settings, extra, spawn=spawn,
                  root=Path(__file__).resolve().parents[1])
    print()
    for gate, code in results:
        word = {0: "PASS", 1: "FAIL", 2: "COULD NOT MEASURE"}.get(code, f"exit {code}")
        print(f"  {gate:<8} {word}")
    code = verdict(results)
    print({0: "PASS: every gate passed",
           1: "FAIL: a gate disagreed",
           2: "COULD NOT MEASURE: a gate could not take its rows"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
