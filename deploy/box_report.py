#!/usr/bin/env python3
"""One block of text that answers "what is this box doing" without a login.

The three readings an operator is asked for when something is wrong live in
different places, and each one has a way of being wrong that the others cannot see:

* **the deploy journal** says what the runner tried and refused. It is the only
  place a gate's own sentence exists, and it is also the place nobody looks when
  every page answers 200.
* **the arm check** (`deploy/arm-auto-deploy.sh --check`) says whether the box is
  running the checkout's deploy logic or a copy of it, and whether each gate has
  what it needs. A wrong answer here looks exactly like a right one from outside.
* **the served commit** is published on `/health`, and it is the reading the
  other two cannot substitute for: a merge can land, the reload do nothing, and
  every file on the box still describe the release that never loaded. It is also
  *per worker* -- the value is frozen when a worker imports, so a box can answer
  two different commits within one second.

Collecting them by hand is four commands, three of them with a flag nobody
remembers, and the result is pasted into a chat where the half that was skipped is
invisible. This tool prints them as one ASCII block with the skipped part named:
an input it could not read is a line under `not measured`, never an omission. It
is read-only -- it starts no service, takes no lock on the checkout, writes no
file, and needs no root.

    python3 deploy/box_report.py
    python3 deploy/box_report.py --repo /opt/scangrade --base http://127.0.0.1:8000
    python3 deploy/box_report.py --json > now.json   # the same reading, diff-able

Exit codes follow the other tools here: 0 every input was read, 1 the block was
produced but at least one input could not be read (it is named), 2 neither the app
nor the checkout answered -- there was nothing to report on.

The text block is the thing a person pastes. `--json` is the same reading for a
machine: one object, `sort_keys` so the key order is a property of the report rather
than of the run, tallies as maps sorted by key (a Counter's own order is the order
the samples happened to arrive, which would make two readings of an unchanged box
differ), and `schema` so a comparison of two files can refuse a shape it does not
know. Every key the block prints is in it, plus `exit` and `gaps` -- the two things
a diff wants without parsing prose -- so `diff old.json now.json` answers "what
changed on this box" instead of "what changed in the sampling". Prose stays folded
to ASCII in the block; the JSON keeps the characters as they came.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import pathlib
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

#: Where git may be. The gates run under a service manager whose `PATH` is not a
#: login shell's, so `git` on `PATH` is not a safe assumption -- the same list
#: `app/utils/build_info.py` carries.
GIT_FALLBACKS = ("/usr/bin/git", "/bin/git", "/usr/local/bin/git")

#: The variables git exports to whatever it spawns. Cleared before this tool runs
#: anything, so a probe started from a hook answers about the checkout it was
#: pointed at rather than about the hook's index. `deploy/doc_links.py` keeps the
#: same list for the same reason.
GIT_REPO_ENV = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

DEFAULT_REPO = "/opt/scangrade"
DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_UNIT = "scangrade-deploy"
DEFAULT_STATE_DIR = "/var/lib/scangrade-deploy"
DEFAULT_SAMPLES = 15
DEFAULT_JOURNAL_LINES = 40
DEFAULT_SINCE = "6 hours ago"

#: How much of the journal to *read* before deciding what to print. The timer ticks
#: every two minutes, so a refusal an hour ago sits a long way above a 40-line tail
#: -- and the refusal is the line somebody is looking for. Reading wide and printing
#: narrow is what keeps the digest honest without pasting a screen of idle ticks.
JOURNAL_SCAN = 2000

#: The lines of a journal worth re-printing above the tail: one of these words is
#: why somebody is reading the journal at all.
JOURNAL_VERDICT = re.compile(
    r"(REACHED|FAILED|refuse|quarantin|rollback|rolled back|could not measure"
    r"|served commit|NOT ARMED|ARMED)",
    re.I)

#: How `deploy/arm-auto-deploy.sh --check` says what it found, by exit code. Its
#: header is the source of these; the words are not invented here.
ARM_VERDICTS = {
    0: "ARMED",
    1: "NOT ARMED",
    2: "refused (no way to elevate, or no terminal to ask)",
    3: "refused (installer or roster missing)",
}

MAX_LINE = 150

#: Bumped when a key in `--json` changes meaning. A tool comparing two readings
#: can then refuse a pair it would otherwise read as the same shape, which is the
#: only failure mode a diff of two reports has that looks like a clean answer.
JSON_SCHEMA = 1


def _ascii(text) -> str:
    """The block is pasted into a chat, so it is ASCII on purpose.

    A journal line can carry anything -- a curly quote from a commit subject, an em
    dash from a gate's message, a filename in a script the box does not have a font
    for. Folding the punctuation this repository actually writes and escaping the
    rest is what keeps the report readable where it is pasted instead of failing
    there; an unencodable character once crashed a guard on a cp1252 console.
    """
    folded = (str(text)
              .replace("\u2014", "--").replace("\u2013", "-")
              .replace("\u2018", "'").replace("\u2019", "'")
              .replace("\u201c", '"').replace("\u201d", '"')
              .replace("\u2026", "..."))
    return folded.encode("ascii", "backslashreplace").decode("ascii")


def _clip(text, width: int = MAX_LINE) -> str:
    line = _ascii(text).replace("\t", " ").rstrip()
    return line if len(line) <= width else line[:width - 3] + "..."


def _resolved(path) -> str:
    """`safe.directory` is compared against the path git opened, after links.

    An unresolved spelling is accepted as configuration and then matches nothing,
    which is a silent no -- the shape this whole reading exists to avoid.
    """
    try:
        return os.path.realpath(str(path))
    except (OSError, RuntimeError):
        return str(path)


def git_binary():
    found = shutil.which("git")
    if found:
        return found
    for path in GIT_FALLBACKS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _default_run(argv, env=None, timeout=10):
    """(returncode, stdout, stderr). Never raises: a command that cannot run is a reason."""
    environ = dict(os.environ)
    for name in GIT_REPO_ENV:
        environ.pop(name, None)
    if env:
        environ.update(env)
    try:
        # Decoded as UTF-8 rather than through the locale: the same command answers
        # on a box that runs `C.UTF-8` and on one that does not, and a gate's em
        # dash read as cp1252 arrives as mojibake that no folding can undo.
        done = subprocess.run(list(argv), capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=environ)
    except (OSError, subprocess.SubprocessError) as exc:
        return 255, "", "%s: %s" % (type(exc).__name__, exc)
    return (done.returncode, (done.stdout or "").strip(), (done.stderr or "").strip())


def _http_get(url, timeout=5):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "scangrade-box-report",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


# ── the served commit, per worker ────────────────────────────────────────────

def read_health(base, samples=DEFAULT_SAMPLES, opener=None, path="/health"):
    """Sample `/health` and tally the commits the workers report.

    One request is not a reading of the box: `build_info` freezes its answer at
    import, so each worker can name a different commit, and a single sample reports
    whichever worker answered. Asking several times is both how the disagreement is
    seen and how a genuinely blind worker is counted rather than mistaken for the
    whole box.

    The sampling is no longer the *only* way to see it, and the block the app now
    publishes (`commit_readers`, see `app/utils/commit_readers.py`) is kept
    separately in `readers`: it is the app's own count of every worker, taken from
    the markers written at import, so one sample can be a reading of the box. It is
    stored as it came, never merged into the tallies above — the two answer
    different questions (samples that came back without a commit, versus workers
    that cannot name one), and a report that averaged them would state neither.
    """
    opener = opener or _http_get
    url = str(base).rstrip("/") + path
    out = {
        "available": False, "url": url, "samples": 0, "answered": 0, "blind": 0,
        "served": collections.Counter(), "reasons": collections.Counter(),
        "loaded_at": collections.Counter(), "errors": collections.Counter(),
        "workers": None, "uptime_ms": None, "detail": None, "readers": None,
    }
    for _ in range(max(1, int(samples))):
        out["samples"] += 1
        try:
            _status, body = opener(url)
        except urllib.error.HTTPError as exc:
            out["errors"]["HTTP %s" % exc.code] += 1
            continue
        except Exception as exc:  # urllib raises URLError, socket errors, timeouts
            out["errors"][type(exc).__name__] += 1
            continue
        try:
            data = json.loads(body)
        except ValueError:
            out["errors"]["not_json"] += 1
            continue
        out["answered"] += 1
        out["available"] = True
        readers = data.get("commit_readers")
        if isinstance(readers, dict):
            # Last one wins, and any one is enough: this block is the app's count of
            # the box, not a sample of it. An app from a release that predates it
            # answers without the block, and `None` is then the honest reading.
            out["readers"] = readers
        commit = data.get("commit") or {}
        short = commit.get("commit") or None
        if short:
            out["served"][short] += 1
        else:
            out["blind"] += 1
            out["reasons"][commit.get("reason_key") or "no reason given"] += 1
        if commit.get("loaded_at"):
            out["loaded_at"][commit["loaded_at"]] += 1
        workers = data.get("workers")
        if workers is not None:
            out["workers"] = str(workers)
        uptime = data.get("uptime_ms")
        if isinstance(uptime, (int, float)):
            out["uptime_ms"] = int(uptime)
    if not out["answered"]:
        seen = ", ".join("%s x%s" % (k, v) for k, v in out["errors"].items()) or "no reason"
        out["detail"] = "no answer from %s (%s)" % (url, seen)
    return out


# ── the checkout ─────────────────────────────────────────────────────────────

def _git_probe(git, repo, args, run, exempt):
    argv = [git, "--no-optional-locks"]
    if exempt:
        argv += ["-c", "safe.directory=%s" % _resolved(repo)]
    argv += ["-C", str(repo), *args]
    return run(argv)


def read_checkout(repo, run=None, git=None):
    """HEAD, the upstream it is measured against, and whether git would read either.

    The plain probe exists to say *why* a checkout is unreadable: git 2.35.2 refuses
    one whose owner is not the reading user, and that sentence is the difference
    between "the deploy user owns this and the app does not" and "the tree is
    broken". The exempted probe is the one whose answers are reported.
    """
    run = run or _default_run
    if git is None:
        git = git_binary()
    repo = pathlib.Path(repo)
    out = {
        "path": str(repo), "git_state": "unknown", "detail": None,
        "exemption": "unknown", "head": None, "subject": None, "branch": None,
        "origin": None, "behind": None, "ahead": None,
    }
    if not git:
        out["git_state"] = "no_git"
        return out
    if not (repo / ".git").exists():
        out["git_state"] = "not_a_checkout"
        return out

    plain_rc, _plain_out, plain_err = _git_probe(
        git, repo, ["rev-parse", "HEAD"], run, exempt=False)
    rc, head, err = _git_probe(git, repo, ["rev-parse", "HEAD"], run, exempt=True)
    refused = "dubious ownership" in (_ascii(plain_err) + _ascii(err))
    if plain_rc != 0 and refused:
        out["exemption"] = "required" if rc == 0 else "insufficient"
    else:
        out["exemption"] = "not required"
    if rc != 0 or not head:
        out["git_state"] = "unreadable"
        out["detail"] = err or plain_err or None
        return out

    out["git_state"] = "readable"
    out["head"] = head
    rc, subject, _ = _git_probe(git, repo, ["log", "-1", "--format=%s"], run, exempt=True)
    out["subject"] = subject if rc == 0 and subject else None
    rc, branch, _ = _git_probe(git, repo, ["rev-parse", "--abbrev-ref", "HEAD"],
                               run, exempt=True)
    out["branch"] = branch if rc == 0 and branch else None
    rc, origin, _ = _git_probe(git, repo, ["rev-parse", "--short", "origin/main"],
                               run, exempt=True)
    if rc != 0 or not origin:
        return out
    out["origin"] = origin
    rc, behind, _ = _git_probe(git, repo, ["rev-list", "--count", "HEAD..origin/main"],
                               run, exempt=True)
    out["behind"] = _count(behind) if rc == 0 else None
    rc, ahead, _ = _git_probe(git, repo, ["rev-list", "--count", "origin/main..HEAD"],
                              run, exempt=True)
    out["ahead"] = _count(ahead) if rc == 0 else None
    return out


def _count(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


# ── the last release the runner recorded ─────────────────────────────────────

def read_last_release(path):
    """Three lines the runner writes: the commit, when, and the recovery point.

    "Not there" and "there but unreadable" are different answers -- the file is
    cleared on purpose by the recovery lever, so absence is a state, and a record
    that cannot be read is not evidence of absence.
    """
    path = pathlib.Path(path)
    out = {"path": str(path), "state": "absent", "commit": None, "at": None,
           "restore": None, "detail": None}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return out
    except OSError as exc:
        out["state"] = "unreadable"
        out["detail"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        out["state"] = "empty"
        return out
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", lines[0]):
        out["state"] = "malformed"
        out["detail"] = lines[0]
        return out
    out["state"] = "recorded"
    out["commit"] = lines[0]
    out["at"] = lines[1] if len(lines) > 1 else None
    out["restore"] = lines[2] if len(lines) > 2 else None
    return out


# ── the deploy journal ───────────────────────────────────────────────────────

def read_journal(unit=DEFAULT_UNIT, since=DEFAULT_SINCE, lines=DEFAULT_JOURNAL_LINES,
                 run=None):
    run = run or _default_run
    scan = max(JOURNAL_SCAN, int(lines))
    out = {"available": False, "unit": unit, "since": since, "entries": 0,
           "tail": [], "verdicts": [], "detail": None}
    rc, stdout, stderr = run([
        "journalctl", "-u", unit, "--since", since, "--no-pager",
        "-n", str(scan), "-o", "short-iso",
    ])
    if rc != 0:
        reason = stderr or stdout or "exit %s" % rc
        out["detail"] = ("journalctl could not be run (%s)" % reason if rc == 255
                         else reason)
        return out
    body = [line for line in (stdout or "").splitlines()
            if line.strip() and not line.startswith("-- No entries")]
    out["available"] = True
    out["entries"] = len(body)
    out["tail"] = body[-int(lines):]
    out["verdicts"] = [line for line in body if JOURNAL_VERDICT.search(line)][-8:]
    return out


# ── what is armed ────────────────────────────────────────────────────────────

def read_arm(repo, run=None):
    """Run the checkout's own `--check`, which is documented as changing nothing.

    Its verdict is read from its exit code and its lines are kept as they are: the
    checker knows more about a box than a summary written here would, and the words
    it uses are the ones the next command's error messages quote.
    """
    run = run or _default_run
    script = pathlib.Path(repo) / "deploy" / "arm-auto-deploy.sh"
    out = {"available": False, "script": str(script), "rc": None, "verdict": None,
           "lines": [], "detail": None}
    if not script.is_file():
        out["detail"] = "not in this checkout: %s" % script
        return out
    rc, stdout, stderr = run(["bash", str(script), "--check"],
                             env={"SG_REPO": str(repo)})
    if rc == 255:
        # The checker never ran (no bash, no permission). That is not an unarmed
        # box, and reporting it as one would send the operator to the wrong fix.
        out["detail"] = "the checker could not run: %s" % (stderr or "exit 255")
        return out
    out["available"] = True
    out["rc"] = rc
    out["verdict"] = ARM_VERDICTS.get(rc, "unexpected exit %s" % rc)
    body = [line for line in (stdout or "").splitlines()
            + (stderr or "").splitlines() if line.strip()]
    out["lines"] = body[-16:]
    return out


# ── the block ────────────────────────────────────────────────────────────────

def collect(repo=DEFAULT_REPO, base=DEFAULT_BASE, unit=DEFAULT_UNIT,
            since=DEFAULT_SINCE, samples=DEFAULT_SAMPLES,
            journal_lines=DEFAULT_JOURNAL_LINES, state_dir=DEFAULT_STATE_DIR,
            run=None, opener=None, git=None, now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    report = {
        "when": now,
        "host": socket.gethostname(),
        "kernel": platform.release(),
        "repo": str(repo),
        "health": read_health(base, samples=samples, opener=opener),
        "checkout": read_checkout(repo, run=run, git=git),
        "release": read_last_release(pathlib.Path(state_dir) / "last-deploy"),
        "journal": read_journal(unit, since=since, lines=journal_lines, run=run),
        "arm": read_arm(repo, run=run),
    }
    report["placement"] = place_served(
        majority_commit(report["health"]), str(repo), report["checkout"].get("head"),
        run=run, git=git)
    return report


def _tally(counter, empty="-"):
    """`key xN`, most frequent first. Takes a Counter or a plain mapping."""
    if not counter:
        return empty
    ordered = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return ", ".join("%s x%s" % (_ascii(key), value) for key, value in ordered)


def place_served(served, repo, head, run=None, git=None):
    """Where the served commit sits against the checkout -- asked of the graph.

    "Not equal" has two causes and only one of them is a deploy fault: a release
    landed and the process never loaded it (the served commit is an ancestor of
    HEAD), or the report is being run against a checkout that is not this box's (the
    commit is not in this history at all). Guessing the first would be a false alarm
    about a reload; asking `rev-list` tells them apart.
    """
    if not served or not head:
        return {"state": "unknown", "distance": None}
    if str(head).startswith(str(served)):
        return {"state": "same", "distance": 0}
    run = run or _default_run
    if git is None:
        git = git_binary()
    if not git:
        return {"state": "unknown", "distance": None}
    rc, count, _err = _git_probe(git, repo, ["rev-list", "--count", "%s..HEAD" % served],
                                 run, exempt=True)
    if rc != 0:
        return {"state": "elsewhere", "distance": None}
    return {"state": "behind", "distance": _count(count)}


def readers_line(readers):
    """`/health`'s own count of the box's workers, as one line.

    Written from the published block when it is there, because that is the count
    that covers workers this process never reached; the sampling above is the
    fallback for an app that predates the block, and the two are never blended.
    """
    if not readers:
        return None
    seen = readers.get("seen")
    if readers.get("key") == "unmeasured" or seen is None:
        return "NOT MEASURED (%s)" % (readers.get("detail") or "no reason given")
    named = readers.get("commits") or {}
    if readers.get("key") == "uniform":
        return "uniform -- all %d worker(s) named %s" % (
            seen, _tally(named) if named else "no commit")
    parts = []
    if readers.get("blind"):
        parts.append("%d of %d worker(s) cannot name their own commit (%s)"
                     % (readers["blind"], seen, _tally(readers.get("reasons"))))
    if len(named) > 1:
        parts.append("the workers named %d different commits (%s)"
                     % (len(named), _tally(named)))
    if readers.get("skipped"):
        parts.append("%d marker(s) could not be read as a worker" % readers["skipped"])
    if readers.get("unregistered"):
        parts.append("%d of %s worker(s) are missing from the ledger"
                     % (readers["unregistered"], readers.get("expected")))
    return "mixed -- " + "; ".join(parts or ["the workers do not agree"])


def majority_commit(health):
    """The commit most of the workers named, or None when none of them named one.

    A majority rather than the last answer: the sample is the *box*, and one blind
    worker answering last must not become the reading of a box whose other workers
    agree. The count is printed next to it, so the strength of the answer is visible.
    """
    served = health.get("served") or {}
    if not served:
        return None
    return max(served.items(), key=lambda item: item[1])[0]


def _age(iso):
    try:
        moment = _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    seconds = int((_dt.datetime.now(_dt.timezone.utc) - moment).total_seconds())
    if seconds < 0:
        return "in the future"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


#: Why the checkout could not be read, in words rather than in a state name. The
#: three are different fixes: point --repo at the checkout, install git, or look at
#: the tree.
CHECKOUT_WORDS = {
    "not_a_checkout": "there is no .git at that path",
    "no_git": "git is not installed, or not on this PATH",
    "unreadable": "git ran and could not read the tree",
}


def gaps_of(report):
    """The inputs that were asked for and could not be read."""
    gaps = []
    health = report["health"]
    if not health["available"]:
        gaps.append("served commit: %s" % health["detail"])
    checkout = report["checkout"]
    if checkout["git_state"] != "readable":
        words = CHECKOUT_WORDS.get(checkout["git_state"], checkout["git_state"])
        detail = checkout["detail"]
        gaps.append("checkout: %s%s" % (words, " -- " + str(detail) if detail else ""))
    if report["release"]["state"] == "unreadable":
        gaps.append("last release record: %s" % report["release"]["detail"])
    if not report["journal"]["available"]:
        gaps.append("deploy journal: %s" % report["journal"]["detail"])
    if not report["arm"]["available"]:
        gaps.append("arm check: %s" % report["arm"]["detail"])
    # A box whose workers do not agree is the finding this half of the report
    # exists for, so it is a gap and not only a line: an exit code of 0 would say
    # the box was read and nothing was wrong, while one worker is serving something
    # the others are not — or cannot say what it is serving.
    if (health.get("readers") or {}).get("key") == "mixed":
        gaps.append("served commit: %s" % readers_line(health["readers"]))
    return gaps


def exit_code_of(report):
    measured = report["health"]["available"] or report["checkout"]["git_state"] == "readable"
    if not measured:
        return 2
    return 1 if gaps_of(report) else 0


def _json_ready(value):
    """Counters to sorted maps, datetimes to UTC ISO, the rest left as it is.

    A `Counter` is a dict to `json`, but its order is insertion order -- the order
