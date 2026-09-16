"""Read a real photograph of a filled LJK and say exactly what happened.

The scanner's evidence so far is a *simulator*: the app prints a sheet, rasterises
it, applies the distortions a handset applies and reads it back. That is a strong
test of the geometry and a weak test of paper. This script is what closes the gap,
because it takes a photograph someone actually took — the printed sheet, a real
pencil, the lighting in the room — and reports what the reader made of it.

    python test_scripts/check_omr_photo.py photo.jpg
    python test_scripts/check_omr_photo.py photo.jpg --questions 50 --page 0
    python test_scripts/check_omr_photo.py photo.jpg --expect A,B,C,D,A
    python test_scripts/check_omr_photo.py *.jpg --out-dir /tmp/omr

Exit codes: 0 read, 1 refused (with the reason), 2 the file could not be opened.
The annotated image shows where the reader thinks the four markers are and which
bubbles it read, which is the fastest way to see *why* a photo failed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import omr_service as O  # noqa: E402


def _load(path: str):
    with open(path, "rb") as fh:
        return fh.read()


def _fmt_answers(answers: dict, confidence: dict, needs_review) -> str:
    flagged = {str(x) for x in (needs_review or [])}
    items = sorted(answers.items(), key=lambda kv: int(kv[0]))
    lines = []
    for i in range(0, len(items), 10):
        row = items[i:i + 10]
        cells = []
        for k, v in row:
            mark = "!" if k in flagged else (" " if confidence.get(k, 1) >= 0.7 else "?")
            cells.append(f"{int(k) + 1:>3}:{v}{mark}")
        lines.append("   " + "  ".join(cells))
    return "\n".join(lines) if lines else "   (none)"


def check(path: str, questions: int, page: int, expect: list[str] | None,
          out_dir: str | None) -> int:
    print("=" * 78)
    print(f"{os.path.basename(path)}")
    print("=" * 78)

    try:
        raw = _load(path)
        img = O.load_image(raw)
    except OSError as e:
        print(f"  could not open: {e}")
        return 2
    if img is None:
        print("  could not decode as an image")
        return 2

    h, w = img.shape[:2]
    print(f"  photograph: {w}×{h}  "
          f"({os.path.getsize(path) / 1024:.0f} KB)")

    result = O.process_scan(raw, total_questions=questions, preprocess=True,
                            page_index=page)

    if "error" in result:
        print(f"  REFUSED: {result['error']}")
        print("  What to try: get the whole sheet in frame with all four corner "
              "squares\n     visible, shoot from above on a contrasting surface, "
              "and avoid a hard\n     shadow or glare.")
        if out_dir:
            _annotate(raw, None, None, out_dir, path)
        return 1

    code = result.get("layout_code")
    from_qr = result.get("layout_from_qr")
    print(f"  sheet code: {code!r} "
          f"({'read from the printed QR' if from_qr else 'NOT read — assumed from --questions'})")
    print(f"  markers found: {result.get('markers_found')}   "
          f"orientation settled by: {result.get('orientation')}")
    print(f"  grid match: {result.get('grid_match')} "
          f"(the fraction of sampled positions that landed on the sheet's own "
          f"printed grid)")
    print(f"  page: {result.get('page_index', 0) + 1} of {result.get('page_count', 1)}  "
          f"questions {result.get('first_question')}–{result.get('last_question')}")

    answers = {int(k): v for k, v in (result.get("answers") or {}).items()}
    confidence = result.get("confidence") or {}
    needs = result.get("needs_review") or []
    print(f"  read: {len(answers)} of {result.get('page_questions')} questions on this "
          f"page answered   avg confidence {result.get('avg_confidence')}")
    print(f"  NISN: {result.get('nisn')!r} "
          f"({result.get('nisn_read')} digits filled, "
          f"confidence {result.get('nisn_confidence')})")
    if needs:
        print(f"  needs review ({len(needs)}): {needs}")
    print("  answers  ( ! = needs review, ? = low confidence )")
    print(_fmt_answers({str(k): v for k, v in answers.items()}, confidence, needs))

    if expect:
        got = [answers.get(i) for i in range(len(expect))]
        wrong = [(i + 1, want, got[i]) for i, want in enumerate(expect)
                 if got[i] is not None and got[i] != want]
        blank = [i + 1 for i in range(len(expect)) if got[i] is None]
        print(f"  against --expect: {len(expect) - len(wrong) - len(blank)}/"
              f"{len(expect)} match, {len(wrong)} wrong, {len(blank)} unread")
        if wrong:
            print(f"    WRONG: {wrong}")
        if blank:
            print(f"    UNREAD (blank on the sheet, or not marked): {blank}")

    if out_dir:
        _annotate(raw, img, result, out_dir, path)

    print("  -> READ OK")
    return 0


def _annotate(raw: bytes, img, result, out_dir: str, path: str) -> None:
    """Write the reader's own view, so a failure can be looked at, not guessed."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        if img is None:
            img = O.load_image(raw)
        if img is None:
            return
        corners = O.find_registration_marks(img)
        answers = (result or {}).get("answers")
        page = int((result or {}).get("page_index", 0))
        jpg = O.draw_debug_image(img, corners, answers, page_index=page)
        dst = os.path.join(out_dir,
                           os.path.splitext(os.path.basename(path))[0] + "-annotated.jpg")
        with open(dst, "wb") as fh:
            fh.write(jpg)
        print(f"  annotated: {dst}")
    except Exception as e:  # the picture is a courtesy, never the result
        print(f"  (could not write the annotated image: {e})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="photograph(s) of a filled LJK")
    ap.add_argument("--questions", type=int, default=50,
                    help="question count, used only if the sheet's QR cannot be read")
    ap.add_argument("--page", type=int, default=0,
                    help="which page of a multi-page sheet this photograph is (0-based)")
    ap.add_argument("--expect", default="",
                    help="comma-separated answers you filled in, e.g. A,B,C,D — "
                         "compared against what was read")
    ap.add_argument("--out-dir", default="",
                    help="write an annotated copy of each photograph here")
    ap.add_argument("--json", action="store_true", help="also print the raw result")
    args = ap.parse_args(argv)

    files = []
    for p in args.paths:
        files.extend(sorted(glob.glob(p)) or [p])
    expect = [x.strip().upper() for x in args.expect.split(",") if x.strip()] or None

    worst = 0
    for path in files:
        code = check(path, args.questions, args.page, expect, args.out_dir or None)
        worst = max(worst, code)
        if args.json:
            raw = _load(path)
            print(json.dumps(O.process_scan(raw, total_questions=args.questions,
                                            preprocess=True, page_index=args.page),
                             indent=2, default=str))
    return worst


if __name__ == "__main__":
    sys.exit(main())
