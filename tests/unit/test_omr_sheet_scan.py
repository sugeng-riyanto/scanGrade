"""The printed sheet, read back from a photograph of itself.

This is the one test in the suite that exercises the *whole* chain, and it does it
without a fixture of pre-made images: the app's own generator makes the PDF, the
app's own rasteriser turns it into paper, a known answer set is filled in as
pencil marks, and then the paper is *photographed* — perspective, rotation, blur,
uneven light, noise and JPEG, the way a phone delivers it. What
`process_scan` returns is compared against what was filled in.

Two things make that worth doing rather than testing the pieces.

**The printer and the scanner share one model.** Every coordinate here comes from
`omr_layout`, so a sheet that prints correctly and a scanner that reads correctly
cannot disagree about where a bubble is — and if someone edits one side only,
this file fails rather than the classroom.

**Refusing is a result too.** A scanner that guesses is worse than one that says
it could not read the sheet, because a wrong mark is invisible in a score. So the
tests below assert *zero* wrong answers and *zero* invented ones everywhere —
including on photographs the pipeline is allowed to refuse — and then separately
assert that a warp which is not on the page is refused, that a genuinely blank
sheet is not, and that the four corner markers land within a few pixels of where
they really are.

The photo simulation is deliberately physical rather than adversarial: it applies
the distortions a hand-held phone applies and nothing that a sheet could not
survive. Its conditions are named for what they are, and the harshest of them is
past what a teacher should have to accept — it is here to show where the envelope
is, not to be the standard.
"""
import functools
import math
import pathlib
import random

import cv2
import numpy as np
import pytest

from app.services import omr_layout as L
from app.services import omr_service as O
from app.services.answer_sheet_generator import generate_answer_sheet

SHEET_DPI = 200
PAPER_MM = 25.4 / SHEET_DPI
MARK_VALUE = 40          # a pencil mark on white paper, of 255
DESK = 150               # the surface the sheet is lying on

# Named photo conditions. Each is a set of physical parameters, not a difficulty
# label: `tilt` and `angle` are degrees of rotation and fraction-of-frame
# perspective, `light` the strength of a lighting gradient plus a dark corner,
# `jpeg` the quality a phone would save at.
CONDITIONS = {
    "flat-scan": dict(margin=0.00, angle=0.00, tilt=0.0, blur=0.0, light=0.00,
                      noise=0.0, jpeg=100),
    "mild":      dict(margin=0.12, angle=0.06, tilt=3.0, blur=0.8, light=0.18,
                      noise=4.0, jpeg=90),
    "normal":    dict(margin=0.12, angle=0.10, tilt=6.0, blur=1.1, light=0.32,
                      noise=5.0, jpeg=72),
    "hard":      dict(margin=0.12, angle=0.16, tilt=6.0, blur=1.8, light=0.55,
                      noise=6.0, jpeg=55),
    "close-up":  dict(margin=0.03, angle=0.12, tilt=8.0, blur=1.4, light=0.40,
                      noise=5.0, jpeg=60),
    "far-away":  dict(margin=0.45, angle=0.12, tilt=8.0, blur=1.4, light=0.40,
                      noise=5.0, jpeg=60),
    "rotated":   dict(margin=0.12, angle=0.10, tilt=16.0, blur=1.2, light=0.35,
                      noise=5.0, jpeg=65),
}
# Conditions a read must survive. The others are still held to "never wrong".
READABLE = ("flat-scan", "mild", "normal", "hard", "close-up", "far-away", "rotated")


# ── the paper ────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _paper_png(total: int = 20, options: int = 5) -> bytes:
    """The printed sheet, rendered once per shape — the print is not the subject."""
    pdf = generate_answer_sheet(total_questions=total, options=options,
                               mark_type="circle", exam_version="A")
    import fitz
    doc = fitz.open(stream=pdf.read(), filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=SHEET_DPI, colorspace=fitz.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3)
    return cv2.imencode(".png", img)[1].tobytes()


def render_paper(total=20, options=5):
    """The app's generator → a PDF → the pixels a printer would put on paper."""
    return cv2.imdecode(np.frombuffer(_paper_png(total, options), np.uint8),
                        cv2.IMREAD_COLOR)


def fill(paper, layout, marks):
    """Pencil marks: a filled disc at each chosen option's centre."""
    out = paper.copy()
    for q, option in marks:
        x_mm, y_mm = layout.option_centre_mm(q, option, 0)
        cv2.circle(out, (int(round(x_mm / PAPER_MM)), int(round(y_mm / PAPER_MM))),
                   max(2, int(L.OPTION_RADIUS_MM / PAPER_MM * 0.85)),
                   (MARK_VALUE, MARK_VALUE, MARK_VALUE), -1)
    return out


def answer_set(total, options, seed, blank_rate=0.12):
    """Which bubbles were filled, and the letters a correct read must return."""
    rng = random.Random(seed)
    marks, expected = [], {}
    for q in range(total):
        if rng.random() < blank_rate:
            continue
        option = rng.randrange(options)
        marks.append((q, option))
        expected[str(q)] = "ABCDEFGH"[option]
    return marks, expected


