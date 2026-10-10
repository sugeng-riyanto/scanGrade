#!/usr/bin/env python3
"""Phase 11: does a *real* SEB client agree with the key this server computes?

Why this is a tool and not a paragraph
--------------------------------------
Every other check in this feature is a reading or a self-consistency test. This
module's serialiser can agree with itself perfectly and still disagree with every
SEB client in the world, and the failure mode is the worst kind: `require_seb`
reads ON, the `.seb` file downloads, the panel looks right, and **no pupil can open
the paper** — with the refusal message blaming their browser. The only thing that
settles it is a real client, on a real machine, and the only thing that makes that
exercise worth doing is writing down what came out. So the exercise has a
machine-readable half, and this is it.

What it compares, and what it deliberately does not
---------------------------------------------------
The Config Key of the file **is** this server's own output: it was produced by
`seb_config_key.config_key` when the file was written, so comparing it against
itself proves nothing and is not offered as evidence. The four comparisons that do
carry evidence are:

1. `--seb-key` — the Config Key the *SEB client itself* reports for this config
   (SEB shows it in its own settings/About window and writes it to its log). This
   is the one that decides whether our serialiser agrees with the client.
2. `--seb-json` — SEB's own SEB-JSON string, which SEB writes to its log at
   *Verbose* log level under the heading `JSON for Config Key:`. When the keys
   disagree, this is what says *where*: the first differing byte is reported with
   its offset and context, so the answer is "our rule 4 is wrong" rather than
   "something is wrong".
3. `--exam` — the key stored on the exam row, which is the value the door actually
   compares against. A file re-downloaded before a re-issue carries the *old* key,
   and that is a real and reproducible cause of "SEB is installed and it still
   refuses".
4. `--seb-header` — the `X-SafeExamBrowser-ConfigKeyHash` the client actually sent,
   compared against `SHA256(startURL + Config Key)` computed here. This is the
   end-to-end answer, and the only one that covers the URL half as well.

Usage
-----
    python deploy/seb_phase11.py exam.seb --exam <exam_id> \\
        --seb-key <key SEB reports> --seb-json @seb-log-line.txt \\
        --seb-header <value from the request>

Exit codes
----------
  0  every comparison that was asked for matched
  1  at least one comparison did not match
  2  could not measure — unreadable file, no `startURL`, no such exam row, or
     nothing to compare against at all

`2` is separate from `1` on purpose: "we did not measure" and "we measured and it
failed" are different findings, and collapsing them is how a Phase 11 report ends
up claiming more than it saw.

What this tool must never do
----------------------------
Read `exam_seb_credential`. That table holds the *reversible* copy of the quit and
admin passwords, and a diagnostic has no business touching it — the plaintext
exists so a teacher can read one password aloud, not so a debugging script can
dump it. The exam row read here names its columns, and `tests/unit/test_seb_phase11.py`
fails if that table is mentioned anywhere in this file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))          # the checkout
sys.path.insert(0, str(Path(__file__).resolve().parent))               # db_snapshot

from app.services import seb_config_key as ck                          # noqa: E402
from app.services import seb_service                                   # noqa: E402

#: The columns the exam read names. Only what a verdict needs — and deliberately
#: not `*`, which would one day grow a column this tool should not print.
EXAM_COLUMNS = "id,title,require_seb,seb_config_key"


def _first_difference(ours: str, theirs: str) -> str:
    """Where two strings part, in the words an operator can act on.

    A bare "they differ" sends the reader back to the specification with no
    starting point. The offset plus a window on each side is usually enough to name
    the rule: a doubled backslash is rule 4, a sorted-away key is rule 2, an empty
    dictionary is rule 5, a stray space is rule 3.
    """
    limit = min(len(ours), len(theirs))
    for index in range(limit):
        if ours[index] != theirs[index]:
            lo, hi = max(0, index - 40), index + 40
            return (
                f"first difference at offset {index}\n"
                f"      ours  : ...{ours[lo:hi]}...\n"
                f"      theirs: ...{theirs[lo:hi]}..."
            )
    if len(ours) != len(theirs):
        longer, kind = (ours, "ours") if len(ours) > len(theirs) else (theirs, "theirs")
        return (f"identical for {limit} characters, then {kind} continues for "
                f"{len(longer) - limit} more: ...{longer[limit:limit + 80]}...")
    return ""


def _clean(value: str) -> str:
    """Trailing whitespace and a trailing `;` are what clients actually send."""
    return (value or "").strip().rstrip(";").strip()


def _load_remote_exam(repo: Path, exam_id: str) -> dict:
    """The exam row, over the REST API. No SQL, so no credential beyond the key."""
    import requests
    import db_snapshot

    api_url, api_key = db_snapshot.load_credentials(repo)
    if not api_url or not api_key:
        raise SystemExit(2)
    resp = requests.get(
        f"{api_url}/rest/v1/exams", params={"select": EXAM_COLUMNS, "id": f"eq.{exam_id}"},
        headers={"apikey": api_key, "Authorization": f"Bearer {api_key}"}, timeout=30)
    resp.raise_for_status()
    rows = resp.json() or []
    if not rows:
        return {}
    return rows[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a real SEB client's Config Key with this server's.")
    parser.add_argument("file", help="the .seb file that was handed to the client")
    parser.add_argument("--exam", default="",
                        help="exam id: also compare against the stored key the door uses")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]),
                        help="checkout holding .env (default: the one this script lives in)")
    parser.add_argument("--seb-key", default="",
                        help="the Config Key the SEB client reports for this config")
    parser.add_argument("--seb-json", default="",
                        help="SEB's own SEB-JSON (Verbose log, 'JSON for Config Key:'), "
                             "or @path to a file holding it")
    parser.add_argument("--seb-header", default="",
                        help="the X-SafeExamBrowser-ConfigKeyHash value the client sent")
    parser.add_argument("--show-json", action="store_true",
                        help="print our SEB-JSON as well (one long line)")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"[CANNOT MEASURE] no such file: {path}")
        return 2
    try:
        settings = seb_service.decode_seb(path.read_bytes())
    except Exception as exc:                                           # noqa: BLE001
        print(f"[CANNOT MEASURE] {path.name} is not a readable .seb file "
              f"({type(exc).__name__}: {exc})")
        return 2

    key = ck.config_key(settings)
    start_url = str(settings.get("startURL") or "")
    print("=" * 74)
    print("PHASE 11 - Config Key, against a real client")
    print("=" * 74)
    print(f"   file:      {path}")
    print(f"   startURL:  {start_url or '(none - the file gates nothing)'}")
    print(f"   our key:   {key}")
    if args.show_json:
        print(f"   our JSON:  {ck.seb_json(settings)}")
    print()

    verdicts: list[bool] = []          # False at the end means a mismatch was seen

    # ── 1. the key the client reports ────────────────────────────────────────
    if args.seb_key:
        theirs = _clean(args.seb_key).lower()
        ok = theirs == key
        verdicts.append(ok)
        print(f"[{'MATCH' if ok else 'MISMATCH'}] Config Key reported by SEB")
        print(f"      SEB:  {theirs}")
        print(f"      ours: {key}")
        if not ok:
            print("      -> run again with --seb-json and the client's")
            print("         'JSON for Config Key:' line: the key differing means the")
            print("         serialiser differs, and that line says where.")
    else:
        print("[skipped] SEB's own Config Key (--seb-key) - no value given")

    # ── 2. the client's own SEB-JSON, byte for byte ──────────────────────────
    if args.seb_json:
        raw = args.seb_json
        if raw.startswith("@"):
            try:
                raw = Path(raw[1:]).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"[CANNOT MEASURE] --seb-json file unreadable: {exc}")
                return 2
        theirs = raw.strip()
        # SEB logs the line with its own heading; accept it either way.
        for marker in ("JSON for Config Key:", "JSON for Config Key"):
            if theirs.startswith(marker):
                theirs = theirs[len(marker):].strip()
        ours = ck.seb_json(settings)
        ok = theirs == ours
        verdicts.append(ok)
        print(f"[{'MATCH' if ok else 'MISMATCH'}] SEB-JSON, byte for byte")
        if not ok:
            print("      " + (_first_difference(ours, theirs) or "they differ"))
    else:
        print("[skipped] SEB's own SEB-JSON (--seb-json) - no value given")

    # ── 3. the key the door actually compares against ────────────────────────
    if args.exam:
        repo = Path(args.repo)
        try:
            exam = _load_remote_exam(repo, args.exam)
        except Exception as exc:                                       # noqa: BLE001
            print(f"[CANNOT MEASURE] could not read exam {args.exam} "
                  f"({type(exc).__name__}: {exc})")
            return 2
        if not exam:
            print(f"[CANNOT MEASURE] no exam row with id {args.exam}")
            return 2
        stored = _clean(exam.get("seb_config_key") or "").lower()
        require = bool(exam.get("require_seb"))
        print(f"      exam: {exam.get('title') or args.exam}  (require_seb={require})")
        if not require:
            print("[MISMATCH] this exam does not require SEB at all - the door would")
            print("      admit a plain browser, so a file proves nothing here")
            verdicts.append(False)
        elif not stored:
            print("[MISMATCH] the exam requires SEB but no key is stored - the door")
            print("      fails closed, so nobody can open it (re-issue from the panel)")
            verdicts.append(False)
        else:
            ok = stored == key
            verdicts.append(ok)
            print(f"[{'MATCH' if ok else 'MISMATCH'}] stored key vs the file's key")
            print(f"      stored: {stored}")
            if not ok:
                print("      -> the file predates a re-issue (or the row was edited")
                print("         by hand). Download the file again from the panel.")
    else:
        print("[skipped] stored key (--exam) - no exam id given")

    # ── 4. the header, which covers the URL half too ─────────────────────────
    if args.seb_header:
        if not start_url:
            print("[CANNOT MEASURE] the file carries no startURL, so the header "
                  "cannot be recomputed")
            return 2
        theirs = _clean(args.seb_header).lower()
        expected = ck.request_hash(start_url, key)
        ok = theirs == expected
        verdicts.append(ok)
        print(f"[{'MATCH' if ok else 'MISMATCH'}] header the client sent")
        print(f"      sent:     {theirs}")
        print(f"      expected: {expected}")
        if not ok:
            print(f"      over URL: {ck.absolute_url_without_fragment(start_url)}")
            print("      -> if the Config Key matched but this did not, the URL half")
            print("         differs: a different host, `www.`, a port, or a link that")
            print("         carried a query string when the file was written.")
    else:
        print("[skipped] the client's header (--seb-header) - no value given")

    print()
    if not verdicts:
        print("COULD NOT MEASURE: nothing was compared. Give at least one of")
        print("--seb-key, --seb-json, --seb-header, or --exam.")
        return 2
    if all(verdicts):
        print("MATCH: every comparison asked for agreed with the client.")
        return 0
    print("MISMATCH: at least one comparison disagreed - this is the finding to")
    print("report, with the values above, rather than a paper declared secure.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