the samples happened to arrive. Two runs of an unchanged box would then differ in
key order alone, and a diff would report every tally as changed. Sorting here is
what makes the object a reading of the box rather than of the sampling loop.
    """
    if isinstance(value, collections.Counter):
        return {str(key): count
                for key, count in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, _dt.datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
        return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def as_json(report):
    """The same reading as one JSON object, so two runs can be compared.

    Every section the block prints is here under the key it is built from, plus the
two the block only implies: `exit` (the code this run returns) and `gaps` (every
input that could not be read). A comparison that had to parse the prose for those
would break the moment a sentence was reworded -- and the sentence is the part of
this tool that changes most.
    """
    return _json_ready({
        "schema": JSON_SCHEMA,
        "when": report["when"],
        "host": report["host"],
        "kernel": report["kernel"],
        "repo": report["repo"],
        "exit": exit_code_of(report),
        "gaps": gaps_of(report),
        "health": report["health"],
        "checkout": report["checkout"],
        "placement": report.get("placement"),
        "release": report["release"],
        "journal": report["journal"],
        "arm": report["arm"],
    })


def render(report):
    """The paste-able block. Every line is clipped, and everything is ASCII."""
    lines = []
    add = lines.append
    gaps = gaps_of(report)
    when = report["when"].astimezone()
    add("=== ScanGrade box report ===")
    add("when   : %s (%s)" % (when.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              when.strftime("%Y-%m-%d %H:%M %z")))
    add("host   : %s   kernel: %s" % (report["host"], report["kernel"]))
    add("repo   : %s" % report["repo"])
    add("exit   : %s  (0 complete / 1 gaps named / 2 could not measure)"
        % exit_code_of(report))

    health = report["health"]
    add("")
    add("-- served commit (/health, %d samples) --" % health["samples"])
    add("url    : %s" % health["url"])
    if not health["available"]:
        add("status : NOT MEASURED -- %s" % health["detail"])
    else:
        add("workers: %s   answered %d/%d   blind %d"
            % (health["workers"] or "?", health["answered"], health["samples"],
               health["blind"]))
        add("served : %s" % _tally(health["served"], "none named"))
        if health["blind"]:
            add("blind  : %s" % _tally(health["reasons"]))
        if health["loaded_at"]:
            stamps = health["loaded_at"]
            for stamp in list(stamps)[:3]:
                age = _age(stamp)
                add("loaded : %s%s%s" % (stamp, " (" + age + " old)" if age else "",
                                        "" if len(stamps) == 1 else
                                        " x%s" % stamps[stamp]))
        line = readers_line(health.get("readers"))
        if line:
            add("readers: %s" % line)
        if len(health["served"]) > 1:
            add("WARNING: the workers report different commits -- the reload landed"
                " in some of them")
        if health["blind"] and health["answered"] - health["blind"] > 0:
            add("WARNING: some workers cannot name their own commit; a served-commit"
                " gate landing on them reads 'could not measure'")
        if health["uptime_ms"] is not None:
            total = health["uptime_ms"] // 1000
            add("uptime : %dd %dh %dm" % (total // 86400, (total % 86400) // 3600,
                                          (total % 3600) // 60))
    if health["errors"]:
        add("errors : %s" % _tally(health["errors"]))

    checkout = report["checkout"]
    # Read before the checkout lines: the placement sentence below says how much a
    # "behind HEAD" reading is allowed to claim, and that depends on whether the
    # runner left a record on this box.
    release = report["release"]
    add("")
    add("-- checkout --")
    add("path   : %s" % checkout["path"])
    if checkout["git_state"] != "readable":
        add("status : NOT READ (%s) -- %s"
            % (checkout["git_state"], checkout["detail"] or "no reason given"))
    else:
        add("HEAD   : %s %s" % (checkout["head"][:9],
                                "(%s)" % checkout["subject"] if checkout["subject"] else ""))
        add("branch : %s   origin/main: %s   behind: %s   ahead: %s"
            % (checkout["branch"] or "?",
               checkout["origin"] or "absent",
               "-" if checkout["behind"] is None else checkout["behind"],
               "-" if checkout["ahead"] is None else checkout["ahead"]))
        served = majority_commit(report["health"])
        placement = report.get("placement") or {"state": "unknown", "distance": None}
        if served and placement["state"] == "same":
            add("served : %s matches HEAD -- the process holds the box's code" % served)
        elif served and placement["state"] == "behind":
            add("served : %s is %s commit(s) behind HEAD %s -- the process holds older"
                " code than this checkout"
                % (served, placement["distance"], checkout["head"][:9]))
            if release["state"] == "recorded":
                add("         (on this box that is a release that landed and was never"
                    " loaded; the record below is what the runner wrote)")
            else:
                add("         (no release record here, so this may be another box's"
                    " checkout rather than a reload that did not take)")
        elif served and placement["state"] == "elsewhere":
            add("served : %s is not in this checkout's history -- a different box, or"
                " the tree was reset under the running process" % served)
        if checkout["exemption"] == "required":
            add("git    : readable only with the safe.directory exemption this process"
                " passes itself (the checkout's owner is not the reading user)")
        elif checkout["exemption"] == "insufficient":
            add("git    : WRITABLE BUT UNREADABLE EVEN WITH THE EXEMPTION -- the"
                " checkout is broken, not just owned by someone else")
        else:
            add("git    : readable with the box's own config (no exemption needed)")

    add("")
    add("-- last recorded release --")
    if release["state"] == "recorded":
        add("commit : %s at %s" % (release["commit"], release["at"] or "?"))
        add("restore: %s" % (release["restore"] or "none recorded"))
    else:
        add("state  : %s%s" % (release["state"],
                               " -- " + str(release["detail"]) if release["detail"] else ""))

    arm = report["arm"]
    add("")
    add("-- arm (deploy/arm-auto-deploy.sh --check) --")
    if not arm["available"]:
        add("status : NOT MEASURED -- %s" % arm["detail"])
    else:
        add("verdict: %s   exit: %s" % (arm["verdict"], arm["rc"]))
        for line in arm["lines"]:
            add("  " + line)

    journal = report["journal"]
    add("")
    add("-- deploy journal (%s, since %s) --"
        % (journal["unit"], journal["since"]))
    if not journal["available"]:
        add("status : NOT MEASURED -- %s" % journal["detail"])
    elif not journal["entries"]:
        add("entries: 0 -- the runner has not run in this window")
    else:
        add("entries: %d read (verdict digest, then the last %d)"
            % (journal["entries"], len(journal["tail"])))
        for line in journal["verdicts"]:
            add("  ! " + line)
        for line in journal["tail"]:
            add("  | " + line)

    add("")
    add("-- not measured --")
    if gaps:
        for gap in gaps:
            add("  * " + str(gap))
    else:
        add("  (none -- every input above was read)")

    if release["state"] == "recorded" and checkout["head"]:
        recorded = str(release["commit"])
        head = str(checkout["head"])
        if not (head.startswith(recorded) or recorded.startswith(head)):
            add("")
            add("NOTE: the checkout's HEAD (%s) is not the commit on record (%s) --"
                " the tree moved outside the runner" % (head[:9], recorded[:9]))

    add("=== end report ===")
    return "\n".join(_clip(line, MAX_LINE) for line in lines)


def main(argv=None, run=None, opener=None):
    parser = argparse.ArgumentParser(
        description="Print a paste-able summary of this box's deploy state.")
    parser.add_argument("--repo", default=os.environ.get("SG_REPO", DEFAULT_REPO))
    parser.add_argument("--base", default=os.environ.get("SG_BASE", DEFAULT_BASE),
                        help="where /health answers (default %s)" % DEFAULT_BASE)
    parser.add_argument("--unit", default=DEFAULT_UNIT,
                        help="the deploy unit's journal (default %s)" % DEFAULT_UNIT)
    parser.add_argument("--since", default=DEFAULT_SINCE,
                        help="journal window (default '%s')" % DEFAULT_SINCE)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help="how many times to ask /health (default %d)"
                        % DEFAULT_SAMPLES)
    parser.add_argument("--journal-lines", type=int, default=DEFAULT_JOURNAL_LINES,
                        help="journal tail length (default %d)"
                        % DEFAULT_JOURNAL_LINES)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR,
                        help="where the runner keeps last-deploy (default %s)"
                        % DEFAULT_STATE_DIR)
    parser.add_argument("--json", action="store_true",
                        help="print the same reading as one JSON object, for diffing"
                             " two runs against each other")
    args = parser.parse_args(argv)

    report = collect(repo=args.repo, base=args.base, unit=args.unit, since=args.since,
                     samples=args.samples, journal_lines=args.journal_lines,
                     state_dir=args.state_dir, run=run, opener=opener)
    if args.json:
        # sort_keys is the point rather than a preference: a diff of two runs must
        # show what the box changed, and an object whose key order followed a dict's
        # insertion order would make every section look rewritten.
        print(json.dumps(as_json(report), indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(render(report))
    return exit_code_of(report)


if __name__ == "__main__":
    sys.exit(main())