def photograph(paper, seed, **kw):
    """Turn flat paper into what a phone returns, and say where the corners went.

    Returns `(image, truth)`; `truth` is the four marker centres in the returned
    image's pixels, recovered by replaying the same matrices this applies. Without
    it the only thing a test could check is "it found something", which is exactly
    the assertion that let a wrong foursome ship.
    """
    rng = random.Random(seed)
    noise = np.random.default_rng(abs(hash(seed)) % (2 ** 31))
    h, w = paper.shape[:2]
    m = int(max(h, w) * kw["margin"])
    canvas = np.full((h + 2 * m, w + 2 * m, 3), DESK, np.uint8)
    canvas[m:m + h, m:m + w] = paper
    H, W = canvas.shape[:2]

    # Sized to hold whatever the tilt produced, in both stages below. That is not
    # tidiness: a corner pushed outside the picture is a photograph of a sheet
    # with a corner missing, which is a genuinely unreadable case — and one worth
    # refusing — so it must not be what a "close-up" test accidentally measures.
    # The first version of this fixture cropped a corner in three of four seeds
    # and the scanner was blamed for refusing them.
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    d = max(H, W) * kw["angle"]
    jitter = np.float32([[rng.uniform(-d, d), rng.uniform(-d, d)] for _ in range(4)])
    moved = src + jitter
    perspective = np.array(cv2.getPerspectiveTransform(src, moved), dtype=np.float64)
    lo, hi = moved.min(axis=0), moved.max(axis=0)
    pad = 24
    out_w, out_h = int(hi[0] - lo[0]) + 2 * pad, int(hi[1] - lo[1]) + 2 * pad
    shift = np.array([[1.0, 0.0, pad - lo[0]], [0.0, 1.0, pad - lo[1]], [0.0, 0.0, 1.0]])
    perspective = shift @ perspective
    frame = cv2.warpPerspective(canvas, perspective, (out_w, out_h),
                                borderValue=(DESK, DESK, DESK))

    # Rotated into a frame grown to hold the result. A camera pointed at a tilted
    # sheet does not lose the sheet's corners, and a simulator that cropped them
    # would be asking the scanner to find a marker that the photograph does not
    # contain — which is a different failure, and one it *should* refuse.
    fh, fw = frame.shape[:2]
    angle = rng.uniform(-kw["tilt"], kw["tilt"])
    cos, sin = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    rw, rh = int(fw * cos + fh * sin) + 2, int(fw * sin + fh * cos) + 2
    rotate = cv2.getRotationMatrix2D((fw / 2, fh / 2), angle, 1.0)
    rotate[0, 2] += (rw - fw) / 2
    rotate[1, 2] += (rh - fh) / 2
    frame = cv2.warpAffine(frame, rotate, (rw, rh), borderValue=(DESK, DESK, DESK))

    if kw["blur"]:
        k = int(kw["blur"] * 4) | 1
        frame = cv2.GaussianBlur(frame, (k, k), kw["blur"])
    if kw["light"]:
        yy, xx = np.mgrid[0:frame.shape[0], 0:frame.shape[1]].astype(np.float32)
        gx = xx / max(frame.shape[1] - 1, 1)
        gy = yy / max(frame.shape[0] - 1, 1)
        grad = (1.0 + kw["light"] * (0.6 * gx - 0.4 * gy)
                - kw["light"] * 0.3 * np.exp(
                    -(((gx - 0.85) ** 2 + (gy - 0.9) ** 2) / 0.06)))
        frame = np.clip(frame.astype(np.float32) * grad[:, :, None], 0, 255).astype(np.uint8)
    if kw["noise"]:
        frame = np.clip(frame.astype(np.float32)
                        + noise.normal(0, kw["noise"], frame.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(kw["jpeg"])])
    shot = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else frame

    corners = np.float32([[[x / PAPER_MM, y / PAPER_MM]]
                          for (x, y) in L.MARKER_CORNERS_MM]) + np.float32([m, m])
    transform = np.vstack([rotate, [0, 0, 1]]) @ perspective
    truth = cv2.perspectiveTransform(
        corners.reshape(1, -1, 2), transform).reshape(-1, 2)
    return shot, truth


def as_upload(image):
    """The bytes a browser would actually post."""
    return cv2.imencode(".jpg", image)[1].tobytes()


def corner_error(found, truth):
    """Largest distance from a found corner to its true counterpart, any rotation."""
    if not found:
        return None
    found_pts = np.array([[m.cx, m.cy] for m in found], dtype=np.float64)
    return min(float(np.max(np.linalg.norm(np.roll(found_pts, shift, axis=0) - truth,
                                           axis=1)))
               for shift in range(4))


# ── the sheet reads back ─────────────────────────────────────────────────────

class TestEveryFilledBubbleComesBack:
    """Zero wrong, zero invented, zero missed — on every condition that must work."""

    @pytest.mark.parametrize("condition", READABLE)
    @pytest.mark.parametrize("seed", [0, 1, 2])
    def test_the_read_is_exact(self, condition, seed):
        layout = L.default_layout(20)
        marks, expected = answer_set(20, 5, seed)
        shot, _truth = photograph(fill(render_paper(20), layout, marks), seed,
                                  **CONDITIONS[condition])
        result = O.process_scan(as_upload(shot), total_questions=20)

        assert "error" not in result, (condition, result.get("error"))
        got = result["answers"]
        wrong = {k: (v, got.get(k)) for k, v in expected.items()
                 if k in got and got[k] != v}
        invented = [k for k in got if k not in expected]
        missed = [k for k in expected if k not in got]
        assert wrong == {}, f"{condition}/{seed} misread: {wrong}"
        assert missed == [], f"{condition}/{seed} missed: {missed}"
        assert invented == [], f"{condition}/{seed} invented: {invented}"
        # The read also has to say it landed on the sheet, not merely look right.
        assert result["grid_match"] >= O.GRID_MATCH_MIN

    @pytest.mark.parametrize("condition", ["flat-scan", "normal", "hard"])
    def test_the_four_markers_land_where_they_really_are(self, condition):
        """A quad that is *nearly* right reads *nearly* right, which is worse.

        Detection is graded against the true corners rather than accepted for
        returning four of something, because that is the difference between this
        and the failure it replaced: on a hard photo the old contour search
        returned a rectangle of four filled bubbles, 1344 px from the page.
        """
        shot, truth = photograph(render_paper(20), 5, **CONDITIONS[condition])
        frame = shot
        if max(frame.shape[:2]) > 2400:
            factor = 2400 / max(frame.shape[:2])
            frame = cv2.resize(frame, None, fx=factor, fy=factor,
                               interpolation=cv2.INTER_AREA)
            truth = truth * factor
        found = O.find_square_markers(frame)
        assert found is not None, condition
        assert corner_error(found, truth) < 25.0, condition

    def test_an_answer_key_on_the_sheet_changes_nothing_about_reading(self):
        """The scanner reads marks; it must not care what the key says."""
        layout = L.default_layout(20)
        marks, expected = answer_set(20, 5, 11)
        paper = fill(render_paper(20), layout, marks)
        first = O.process_scan(as_upload(photograph(paper, 3, **CONDITIONS["normal"])[0]),
                               total_questions=20)
        second = O.process_scan(as_upload(photograph(paper, 3, **CONDITIONS["normal"])[0]),
                                total_questions=20)
        assert first["answers"] == second["answers"] == expected
        assert first["layout_code"] == second["layout_code"]


# ── refusing is a result ─────────────────────────────────────────────────────

class TestAWrongWarpIsRefusedNotGuessed:
    """Nothing that is not on the page may produce an answer set."""

    @pytest.mark.parametrize("shift", [1, 2, 3])
    def test_a_rotation_the_qr_did_not_decode_is_caught(self, shift):
        """The sheet is 180°-symmetric, so three warps of four are wrong.

        With the QR readable the code settles it. This forces the other path —
        no layout code — and asserts the grid check catches what the geometry
        cannot: rotations 0 and 2 have *identical* anisotropy, so the geometric
        tie-break cannot tell them apart at all.
        """
        layout = L.default_layout(20)
        marks, expected = answer_set(20, 5, 4)
        shot, truth = photograph(fill(render_paper(20), layout, marks), 4,
                                 **CONDITIONS["normal"])
        frame = shot
        factor = 1.0
        if max(frame.shape[:2]) > 2400:
            factor = 2400 / max(frame.shape[:2])
            frame = cv2.resize(frame, None, fx=factor, fy=factor,
                               interpolation=cv2.INTER_AREA)
        markers = O.find_square_markers(frame)
        assert markers is not None
        rotated = markers[shift:] + markers[:shift]
        warped, _H = O._warp_to_page(frame, rotated)
        read = O.read_answers(warped, layout)
        assert read["grid_match"] < O.GRID_MATCH_MIN, (
            f"a wrong warp scored {read['grid_match']}")
        # …and that is what makes the answers it would have returned unusable.
        right = sum(1 for k, v in expected.items() if read["answers"].get(k) == v)
        assert right <= len(expected) * 0.5

    def test_a_blank_sheet_is_not_a_failed_read(self):
        """An unanswered sheet must not be refused — it is a legitimate result.

        The two are easy to confuse: both produce almost no answers. They are told
        apart by the printed outline, which is present at every bubble whether or
        not the student marked it, and absent everywhere once the warp leaves the
        page.
        """
        shot, _truth = photograph(render_paper(20), 6, **CONDITIONS["normal"])
        result = O.process_scan(as_upload(shot), total_questions=20)
        assert "error" not in result, result.get("error")
        assert result["detected"] == 0
        assert result["grid_match"] >= O.GRID_MATCH_MIN
        assert result["grid_match"] > 0.5, "a blank sheet shows nearly every outline"

    def test_the_markers_have_to_be_there_at_all(self):
        """A photograph of something that is not a sheet is refused."""
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 255, (900, 700, 3), dtype=np.uint8)
        result = O.process_scan(cv2.imencode(".jpg", noise)[1].tobytes())
        assert "error" in result

    def test_a_photograph_missing_a_corner_is_refused(self):
        """Crop the corner off and there is no page to find — and it must say so.

        This is the honest half of "the whole sheet must be visible": a marker
        that is not in the picture cannot be found, so the pipeline has to refuse
        rather than fall back on a different geometry and return an answer set
        derived from part of a page. It is also the case a fixture can create by
        accident, which is exactly how it was found.
        """
        layout = L.default_layout(20)
        marks, expected = answer_set(20, 5, 1)
        shot, _truth = photograph(fill(render_paper(20), layout, marks), 1,
                                  **CONDITIONS["normal"])
        cut = shot[:int(shot.shape[0] * 0.62), :]          # lose the bottom
        result = O.process_scan(as_upload(cut), total_questions=20)
        assert "error" in result
        assert "answers" not in result


# ── the format is one format ─────────────────────────────────────────────────

class TestThePrinterAndTheScannerAgreeByConstruction:
    """Both sides read their coordinates out of `omr_layout`; these pin that."""

    def test_the_generator_draws_marks_where_the_reader_looks(self):
        layout = L.default_layout(20)
        paper = render_paper(20)
        # The four marker corners are printed as solid ink where the model says.
        for x_mm, y_mm in L.MARKER_CORNERS_MM:
            x, y = int(round(x_mm / PAPER_MM)), int(round(y_mm / PAPER_MM))
            patch = paper[y - 8:y + 8, x - 8:x + 8]
            assert patch.mean() < 120, f"no ink at ({x_mm}, {y_mm}) mm"

    def test_the_printed_sheet_carries_its_own_description(self):
        """The QR is what makes the sheet self-describing, and it must decode.

        'Compatible' means a sheet printed today reads tomorrow: the code states
        the grid, so a scanner never has to be told how many questions to expect.
        """
        paper = render_paper(20)
        layout = L.default_layout(20)
        warped = cv2.resize(paper, (O.OUT_W, O.OUT_H))
        code = O.read_layout_code(warped)
        assert code is not None, "the layout QR did not decode off a clean render"
        assert code.total_questions == 20
        assert code.options == 5
        assert code.pages == layout.pages
        assert code.code() == layout.code()

    def test_every_sheet_the_generator_can_print_the_reader_can_read(self):
        """Both sides agree about the grid size, from one model.

        A sheet that prints with 25 rows in a column and a reader that computes 24
        would read the last question off the page, so the two counts are asserted
        together rather than trusted.
        """
        for total in (10, 25, 30, 50, 60, 100):
            layout = L.default_layout(total)
            assert L.parse_code(layout.code()) is not None
            assert layout.per_page * layout.pages >= total
            assert L.marker_centres_mm() == L.MARKER_CORNERS_MM
            # Every question has a slot of its own, the grid fills a column
            # top-to-bottom before moving across, and no bubble is drawn where
            # the sheet has no paper. The order matters as much as the positions:
            # the reader walks questions in this order too, so a printer that
            # filled across instead of down would put every answer on the wrong
            # question while still drawing them all inside the margin.
            taken = set()
            for q in range(total):
                page = q // layout.per_page
                slot = q - page * layout.per_page
                col, row = slot // layout.n_rows, slot % layout.n_rows
                assert (page, col, row) not in taken, (total, q)
                taken.add((page, col, row))
                x, y = layout.option_centre_mm(q, 0, page)
                assert L.CONTENT_MARGIN_MM <= x <= L.CONTENT_RIGHT_MM, (total, q)
                assert 0 < y < L.ID_BAND_TOP_MM, (total, q)
                assert abs(y - (L.GRID_FIRST_ROW_MM + row * L.ROW_PITCH_MM)) < 1e-9
                last = layout.option_centre_mm(q, layout.options - 1, page)
                assert last[0] <= L.CONTENT_RIGHT_MM, (total, q)
            # …and the grid cannot reach the ID band however many questions there
            # are, which is what `ROWS_MAX_PER_COLUMN` is for.
            assert (L.GRID_FIRST_ROW_MM
                    + (L.ROWS_MAX_PER_COLUMN - 1) * L.ROW_PITCH_MM) < L.ID_BAND_TOP_MM


class TestTheFormatShownMatchesTheFormatThatPrints:
    """The generator's preview has to depict the sheet the scanner reads.

    A preview is documentation, and this one had drifted: it drew the four corner
    marks as L-shaped rules, which is the geometry the sheet used *before* the
    layout existed and which the scanner can no longer read, because a rule is
    two thin strokes whose apparent thickness changes with distance, blur and
    JPEG. A teacher who checked the preview before printing would have been shown
    a sheet their own scanner could not register.
    """

    PREVIEW = "app/templates/tools/generate_answer_sheet.html"

    def _markup(self):
        with open(self.PREVIEW, encoding="utf-8") as handle:
            return handle.read()

    def test_no_corner_rule_survives_in_the_preview(self):
        markup = self._markup()
        for rule in ("border-l-2 border-t-2", "border-r-2 border-t-2",
                     "border-l-2 border-b-2", "border-r-2 border-b-2"):
            assert rule not in markup, (
                "the preview draws an L-shaped corner rule; the printed sheet's "
                "marks are solid squares")

    def test_the_preview_shows_four_solid_marks(self):
        markup = self._markup()
        assert markup.count('class="w-4 h-4 bg-black"') == 4, (
            "the sheet has four registration marks, one near each corner")

    def test_the_preview_shows_the_student_id_band(self):
        """The scanner reads the ID off the same photograph; it has to be drawn."""
        markup = self._markup()
        assert "NISN" in markup
        assert "x-for=\"d in 10\"" in markup and "x-for=\"v in 10\"" in markup


class TestTheDefectsThisReplacedCannotComeBack:
    """Three failures, each pinned by the property that let it happen."""

    def test_a_photograph_yields_marker_candidates(self):
        """The old binary returned **zero** candidates on any photographed sheet.

        Every dark mark is inside the page, and a photographed page has a bright
        closed frame where paper meets desk — so a contour search that keeps only
        outermost contours saw the frame and every mark as its holes. The flat
        scan the old detector was calibrated on has no desk in it, which is why
        nobody noticed. This asserts candidates exist on a *photo*, which is the
        case that was never measured.
        """
        shot, _truth = photograph(render_paper(20), 2, **CONDITIONS["normal"])
        assert len(O.marker_candidates(shot)) >= 4

    def test_the_candidates_do_not_depend_on_the_page_filling_the_frame(self):
        """A sheet photographed from across the desk still reads."""
        for condition in ("close-up", "far-away"):
            shot, truth = photograph(render_paper(20), 8, **CONDITIONS[condition])
            frame = shot
            if max(frame.shape[:2]) > 2400:
                factor = 2400 / max(frame.shape[:2])
                frame = cv2.resize(frame, None, fx=factor, fy=factor,
                                   interpolation=cv2.INTER_AREA)
                truth = truth * factor
            found = O.find_square_markers(frame)
            assert found is not None, condition
            assert corner_error(found, truth) < 25.0, condition

    def test_a_lighting_gradient_cannot_demote_a_real_marker(self):
        """Marker selection must not be a ranking of how dark a blob looks.

        The bottom-left marker used to fall to rank 34 of 61 under an uneven
        light, because a gradient across the page lowers a marker's absolute
        contrast below that of the filled bubbles in the bright half — and with
        the search capped at the top 14 the true four were never considered
        together. The four markers are on the convex hull of the sheet's dark
        blobs by construction, so that is where the search is anchored.
        """
        for condition in ("hard", "normal"):
            shot, truth = photograph(render_paper(50), 9, **CONDITIONS[condition])
            frame = shot
            if max(frame.shape[:2]) > 2400:
                factor = 2400 / max(frame.shape[:2])
                frame = cv2.resize(frame, None, fx=factor, fy=factor,
                                   interpolation=cv2.INTER_AREA)
                truth = truth * factor
            candidates = O.marker_candidates(frame)
            hull = cv2.convexHull(
                np.array([[m.cx, m.cy] for m in candidates], dtype=np.float32
                         ).reshape(-1, 1, 2), returnPoints=False).ravel()
            for corner in truth:
                nearest = min(np.linalg.norm(np.array([candidates[int(i)].cx,
                                                       candidates[int(i)].cy]) - corner)
                              for i in hull)
                assert nearest < 25.0, f"{condition}: a true marker is off the hull"


# ── one photograph is one page ────────────────────────────────────────────────
#
# The grid is not unique across pages. `option_centre_mm(q, o, page)` puts page
# 2's question 81 at the same millimetres as page 1's question 1, so the reader
# looping `range(layout.pages)` over a single photograph did not read more of the
# sheet — it read the same bubbles again and reported them under a second
# question number. Measured on the committed 100-question LJK: photographing
# page 1 alone returned answers for **all 100** questions, with 81-100 taken from
# 1-20's marks, and nothing in the result said so. On a real two-page exam that
# is twenty scores computed from the wrong part of the paper.

# 160 questions is 2 pages of 80 — both pages equally full, so the two are
# indistinguishable by ink density. That is the case where reading the wrong
# page cannot be caught by anything except knowing which page it is.
SHEET_MULTI = 160
# 100 questions is also 2 pages, but the second carries only 20 questions, so it
# looks obviously wrong to the grid self-check. Kept as the contrast case.
SHEET_SPARSE = 100


@functools.lru_cache(maxsize=None)
def _sheet_page_png(total: int, page_no: int) -> bytes:
    pdf = generate_answer_sheet(total_questions=total, options=5,
                                mark_type="circle", exam_version="A")
    import fitz
    doc = fitz.open(stream=pdf.read(), filetype="pdf")
    pix = doc[page_no].get_pixmap(dpi=SHEET_DPI, colorspace=fitz.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.imencode(".png", img)[1].tobytes()


def render_sheet_page(total, page_no):
    return cv2.imdecode(np.frombuffer(_sheet_page_png(total, page_no), np.uint8),
                        cv2.IMREAD_COLOR)


def fill_page(paper, layout, page_no, marks):
    """Pencil marks placed by *that page's* coordinates."""
    out = paper.copy()
    for q, option in marks:
        x_mm, y_mm = layout.option_centre_mm(q, option, page_no)
        cv2.circle(out, (int(round(x_mm / PAPER_MM)), int(round(y_mm / PAPER_MM))),
                   max(2, int(L.OPTION_RADIUS_MM / PAPER_MM * 0.85)),
                   (MARK_VALUE, MARK_VALUE, MARK_VALUE), -1)
    return out


def page_marks(layout, page_no):
    """Marks for one page, chosen so the two pages *disagree* at every position
    they share on paper — a reader that confused them returns the other page's
    letters rather than something plausible."""
    lo = page_no * layout.per_page
    hi = min(lo + layout.per_page, layout.total_questions)
    shift = 0 if page_no == 0 else 2
    return [(q, (q + shift) % layout.options) for q in range(lo, hi, 3)]


class TestOnePhotographIsOnePage:
    """A page is read as itself, not as a copy of another page."""

    def test_a_page_returns_only_its_own_questions(self):
        layout = L.default_layout(SHEET_MULTI, options=5, mark_type="C", version="A")
        assert layout.pages == 2, "this test assumes 160 questions print as 2 pages"
        for page_no in (0, 1):
            lo = page_no * layout.per_page
            hi = min(lo + layout.per_page, layout.total_questions)
            marks = page_marks(layout, page_no)
            shot, _ = photograph(fill_page(render_sheet_page(SHEET_MULTI, page_no),
                                           layout, page_no, marks),
                                 4, **CONDITIONS["normal"])
            got = O.process_scan(as_upload(shot), total_questions=SHEET_MULTI,
                                 page_index=page_no)
            assert "error" not in got, got.get("error")
            assert (got["first_question"], got["last_question"]) == (lo, hi - 1)
            assert got["page_count"] == 2
            assert got["page_index"] == page_no
            answers = got["answers"]
            invented = sorted(q for q in answers if not lo <= int(q) < hi)
            assert invented == [], (
                f"page {page_no} invented answers for {invented} — questions that "
                "are not in this photograph")
            expected = {str(q): "ABCDEFGH"[o] for q, o in marks}
            wrong = {k: (v, expected[k]) for k, v in answers.items()
                     if expected.get(k) != v}
            assert wrong == {}, wrong
            missed = sorted(set(expected) - set(answers))
            assert missed == [], missed

    def test_reading_the_wrong_page_yields_a_complete_false_answer_set(self):
        """Why the page had to be *passed in* rather than worked out.

        160 questions is two pages of 80, laid out identically, so page 2's
        photograph read as page 1 does not fail and does not look odd: it returns
        a full, confident set of answers for questions 1-80 taken from page 2's
        marks, with nothing in the result reporting a problem. Only the question
        *numbers* are wrong — which is why the guarantee has to be pinned by the
        questions a page carries, and not by whether a scan "succeeded".
        """
        layout = L.default_layout(SHEET_MULTI, options=5, mark_type="C", version="A")
        marks = page_marks(layout, 1)
        shot, _ = photograph(fill_page(render_sheet_page(SHEET_MULTI, 1),
                                       layout, 1, marks),
                             4, **CONDITIONS["normal"])
        upload = as_upload(shot)
        as_page_1 = O.process_scan(upload, total_questions=SHEET_MULTI, page_index=1)
        as_page_0 = O.process_scan(upload, total_questions=SHEET_MULTI, page_index=0)

        assert "error" not in as_page_1, as_page_1.get("error")
        assert set(as_page_1["answers"]) <= {str(q) for q in range(80, 160)}
        # The trap, demonstrated rather than described: the wrong page answers
        # confidently, and about questions that were never in the photograph.
        assert "error" not in as_page_0, (
            "this fixture exists to show the wrong page is NOT refused")
        assert as_page_0["answers"], "the wrong page produces a full answer set"
        assert set(as_page_0["answers"]) <= {str(q) for q in range(0, 80)}
        assert as_page_0["answers"] != as_page_1["answers"]

    def test_a_sparse_second_page_is_additionally_caught_by_the_grid_check(self):
        """A second, independent net — but only when the pages differ in density.

        Page 2 of a 100-question sheet holds 20 questions in the same 4-column
        grid, so a reader that mistook it for page 1 samples 400 bubble positions
        where only 100 are printed and the `grid_match` self-check refuses it. That
        is worth pinning, and worth *not* relying on: the check is about the ink
        it found, so it cannot tell two equally full pages apart — which is the
        case the test above covers.
        """
        layout = L.default_layout(SHEET_SPARSE, options=5, mark_type="C", version="A")
        marks = page_marks(layout, 1)
        shot, _ = photograph(fill_page(render_sheet_page(SHEET_SPARSE, 1),
                                       layout, 1, marks),
                             4, **CONDITIONS["normal"])
        upload = as_upload(shot)
        assert "error" not in O.process_scan(upload, total_questions=SHEET_SPARSE,
                                             page_index=1)
        assert "error" in O.process_scan(upload, total_questions=SHEET_SPARSE,
                                         page_index=0)

    def test_a_single_page_sheet_is_unaffected(self):
        """Every sheet at or below 80 questions still reads in full.

        This is the regression that matters for what is already on paper: a
        one-page sheet has to behave exactly as it did before, and it does — one
        page means the covered range is the whole sheet.
        """
        layout = L.default_layout(50, options=5, mark_type="C", version="A")
        assert layout.pages == 1
        marks, expected = answer_set(50, 5, seed=21)
        shot, _ = photograph(fill_page(render_paper(50), layout, 0, marks),
                             21, **CONDITIONS["normal"])
        got = O.process_scan(as_upload(shot), total_questions=50, page_index=0)
        assert "error" not in got, got.get("error")
        assert (got["first_question"], got["last_question"]) == (0, 49)
        assert got["page_count"] == 1
        assert got["answers"] == expected

    def test_a_page_number_beyond_the_sheet_is_clamped_not_crashed(self):
        """A client sending a page the sheet does not have reads the last page."""
        layout = L.default_layout(SHEET_MULTI, options=5, mark_type="C", version="A")
        marks = page_marks(layout, 1)
        shot, _ = photograph(fill_page(render_sheet_page(SHEET_MULTI, 1),
                                       layout, 1, marks),
                             4, **CONDITIONS["normal"])
        got = O.process_scan(as_upload(shot), total_questions=SHEET_MULTI,
                             page_index=99)
        assert "error" not in got, got.get("error")
        assert got["page_index"] == layout.pages - 1
        assert set(got["answers"]) <= {str(q) for q in range(80, 160)}
        assert "error" not in O.process_scan(as_upload(shot),
                                             total_questions=SHEET_MULTI,
                                             page_index=-3)


# ── the committed LJK formats ────────────────────────────────────────────────

LJK_DIR = pathlib.Path(__file__).resolve().parents[2] / "docs" / "ljk"


def _ljk_files():
    return sorted(LJK_DIR.glob("*.pdf"))


def read_layout_from_pdf(path):
    """The layout a file says it is — read out of the file's own metadata.

    That is the same code the sheet prints as a QR, so this also checks the
    format is self-describing: a reader that has never seen the file can learn
    its geometry from it.
    """
    import fitz
    doc = fitz.open("pdf", path.read_bytes())
    layout = L.parse_code((doc.metadata or {}).get("subject", ""))
    assert layout is not None, (
        f"{path.name} does not carry a readable layout code in its metadata")
    return layout

class TestTheCommittedLJKFormatsScanBack:
    """The sheets a teacher prints must be the sheets this reader can read.

    `docs/ljk/` is the deliverable: a school prints one and photocopies it. These
    tests read the *bytes on disk* rather than a fresh in-memory render, so a
    regenerated PDF that no longer matches the geometry the scanner was built
    around cannot hide behind a passing test.
    """

    def test_the_formats_are_present_and_one_is_multi_page(self):
        files = _ljk_files()
        assert files, f"no LJK format committed in {LJK_DIR}"
        layouts = {f.name: read_layout_from_pdf(f) for f in files}
        assert any(lay.pages > 1 for lay in layouts.values()), (
            "at least one committed format must exercise a multi-page sheet")
        # The single-page ones are the common case and must stay the common case.
        assert any(lay.pages == 1 for lay in layouts.values())

    @pytest.mark.parametrize("path", _ljk_files() or [None],
                             ids=lambda p: p.name if p else "no-ljk")
    def test_each_committed_format_round_trips(self, path):
        if path is None:
            pytest.skip("no LJK format committed")
        import fitz
        layout = read_layout_from_pdf(path)
        doc = fitz.open("pdf", path.read_bytes())
        assert doc.page_count == layout.pages, (
            "the PDF's page count must match the geometry its code describes")

        for page_no in range(layout.pages):
            lo = page_no * layout.per_page
            hi = min(lo + layout.per_page, layout.total_questions)
            # A pencil mark, not every bubble filled: a blank is information.
            marks = [(q, q % layout.options) for q in range(lo, hi, 2)]
            pix = doc[page_no].get_pixmap(dpi=SHEET_DPI, colorspace=fitz.csRGB)
            paper = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3)
            paper = cv2.cvtColor(paper, cv2.COLOR_RGB2BGR)
            shot, _ = photograph(fill_page(paper, layout, page_no, marks),
                                 5, **CONDITIONS["normal"])
            got = O.process_scan(as_upload(shot), total_questions=layout.total_questions,
                                 page_index=page_no)
            assert "error" not in got, f"{path.name} p{page_no}: {got.get('error')}"
            expected = {str(q): "ABCDEFGH"[o] for q, o in marks}
            answers = got["answers"]
            assert set(answers) <= {str(q) for q in range(lo, hi)}, (
                f"{path.name} p{page_no} answered questions outside the page")
            wrong = {k: (v, expected[k]) for k, v in answers.items()
                     if expected.get(k) != v}
            assert wrong == {}, f"{path.name} p{page_no}: {wrong}"
            assert set(expected) <= set(answers), (
                f"{path.name} p{page_no} missed "
                f"{sorted(set(expected) - set(answers))}")

    def test_the_student_id_band_on_the_committed_format_reads_back(self):
        """The NISN band is what tells the app whose sheet this is."""
        files = _ljk_files()
        if not files:
            pytest.skip("no LJK format committed")
        import fitz
        path = files[0]
        layout = read_layout_from_pdf(path)
        nisn = "0071234567"
        assert L.ID_DIGITS >= len(nisn), (
            "the sample ID must fit the band the format prints")
        pix = fitz.open("pdf", path.read_bytes())[0].get_pixmap(
            dpi=SHEET_DPI, colorspace=fitz.csRGB)
        paper = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3), cv2.COLOR_RGB2BGR)
        filled = paper.copy()
        for digit, value, x_mm, y_mm in layout.id_bubbles():
            if digit < len(nisn) and int(nisn[digit]) == value:
                cv2.circle(filled, (int(round(x_mm / PAPER_MM)),
                                    int(round(y_mm / PAPER_MM))),
                           max(2, int(L.ID_RADIUS_MM / PAPER_MM * 0.85)),
                           (MARK_VALUE, MARK_VALUE, MARK_VALUE), -1)
        shot, _ = photograph(filled, 5, **CONDITIONS["normal"])
        got = O.process_scan(as_upload(shot),
                             total_questions=layout.total_questions, page_index=0)
        assert "error" not in got, got.get("error")
        assert got.get("nisn") == nisn, got.get("nisn")


class TestThePageTravelsWithTheScan:
    """The page has to reach the reader from every door into it."""

    ROOT = pathlib.Path(__file__).resolve().parents[2]

    def _text(self, rel):
        return (self.ROOT / rel).read_text(encoding="utf-8", errors="replace")

    def _fn(self, rel, name):
        body = self._text(rel)
        start = body.index(f"def {name}(")
        try:
            end = body.index("\ndef ", start + 1)
        except ValueError:
            end = len(body)
        return body[start:end]

    def test_the_reader_does_not_read_every_page_from_one_photograph(self):
        # The *code* shape, not the words: the function's own docstring explains
        # the loop it replaced, so a substring test on the bare phrase would fail
        # on the explanation rather than on a defect.
        fn = self._fn("app/services/omr_service.py", "read_answers")
        assert "page_index" in fn
        assert "for page in range(layout.pages)" not in fn, (
            "reading every page from one photograph invents answers for the "
            "pages that are not in the picture")

    def test_the_scan_entry_point_accepts_a_page(self):
        fn = self._fn("app/services/omr_service.py", "process_scan")
        assert "page_index" in fn
        assert "read_answers(warped, layout, page_index=page_index)" in fn, (
            "process_scan must pass the page on to the reader")

    def test_both_doors_into_the_scanner_pass_the_page_through(self):
        """The sync route and the Celery task are the two ways a scan arrives."""
        # Scoped to the function, not the file: the bulk route carries the same
        # expression, so a file-wide substring test would be satisfied by the
        # *other* door and let the single scan stop reading the page.
        route = self._fn("app/routes/api.py", "scan_process")
        assert 'page_index = max(0, int(request.form.get("page", 0) or 0))' in route, (
            "the route has to read the page the client sent")
        assert "page_index=page_index)" in route, (
            "the route has to hand the page to the reader")
        bulk = self._fn("app/routes/api.py", "scan_bulk")
        assert 'page_index=max(0, int(request.form.get("page", 0) or 0))' in bulk, (
            "the bulk route has to hand the page to the task")
        tasks = self._text("app/services/omr_tasks.py")
        assert "page_index" in self._fn("app/services/omr_tasks.py", "_run_omr")
        assert "process_scan(image_data, total_questions=total_questions,\n                          preprocess=True, page_index=page_index)" in tasks

    def test_the_scan_page_asks_which_page_and_sends_it(self):
        """The third door: the teacher's own scan screen.

        A page number the UI never asks for is a page number the reader never
        gets, so the control and both upload paths are pinned here.
        """
        ui = self._text("app/templates/teacher/scan.html")
        assert 'id="page-select"' in ui, "the screen must offer the page"
        assert "function refreshPageSelect(" in ui
        # Anchored to the exam-select handler. Three call sites exist (that
        # handler, the manual question-count input, and one at load), so a bare
        # search for the call is satisfied by the other two.
        assert ("classList.toggle('hidden', this.value !== '__custom__');"
                "\n    refreshPageSelect(currentTotalQ());") in ui, (
            "the control has to be populated when the exam changes")
        assert ui.count("refreshPageSelect(currentTotalQ());") >= 3, (
            "every door that changes the question count must repopulate the page list")
        assert ui.count("fd.append('page', currentSheetPage())") + \
            ui.count("formData.append('page', currentSheetPage())") == 2, (
            "both the single scan and the bulk upload must send the page")
        # Above 80 questions the sheet is printed as more than one page.
        assert "Math.ceil((parseInt(totalQ, 10) || 50) / 80)" in ui, (
            "the page count must mirror the layout's 80-per-page grid")

    def test_saving_a_page_merges_it_instead_of_replacing_the_sheet(self):
        """Page 2 must not erase page 1.

        `scan_save` used to write `answers` wholesale, so the second page of a
        two-page sheet deleted the first — and grading ran over one page's
        questions. Both are now impossible: the stored answers outside the
        scanned range are kept and the score is computed over the union.
        """
        fn = self._fn("app/routes/api.py", "scan_save")
        # The exact expressions, not their keywords: `merged = dict(answers)` and
        # a dropped `and not adds_new` both leave the words behind, so a looser
        # assertion here passes on precisely the defects it is meant to catch.
        assert "merged = {k: v for k, v in stored.items() if _qkey(k) not in covered}" in fn, (
            "stored answers outside the scanned page must be kept")
        assert "detected = merged" in fn, (
            "grading has to run over the union, not over the page")
        assert 'if current_status in ("graded", "published") and not adds_new:' in fn, (
            "a re-scan of a saved page must still be refused")
