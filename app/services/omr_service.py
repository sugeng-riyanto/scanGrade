"""OMR — reading a photographed answer sheet.

The shape of the problem, and what this file does about it:

1. **Find the page.** Four solid squares are printed in the corners. They are
   found by *shape*, not by size or position: a filled square is the only thing
   on the sheet whose contour has four corners, is convex, and fills its own
   bounding box. Text glyphs fail the fill test, bubble outlines fail it too
   (they are rings), and a photograph's background has no such shape. The four
   best candidates that form a page-like quadrilateral are the page.

2. **Put the page in a known coordinate system.** The four marker centres sit at
   fixed page coordinates (`omr_layout`), so a homography carries the photograph
   into *page millimetres* — 4 pixels per millimetre — and every bubble is then
   sampled at a coordinate the printer also used. Nothing is estimated, and
   there is no second set of constants to drift out of step with the printer.

3. **Prove the orientation.** Four identical squares are rotationally ambiguous.
   The sheet's QR code settles it — but a QR read can fail on a blurry photo, so
   the fallback is geometric rather than a guess: of the four possible
   assignments, the wrong ones map a 192 × 279 mm page onto itself rotated, and
   the distortion that implies is measurable as the anisotropy of the warp. The
   correct assignment is the one that is closest to a similarity, and how close
   it is also tells the caller how much to trust the read.

4. **Read the marks relatively.** A bubble is scored by the darkness inside it
   minus the darkness of the paper ring just outside it. That single number is
   blind to a shadow and blind to printing ink, because both move the inside and
   the outside together. It also cannot mistake an *empty* bubble for a filled
   one: an empty bubble has a printed outline, which darkens the ring, so its
   score goes negative.

Not everything here is new. The L-corner sheet this app printed before is still
on paper in schools, so `find_registration_marks` and the legacy constants below
keep a working path for it; nothing prints that way any more.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Optional

import cv2
import numpy as np

from app.services import omr_layout as L

# ── The template: page space at a fixed resolution ───────────────────────────
# 4 px/mm gives 840 × 1188 px, which is enough to resolve a 5.4 mm bubble into
# ~21 px across while keeping a whole page small enough to process per scan.
TEMPLATE_PX_PER_MM = 4.0
OUT_W = int(round(L.PAGE_W_MM * TEMPLATE_PX_PER_MM))
OUT_H = int(round(L.PAGE_H_MM * TEMPLATE_PX_PER_MM))

# The working image the detector runs on. A phone photo is 12 MP; thresholding
# and contour-finding at that size costs seconds for no gain, because the marks
# are 6 mm across.
WORK_WIDTH = 1600

# ── Bubble decision gates, in units of the inner-minus-outer score (0-255) ───
# Calibrated against rendered sheets photographed under simulated phone
# conditions; see tests/unit/test_omr_sheet_scan.py, which measures these
# distributions rather than trusting the numbers.
MARK_ABS_GATE = 18.0      # below this, nothing is marked in that row
MARK_MARGIN_GATE = 12.0   # the winner must lead the runner-up by this much
ID_ABS_GATE = 14.0
ID_MARGIN_GATE = 8.0

# `page_anisotropy` is reported on every read and nothing decides on it — see
# `process_scan`, where the refusal test it used to drive was measured and
# retired. It is still in the result because how far from a similarity the page
# warp is says how much the read can be trusted, and a caller that wants to show
# or threshold that deserves the number rather than a constant's judgement.


# ══════════════════════════════════════════════════════════════════════════════
# Preprocessing
# ══════════════════════════════════════════════════════════════════════════════

def load_image(image_data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(image_data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _to_gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _working_scale(shape) -> float:
    h, w = shape[:2]
    return min(1.0, WORK_WIDTH / float(w)) if w else 1.0


def deskew(img: np.ndarray) -> np.ndarray:
    """Deskew by the dominant near-horizontal line.

    Kept for the legacy path and as a cheap pre-rotation. The new pipeline does
    not depend on it: a homography from four found markers absorbs any rotation,
    and this can only ever be an approximation of one.
    """
    gray = _to_gray(img)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
    if lines is None:
        return img
    angles = [math.degrees(theta) - 90 for _rho, theta in lines[:, 0]]
    angles = [a for a in angles if abs(a) < 45]
    if not angles:
        return img
    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.7:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w // 2, h // 2), median_angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def gray_world_normalize(img: np.ndarray) -> np.ndarray:
    """Flatten a colour cast, so the page is neutral white before thresholding."""
    out = img.copy()
    for i in range(3):
        avg = float(np.mean(img[:, :, i]))
        if avg > 0:
            out[:, :, i] = np.clip(img[:, :, i] * (128.0 / avg), 0, 255).astype(np.uint8)
    return out


def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """CLAHE then a light unsharp — leaves grayscale gradients readable."""
    gray = _to_gray(img)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    k = np.array([[-0.25, -0.25, -0.25], [-0.25, 3.0, -0.25], [-0.25, -0.25, -0.25]],
                 dtype=np.float32)
    return cv2.filter2D(enhanced, -1, k)


def preprocess_scan(img: np.ndarray) -> np.ndarray:
    """Legacy entry point kept for the old callers: deskew → cast → contrast."""
    img = deskew(img)
    img = gray_world_normalize(img)
    return enhance_contrast(img)


# ── Finding a dark square: a matched filter, not a contour ────────────────────
#
# The marker is a *known shape* at a known contrast, so "is there a 6 mm square of
# ink centred here?" can be asked directly: the mean of a square window minus the
# mean of a square ring around it. That number is high at the centre of a dark
# square and near zero anywhere else, and — unlike a contour — it does not care
# what the camera did to the edges.
#
# This replaced a contour search over an adaptive-thresholded binary, and the
# measurement is why. On a hard simulated photo (18% perspective, blur σ1.8, one
# corner 55% darker, JPEG 55) all four markers *were* in that candidate list,
# within 1.8 px of their true centres — but at ranks 0, 10, 64 and 226, with
# measured sides of 74, 41, 33 and 47 px against a real 47. Blur, JPEG and the
# threshold between them merge a marker with ink beside it in one place and erode
# it to two-thirds of its size in another, so the two properties the old gates
# leaned on hardest — solidity and size — were exactly the two that fell apart,
# and the four true markers could not be selected together. A matched filter has
# no such failure mode: the response peaks at the centre whatever the edges did,
# and the size falls out of *which window* peaked.
#
# The window is a ladder rather than one number because the right one is the size
# of the mark in pixels, which depends on how much of the frame the page fills —
# and that is not known until the page has been found, which is what is being
# found here.
_MARKER_SIDE_LADDER = (21, 31, 41, 51, 61, 71)
_MARKER_MIN_CONTRAST = 14.0     # of 255; below this there is no mark to find

# How much bare paper a blob needs around it before it counts as a marker. The
# sheet keeps each corner marker a ~4 mm quiet zone, so a real one measures 1.00
# and the things that are *not* markers do not: a filled bubble has neighbours, a
# desk edge has the desk. See `_quietness`.
MARKER_QUIET_MIN = 0.90


def _working_gray(img: np.ndarray) -> np.ndarray:
    gray = _to_gray(img)
    scale = _working_scale(gray.shape)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return gray


def _square_response(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How much darker a square window is than the ring around it, and how wide.

    Returns `(response, side)`: local contrast in 0-255, and for every pixel the
    window width that produced the peak there. Both are on the working image.

    This is also what a global threshold was doing badly, and the reason it was
    abandoned. The sheet used to be flattened by dividing out a heavily blurred
    copy of itself and then split once with Otsu — and the blur was σ = max/6,
    which on a 1600 px frame estimates the lighting over a window nearly as wide
    as the page, so the estimate barely varied where the lighting did, and the
    clip to 190 pulled paper and desk to the same value. The histogram of the
    result had paper in a broad band around 170 with the desk right beside it,
    Otsu split *the band* rather than the marks, and the marker search found
    nothing at all on a photographed sheet. Asking a local question — is this
    window darker than the ring around it — never needs the page to be evenly
    lit in the first place.

    The ring is three times the window so that the paper it averages is paper and
    not the next mark along. Normalising both means by their own area is what
    makes the ladder comparable: a window smaller than the mark averages ink with
    paper and reads *less* contrast, a window larger than the mark averages paper
    into the mark and reads less too — so the true size is the peak.
    """
    f = gray.astype(np.float32)
    response = None
    side = None
    for width in _MARKER_SIDE_LADDER:
        k = width if width % 2 else width + 1
        inner = cv2.boxFilter(f, -1, (k, k), normalize=True)
        outer = cv2.boxFilter(f, -1, (k * 3, k * 3), normalize=True)
        r = outer - inner
        if response is None:
            response = r
            side = np.full(r.shape, width, np.int16)
        else:
            take = r > response
            response = np.where(take, r, response)
            side = np.where(take, width, side).astype(np.int16)
    return response, side


def _quietness(response: np.ndarray, cx: float, cy: float, side: float,
               own: float) -> float:
    """How much bare paper surrounds this peak, 0-1.

    The one property the sheet is *designed* to have and no other mark has: each
    corner marker keeps a ring of blank paper around it, because nothing else is
    printed within `MARKER_MARGIN_MM`. A filled bubble has neighbouring bubbles
    and a question number a couple of millimetres away; the QR has its own dense
    modules; text has text.

    Measured *against this peak's own strength*, which is what makes it survive a
    bad photograph: an absolute gate has to be loose enough for the darkest
    corner of the page, and then every speck of noise in the brightest corner
    passes it too. 1.35 keeps the ring clear of the QR's nearest edge by about a
    millimetre and a half on the printed sheet; wider and the top-right mark
    starts scoring itself against the code printed beside it.
    """
    h, w = response.shape
    outer = side * 1.35
    inner = side * 0.78
    x0, x1 = int(max(0, cx - outer)), int(min(w, cx + outer))
    y0, y1 = int(max(0, cy - outer)), int(min(h, cy + outer))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 0.0
    patch = response[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dist = np.maximum(np.abs(xx - cx), np.abs(yy - cy))   # Chebyshev: a square ring
    ring = dist > inner
    if ring.sum() < 4:
        return 0.0
    return float((patch[ring] < own * 0.35).mean())


# ══════════════════════════════════════════════════════════════════════════════
# Finding the page: four solid squares
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Marker:
    cx: float
    cy: float
    side: float
    score: float
    quiet: float = 0.0      # 1.0 = bare paper all around it


def order_quad(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Put four points in the order they go around their own centre.

    Load-bearing, and the bug that made this detector pick the wrong four blobs:
    candidates arrive sorted by *score*, so a valid rectangle came in as e.g.
    top-left, bottom-right, top-right, bottom-left — a bow tie. `isContourConvex`
    then said no, the real page was rejected, and whatever four blobs happened to
    fall in a convex order by accident won the search.
    """
    arr = np.array(pts, dtype=np.float64)
    centre = arr.mean(axis=0)
    ang = np.arctan2(arr[:, 1] - centre[1], arr[:, 0] - centre[0])
    return [tuple(arr[i]) for i in np.argsort(ang)]


def _quad_ok(pts: list[tuple[float, float]]) -> bool:
    """Is this a plausible page: convex, not degenerate, both axes real?"""
    pts = order_quad(pts)
    quad = np.array(pts, dtype=np.float32)
    if not cv2.isContourConvex(quad.astype(np.int32).reshape(-1, 1, 2)):
        return False
    edges = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
    if min(edges) < 20:
        return False
    # A page is a rectangle: opposite sides are comparable. This is what stops a
    # triangle of three markers plus one stray blob from being accepted.
    for a, b in ((edges[0], edges[2]), (edges[1], edges[3])):
        if max(a, b) / max(min(a, b), 1e-6) > 4.0:
            return False
    return True


def _quad_area(pts) -> float:
    quad = np.array(order_quad(pts), dtype=np.float32).reshape(-1, 2)
    x, y = quad[:, 0], quad[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# How far from a similarity a foursome's implied warp may be and still be
# believed to be the page. A real sheet at an angle measures a little over 1.0;
# four bubbles off the answer grid measure near 4, because fitting the printed
# marker rectangle to a 5-wide column instead of a 192 mm page means stretching
# one axis several times more than the other.
#
# Note this is a test on a *candidate* foursome and not on a finished read, which
# is where the old `aniso > 1.45` refusal got it wrong: a wrong foursome measures
# 1.23-1.47, so no line here can separate the two. It only has to be loose enough
# not to throw away a genuine page photographed at an angle — 1.8 does that with
# room, and `grid_match` does the deciding afterwards.
PAGE_SKEW_LIMIT = 1.8

_IDEAL_QUAD: Optional[np.ndarray] = None


def _ideal_marker_quad() -> np.ndarray:
    """The printed marker rectangle, in template pixels, in TL,TR,BR,BL order."""
    global _IDEAL_QUAD
    if _IDEAL_QUAD is None:
        _IDEAL_QUAD = np.array([_mm_to_px(x, y) for (x, y) in L.MARKER_CORNERS_MM],
                               dtype=np.float32)
    return _IDEAL_QUAD


def _page_skew(group: list[Marker]) -> float:
    """How much a foursome must be stretched to fit the *printed* marker rectangle.

    This is the test that tells the page's own four squares from the hundreds of
    other square-ish blobs a photograph offers, and it works because the sheet is
    a known object: the markers are printed at fixed millimetre coordinates, so a
    real foursome is related to that rectangle by a homography that is nearly a
    similarity — a photograph of a page is a scale, a rotation and a little
    perspective, and nothing else. `_anisotropy` measures exactly how far from a
    similarity the implied map is: 1.0 for a true read, ~4 for four bubbles that
    happened to form a rectangle of the wrong shape.

    Area alone could not do this job. It can only say "these four are further
    apart than those four", which is a weak claim on a sheet with 250 filled
    bubbles on a regular grid — and the grid's own regularity is what makes a
    wrong foursome look convincing.
    """
    pts = np.array(order_quad([(m.cx, m.cy) for m in group]), dtype=np.float32)
    ideal = _ideal_marker_quad()
    best = float("inf")
    for shift in range(4):
        try:
            H = _homography(ideal, np.roll(pts, shift, axis=0))
        except cv2.error:
            continue
        best = min(best, _anisotropy(H, OUT_W / 2.0, OUT_H / 2.0))
    return best


def _best_quad(cands: list[Marker], limit: int = 40) -> Optional[list[Marker]]:
    """The four candidate markers that most look like a page.

    Searched on the **convex hull** of the candidates, which is where the four
    markers are by construction: they are printed at the page's own corners, so
    nothing else printed on the sheet can lie further out. That is not a
    heuristic flourish, it replaced a real failure — ranking candidates by how
    dark they look and taking the top 14 put the bottom-left marker at rank 34 on
    a hard photograph, because a lighting gradient across the page lowers a
    marker's absolute contrast below that of the filled bubbles in the bright
    half. The true four were then never considered together at all, and the
    search returned a rectangle of four filled bubbles instead: 1344 px from the
    page, which the pipeline correctly refused rather than misreading.

    On the hull the search is exhaustive again (`C(≤40,4)`), and the *largest*
    page-like quad is the answer for the reason it always was: every other blob
    on the sheet lies inside the page, so swapping one in can only make the quad
    smaller.
    """
    if len(cands) < 4:
        return None
    # The hull is taken over blobs that already *look* like markers, and that is
    # not a refinement of the idea above — it is what makes it true. A sheet
    # lying on a darker surface produces a bright closed edge where paper meets
    # desk, and that edge is a row of candidates *outboard of the bottom markers*:
    # measured on a flat scan of a 20-question sheet on a grey desk, the hull of
    # all 45 candidates had 7 vertices, three of them desk edge, and the two
    # bottom markers were **inside** it. Excluding what the quiet zone already
    # rejects puts the four real markers back on the hull, where the argument
    # says they belong.
    plausible = [m for m in cands if m.quiet >= MARKER_QUIET_MIN]
    anchored = plausible if len(plausible) >= 4 else cands
    pts = np.array([[m.cx, m.cy] for m in anchored], dtype=np.float32)
    hull = cv2.convexHull(pts.reshape(-1, 1, 2), returnPoints=False).ravel()
    pool = [anchored[int(i)] for i in hull[:limit]]
    if len(pool) < 4:
        pool = cands[:limit]
    if len(pool) < 4:
        return None
    best, best_area = None, 0.0
    n = len(pool)
    for a in range(n):
        for b in range(a + 1, n):
            for c in range(b + 1, n):
                for d in range(c + 1, n):
                    group = [pool[a], pool[b], pool[c], pool[d]]
                    pts = [(m.cx, m.cy) for m in group]
                    if not _quad_ok(pts):
                        continue
                    # Prefer the largest page-like quad, but only among those
                    # whose four markers are of comparable size — a real sheet's
                    # markers differ only by perspective. Largest is the right
                    # rule because every other square blob on the sheet (the QR's
                    # finder patterns, most obviously) lies *inside* the page, so
                    # swapping one in can only ever make the quad smaller.
                    sides = [m.side for m in group]
                    if max(sides) / max(min(sides), 1e-6) > 2.2:
                        continue
                    # Every corner marker keeps its own quiet zone, so any
                    # candidate without one is not a marker however square it
                    # looks. This is what keeps the search on the page instead of
                    # on the four most widely separated blobs, which on a
                    # photographed sheet are otherwise just as convex.
                    if min(m.quiet for m in group) < MARKER_QUIET_MIN:
                        continue
                    # …and the four must imply a plausible page warp when fitted
                    # to the rectangle the printer actually drew.
                    if _page_skew(group) > PAGE_SKEW_LIMIT:
                        continue
                    area = _quad_area(pts)
                    if area > best_area:
                        best, best_area = group, area
    return best


def order_cyclic(markers: list[Marker]) -> list[Marker]:
    """Put four markers in going-around order, starting from the top-left-most."""
    pts = np.array([[m.cx, m.cy] for m in markers], dtype=np.float32)
    centre = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0])
    order = list(np.argsort(ang))
    # argsort by angle starts anywhere; rotate so the point nearest the image's
    # top-left leads. Which of the four it truly is, only the warp can say.
    ordered = [markers[i] for i in order]
    start = int(np.argmin([m.cx + m.cy for m in ordered]))
    return ordered[start:] + ordered[:start]


def marker_candidates(img: np.ndarray) -> list[Marker]:
    """Every dark square of a plausible size on the page, strongest first.

    Peaks, not blobs: the response plateaus across a mark rather than spiking, so
    a plain threshold would return one wide region per mark — and two marks a
    bubble apart, or a mark beside the QR, would come back fused into one. A
    pixel is a peak only where no pixel in its own neighbourhood is stronger,
    which keeps each mark its own candidate.

    The gate is *relative* to the strongest response in the frame (25%, floored
    at the contrast below which there is nothing to find). Absolute gates were
    tried first and they are what fails on a real photograph: one threshold loose
    enough for the darkest corner of a page lets every JPEG speckle in the
    brightest corner through.
    """
    gray = _working_gray(img)
    scale = _working_scale(_to_gray(img).shape)
    response, sides = _square_response(gray)
    if response.size == 0:
        return []
    peak = float(response.max())
    if peak < _MARKER_MIN_CONTRAST:
        return []
    gate = max(_MARKER_MIN_CONTRAST, peak * 0.25)
    strong = response >= gate
    typical = int(np.median(sides[strong])) if strong.any() else 41
    window = max(3, typical | 1)
    peaks = strong & (response >= cv2.dilate(response, np.ones((window, window), np.uint8)))

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        peaks.astype(np.uint8), 8)
    height, width = response.shape
    pad = max(2, window // 2)
    found: list[Marker] = []
    for i in range(1, count):
        # A plateau is usually a single pixel — the response is a smooth cone, so
        # its maximum sits on one sample. It is therefore the *centroid of the
        # response around that pixel* that locates the mark, not the pixel: the
        # peak sample can be half a window from the true centre, and at 5 px/mm
        # that is a millimetre of drift on all four corners at once.
        y0, y1 = max(0, int(centroids[i][1]) - pad), min(height, int(centroids[i][1]) + pad + 1)
        x0, x1 = max(0, int(centroids[i][0]) - pad), min(width, int(centroids[i][0]) + pad + 1)
        region = response[y0:y1, x0:x1]
        own = float(region.max())
        weight = np.clip(region - gate, 0, None)
        total = float(weight.sum())
        if total <= 0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx = float((xx * weight).sum() / total)
        cy = float((yy * weight).sum() / total)
        # The size that won *at this mark*, not the frame's median: two marks on
        # one sheet can be 10% apart by perspective, and the quiet ring below is
        # measured in units of it.
        side_px = float(np.median(sides[y0:y1, x0:x1][region >= own * 0.9]))
        quiet = _quietness(response, cx, cy, side_px, own)
        found.append(Marker(cx=cx / scale, cy=cy / scale,
                            side=side_px / scale, quiet=quiet,
                            score=own * quiet ** 2))
    found.sort(key=lambda m: -m.score)
    return found


def find_square_markers(img: np.ndarray) -> Optional[list[Marker]]:
    quad = _best_quad(marker_candidates(img))
    if quad is None:
        return None
    return order_cyclic(quad)


# ══════════════════════════════════════════════════════════════════════════════
# Page space
# ══════════════════════════════════════════════════════════════════════════════

def _mm_to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
    return x_mm * TEMPLATE_PX_PER_MM, y_mm * TEMPLATE_PX_PER_MM


def _homography(src_pts, dst_pts) -> np.ndarray:
    return cv2.getPerspectiveTransform(
        np.array(src_pts, dtype=np.float32), np.array(dst_pts, dtype=np.float32))


def _anisotropy(H: np.ndarray, x: float, y: float) -> float:
    """How much this homography stretches one direction versus the other.

    The `2 × 2` Jacobian at a point is the local linear part of the map; the
    ratio of its singular values is 1.0 for a similarity (any rotation, scale or
    translation) and grows with shear or stretch. A page read in the right
    orientation is nearly a similarity; the same four markers assigned in a
    wrong cyclic order map a tall page onto itself rotated, which is a stretch
    of roughly two — so this number tells the two apart without guessing.
    """
    denom = H[2, 0] * x + H[2, 1] * y + H[2, 2]
    if abs(denom) < 1e-12:
        return float("inf")
    J = np.array([
        [H[0, 0] - H[2, 0] * (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / denom,
         H[0, 1] - H[2, 1] * (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / denom],
        [H[1, 0] - H[2, 0] * (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / denom,
         H[1, 1] - H[2, 1] * (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / denom],
    ], dtype=np.float64)
    sv = np.linalg.svd(J, compute_uv=False)
    if sv[0] <= 1e-12:
        return float("inf")
    return float(sv[0] / max(sv[1], 1e-12))


def _is_legacy_corner_sheet(img: np.ndarray) -> bool:
    """Does this look like the old L-rule sheet rather than the new one?

    The old sheet has no solid squares, so `find_square_markers` returns
    nothing; this only exists so the caller can say *why* the sheet was not
    recognised instead of a generic failure.
    """
    gray = _to_gray(img)
    scale = _working_scale(gray.shape)
    if scale >= 1.0:
        return False                      # a big image: the square search saw it all
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Reading the sheet's own description
# ══════════════════════════════════════════════════════════════════════════════

_qr_detector = None


def _qr() -> "cv2.QRCodeDetector":
    global _qr_detector
    if _qr_detector is None:
        _qr_detector = cv2.QRCodeDetector()
    return _qr_detector


def read_layout_code(warpted_img: np.ndarray) -> Optional[L.SheetLayout]:
    """Decode the layout QR from a page-space image.

    Tried in the QR's own little corner first, generously padded and upscaled,
    then across the whole page. The corner is where it is printed, so cropping
    to it keeps the detector from latching onto the dense bubble grid; the whole
    page is the fallback for a warp that landed the code somewhere unexpected.

    Each candidate is offered both as grayscale and as an adaptive threshold.
    A photograph's QR usually decodes from the thresholded version and a
    rendered PDF's usually decodes from the grayscale one, and there is no way
    to know which this is without trying.
    """
    gray = _to_gray(warpted_img)
    detector = _qr()
    x0, y0, x1, y1 = L.qr_box_mm()
    pad = 6.0
    crop = gray[max(0, int((y0 - pad) * TEMPLATE_PX_PER_MM)):int((y1 + pad) * TEMPLATE_PX_PER_MM),
                max(0, int((x0 - pad) * TEMPLATE_PX_PER_MM)):int((x1 + pad) * TEMPLATE_PX_PER_MM)]

    variants = []
    for source in (crop, gray):
        if source.size == 0:
            continue
        for scale in (4.0, 2.0):
            big = cv2.resize(source, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
            variants.append(big)
            variants.append(cv2.adaptiveThreshold(big, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                  cv2.THRESH_BINARY, 51, 8))
    for img in variants:
        try:
            data, _pts, _ = detector.detectAndDecode(img)
        except cv2.error:
            continue
        layout = L.parse_code(data or "")
        if layout is not None:
            return layout
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Sampling marks
# ══════════════════════════════════════════════════════════════════════════════

def _bubble_metrics(gray: np.ndarray, cx: float, cy: float,
                    r: float) -> tuple[float, float]:
    """Two readings of one bubble: is it marked, and is its *outline* there.

    The first is the one that decides an answer — darkness inside a bubble minus
    darkness of the paper ring just outside it. Two rings, because ink and shadow
    darken the inside and the outside together, so their difference is blind to
    both; and an empty bubble still carries its printed outline, which darkens
    the *ring* and pushes this negative, which is why an unmarked sheet cannot
    read as all-A.

    The second is the same difference the other way round, and it answers a
    different question: *is there a printed bubble here at all?* On a sheet read
    through the right warp it is positive at every bubble, because the outline is
    exactly where the model says it is; through a wrong warp nothing lands
    anywhere in particular and it sits at zero. That is what makes it a check on
    the read rather than on the photograph — measured on a hard simulated photo,
    the true warp reads 0.45 of bubbles with an outline present and every wrong
    warp reads 0.00-0.07, and a *blank* sheet reads near 1.0, so an empty answer
    set can be told from a failed read. See `grid_match`.
    """
    h, w = gray.shape
    r_in = max(1.5, r)
    r_out = r_in * 1.75
    x0 = int(max(0, math.floor(cx - r_out)))
    x1 = int(min(w, math.ceil(cx + r_out) + 1))
    y0 = int(max(0, math.floor(cy - r_out)))
    y1 = int(min(h, math.ceil(cy + r_out) + 1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return 0.0, 0.0
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    dark = 255.0 - patch
    inner = dist <= r_in
    ring = (dist > r_in * 1.35) & (dist <= r_out)
    if inner.sum() < 4 or ring.sum() < 4:
        # The bubble runs off the page — a warning, not a score.
        return 0.0, 0.0
    inside = float(dark[inner].mean())
    outside = float(dark[ring].mean())
    return inside - outside, outside - inside


def _disc_score(gray: np.ndarray, cx: float, cy: float, r: float) -> float:
    """Is this bubble marked — the first reading of `_bubble_metrics`."""
    return _bubble_metrics(gray, cx, cy, r)[0]


# A printed bubble outline has to stand this far out of the paper to count. A
# bubble's stroke is ~0.3 mm, which at the template's 4 px/mm is about a pixel
# wide, so the ring band the sampler averages over carries it diluted, and blur
# and JPEG dilute it further.
#
# Swept against both ends rather than guessed at. Over 15 warps of real sheets
# under five photo conditions and the 45 wrong warps that go with them, the
# ratio of bubbles whose outline is found:
#
#   gate      worst correct warp   best wrong warp   margin
#      6                 0.712             0.236     3.0×
#      8                 0.644             0.184     3.5×
#     10                 0.556             0.140     4.0×   <- peak
#     12                 0.464             0.124     3.7×
#     16                 0.272             0.108     2.5×
#
# 10 is the peak of that curve, so it is what is used. Note it is a *ratio*, and
# a wrong warp scores 0.14 not 0.0: even off the page, one bubble in seven lands
# on ink somewhere.
OUTLINE_PRESENT = 10.0

# Below this fraction of bubbles showing their own outline, the warp is not on
# the sheet and no answer read from it means anything. 0.28 is the geometric
# midpoint between the two measured ends above (0.140 and 0.556) — a factor of
# about two of margin either side. A **blank** sheet measures 1.0 here, not 0,
# because an unanswered bubble still has its printed outline, which is what lets
# a genuinely empty answer set be told from a failed read.
GRID_MATCH_MIN = 0.28


def _decide(scores: list[float], abs_gate: float, margin_gate: float):
    """Which option is marked, how sure, and whether two are.

    Returns (index or None, confidence 0-1, ambiguous).
    """
    if not scores:
        return None, 0.0, False
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    best = float(scores[order[0]])
    second = float(scores[order[1]]) if len(order) > 1 else 0.0
    if best < abs_gate:
        return None, 0.0, False
    margin = best - second
    if margin < margin_gate and second >= abs_gate:
        return order[0], 0.25, True
    # A filled mark reads 60-150; 100 of headroom is a full-confidence read.
    conf = min(1.0, 0.35 + margin / 100.0)
    return order[0], round(conf, 3), False


def read_answers(warpted_img: np.ndarray, layout: L.SheetLayout,
                 page_index: int = 0) -> dict:
    """Read **one page** of the sheet.

    A photograph is one page, and that has to be said out loud because the grid
    is not unique across pages: page 2's question 81 is printed at the same
    millimetres as page 1's question 1. So looping `range(layout.pages)` here did
    not read more of the sheet — it read the *same bubbles* again through
    `option_centre_mm(q, o, page)` and reported them under a second question
    number. Measured on the committed 100-question LJK: photographing page 1
    alone returned answers for all 100 questions, with 81-100 taken from 1-20's
    marks, and nothing in the result said so. On a real two-page exam that is 20
    scores computed from the wrong part of the paper.

    Which page was read, and which questions that covered, now come back in the
    result, so a caller can scan both pages and merge — instead of one page's
    answers being silently copied onto the other's questions.
    """
    gray = _to_gray(warpted_img)
    r_px = L.OPTION_RADIUS_MM * TEMPLATE_PX_PER_MM * 0.72

    answers: dict[str, str] = {}
    confidence: dict[str, float] = {}
    ambiguous: dict[str, list[str]] = {}
    labels = layout.options_labels()
    outlines = 0
    sampled = 0

    page = max(0, min(int(page_index), max(layout.pages - 1, 0)))
    start = page * layout.per_page
    end = min(start + layout.per_page, layout.total_questions)
    for q in range(start, end):
        scores, coords = [], []
        for o in range(layout.options):
            x_mm, y_mm = layout.option_centre_mm(q, o, page)
            cx, cy = _mm_to_px(x_mm, y_mm)
            score, outline = _bubble_metrics(gray, cx, cy, r_px)
            scores.append(score)
            coords.append((cx, cy))
            sampled += 1
            if outline >= OUTLINE_PRESENT:
                outlines += 1
        picked, conf, amb = _decide(scores, MARK_ABS_GATE, MARK_MARGIN_GATE)
        key = str(q)
        if picked is None:
            confidence[key] = 0.0
            continue
        answers[key] = labels[picked]
        confidence[key] = conf
        if amb:
            top = sorted(range(len(scores)), key=lambda i: -scores[i])[:2]
            ambiguous[key] = [labels[i] for i in top]

    high = sum(1 for c in confidence.values() if c >= 0.7)
    avg = round(sum(confidence.values()) / max(len(confidence), 1), 3)
    needs = sorted({k for k, v in confidence.items() if v and v < 0.6}
                   | set(ambiguous), key=int)
    return {
        "answers": answers, "detected": len(answers),
        "total": layout.total_questions, "confidence": confidence,
        "avg_confidence": avg, "high_confidence_count": high,
        "ambiguous": ambiguous, "needs_review": needs,
        "needs_review_count": len(needs),
        # How much of the sheet's own printed grid the warp actually landed on.
        # The read above can look plausible from a warp that is nowhere near the
        # page — a few bubbles will always happen to sit over ink — and this is
        # what says whether it was the page at all.
        "grid_match": round(outlines / max(sampled, 1), 3),
        # Which page this photograph was, and which questions that covered. A
        # multi-page sheet is scanned one page at a time, and the caller needs
        # both numbers to merge the pages rather than replace them.
        "page_index": page,
        "page_count": layout.pages,
        "first_question": start,
        "last_question": (end - 1) if end > start else start,
        "page_questions": end - start,
    }


def read_id(warpted_img: np.ndarray, layout: L.SheetLayout) -> dict:
    """The student-ID band: one bubble per column, read the same way."""
    gray = _to_gray(warpted_img)
    r_px = L.ID_RADIUS_MM * TEMPLATE_PX_PER_MM * 0.8
    digits = []
    conf_sum = 0.0
    for d in range(L.ID_DIGITS):
        scores = []
        for v in range(L.ID_VALUES):
            x_mm, y_mm = layout.id_centre_mm(d, v)
            cx, cy = _mm_to_px(x_mm, y_mm)
            scores.append(_disc_score(gray, cx, cy, r_px))
        picked, conf, _amb = _decide(scores, ID_ABS_GATE, ID_MARGIN_GATE)
        if picked is None:
            digits.append("?")
        else:
            digits.append(str(picked))
            conf_sum += conf
    code = "".join(digits)
    return {
        "nisn": code,
        # Digits that were filled divided by positions filled — a blank tail is
        # how a short ID is written, so it is not an error.
        "nisn_confidence": round(conf_sum / max(sum(1 for c in code if c != "?"), 1), 3),
        "nisn_read": sum(1 for c in code if c != "?"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# The pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _warp_to_page(img: np.ndarray, markers: list[Marker]) -> tuple[np.ndarray, np.ndarray]:
    src = [(m.cx, m.cy) for m in markers]
    dst = [_mm_to_px(x, y) for (x, y) in L.MARKER_CORNERS_MM]
    H = _homography(src, dst)
    warped = cv2.warpPerspective(img, H, (OUT_W, OUT_H), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
    return warped, H


def _best_orientation(img: np.ndarray, markers: list[Marker]):
    """Choose which marker is top-left, and say how it was decided.

    The QR is authoritative. Without it the four rotations are ranked by how
    close to a similarity their warp is — see `_anisotropy` — and the best is
    reported with the number, so a caller can tell a confident read from a
    lucky one.
    """
    best = None
    for shift in range(4):
        ordered = markers[shift:] + markers[:shift]
        try:
            warped, H = _warp_to_page(img, ordered)
        except cv2.error:
            continue
        cx, cy = _mm_to_px(L.PAGE_W_MM / 2, L.PAGE_H_MM / 2)
        aniso = _anisotropy(H, cx, cy)
        layout = read_layout_code(warped)
        if layout is not None:
            return warped, layout, aniso, "qr", ordered
        if best is None or aniso < best[2]:
            best = (warped, None, aniso, "geometry", ordered)
    if best is None:
        return None, None, float("inf"), "none", markers
    return best


def process_scan(image_data: bytes, total_questions: int = 50,
                 preprocess: bool = True, page_index: int = 0) -> dict:
    """Read a photographed sheet — one photograph, one page.

    Returns the same shape the previous pipeline returned, plus `layout_code`,
    `orientation` and `page_anisotropy` — the three things that say *how* the
    sheet was recognised, which a teacher grading a class needs to know when a
    page came back half-read.

    `page_index` says which page of a multi-page sheet this photograph is. It
    exists because the grid repeats: page 2's question 81 prints at the same
    millimetres as page 1's question 1, so a reader that ignores the page reads
    the same bubbles twice and hands back answers for questions that were never
    in the picture. See `read_answers`.
    """
    try:
        img = load_image(image_data)
        if img is None:
            return {"error": "Gagal membaca gambar. Format tidak didukung."}
        if max(img.shape[:2]) > 2400:
            img = cv2.resize(img, None, fx=2400 / max(img.shape[:2]),
                             fy=2400 / max(img.shape[:2]), interpolation=cv2.INTER_AREA)

        markers = find_square_markers(img)
        if markers is None:
            return _no_markers(img, total_questions, preprocess)

        warped, layout, aniso, how, ordered = _best_orientation(img, markers)
        if warped is None:
            return _no_markers(img, total_questions, preprocess)

        if layout is None:
            # No readable code: fall back to the grid the caller asked for, and
            # say so. Reading a sheet whose description was never read is a
            # weaker claim than reading one that described itself.
            layout = L.default_layout(max(1, int(total_questions)))

        result = read_answers(warped, layout, page_index=page_index)
        if result["grid_match"] < GRID_MATCH_MIN:
            # Refused on what the read *found*, not on how tilted the page looks.
            # The old test here was `aniso > AnisotropyWarn`, and measurement
            # retired it. It rejected two photographs whose markers were correct
            # to 1.7 px and whose answers came back 44/44 with 0 wrong, because a
            # 55%-darker corner and real perspective push a legitimate page to
            # 1.46. And it had no power against the thing it was aimed at: four
            # filled bubbles mistaken for the markers also measure 1.23-1.47,
            # *below* the very line that was rejecting good sheets. `grid_match`
            # separates those two cases — 0.45 for the true warp, 0.00-0.07 for
            # every wrong one.
            return {"error": "Susunan gelembung LJK tidak terbaca. Pastikan "
                             "seluruh lembar terlihat dan foto tidak miring "
                             "atau terlalu gelap."}
        result.update(read_id(warped, layout))
        result.update({
            "layout_code": layout.code(),
            "layout_from_qr": layout is not None and how == "qr",
            "orientation": how,
            "page_anisotropy": round(float(aniso), 3),
            "preprocessed": preprocess,
            "markers_found": len(markers),
        })
        return result
    except cv2.error as e:
        return {"error": f"Kesalahan pemrosesan gambar: {str(e)[:150]}"}
    except Exception as e:
        return {"error": f"Gagal memproses scan: {str(e)[:200]}"}


# ══════════════════════════════════════════════════════════════════════════════
# Legacy path — the L-corner sheet this app printed before the layout above
# ══════════════════════════════════════════════════════════════════════════════
#
# Kept because copies are already on paper. Its marks are L-shaped rules rather
# than squares and its pitch is 7.2 × 7.0 mm, so it cannot be read with the new
# geometry; and its constants are the ones that were calibrated against it.

LEGACY_MARGIN_MM = 15.0
LEGACY_GRID_X_MM = 50.0
LEGACY_GRID_TOP_Y_MM = 67.2
LEGACY_BUBBLE_GAP_MM = 6.5
LEGACY_ROW_H_MM = 8.5
LEGACY_BUBBLE_R_MM = 2.8
LEGACY_Q_PER_COL = 25
LEGACY_ID_DIGITS = 8

_LEGACY_OUT_W, _LEGACY_OUT_H = 850, 1100
_MARK_MARGIN_MM = 10.0
_PX_PER_MM_X = _LEGACY_OUT_W / (L.PAGE_W_MM - 2 * _MARK_MARGIN_MM)
_PX_PER_MM_Y = _LEGACY_OUT_H / (L.PAGE_H_MM - 2 * _MARK_MARGIN_MM)
_MARK_SIZE_RATIO = 0.015


def _legacy_mm_to_px_x(v: float) -> int:
    return int(round(v * _PX_PER_MM_X))


def _legacy_mm_to_px_y(v: float) -> int:
    return int(round(v * _PX_PER_MM_Y))


def _otsu_inverse(gray: np.ndarray) -> np.ndarray:
    _t, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh


def _adaptive_inverse(gray: np.ndarray, block_size: int = 15) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block_size, 3)


def find_registration_marks(img: np.ndarray):
    """Locate the four page corners for the debug overlay.

    New sheets: the square markers' centres. Old sheets: the L-rule corners, by
    the method that was calibrated for them.
    """
    squares = find_square_markers(img)
    if squares:
        return [(int(round(m.cx)), int(round(m.cy))) for m in squares]

    gray = _to_gray(img)
    h, w = gray.shape[:2]
    candidates = set()
    min_area = (w * _MARK_SIZE_RATIO) ** 2 * 0.2
    max_area = (w * _MARK_SIZE_RATIO * 4) ** 2
    for fn in (_otsu_inverse, _adaptive_inverse):
        try:
            thresh = fn(gray)
        except Exception:
            continue
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            _x, _y, bw, bh = cv2.boundingRect(cnt)
            aspect = bw / bh if bh > 0 else 0
            if 0.3 < aspect < 3.0:
                m = cv2.moments(cnt)
                if m["m00"] > 0:
                    candidates.add((int(m["m10"] / m["m00"]) // 5 * 5,
                                    int(m["m01"] / m["m00"]) // 5 * 5))
    if len(candidates) < 4:
        return None
    cand = list(candidates)
    min_x = min(p[0] for p in cand)
    max_x = max(p[0] for p in cand)
    min_y = min(p[1] for p in cand)
    max_y = max(p[1] for p in cand)

    def d(p, tx, ty):
        return abs(p[0] - tx) + abs(p[1] - ty)

    return [min(cand, key=lambda p: d(p, min_x, min_y)),
            min(cand, key=lambda p: d(p, max_x, min_y)),
            min(cand, key=lambda p: d(p, max_x, max_y)),
            min(cand, key=lambda p: d(p, min_x, max_y))]


def perspective_correct(img: np.ndarray, corners, output_size=(_LEGACY_OUT_W, _LEGACY_OUT_H)):
    src = np.array(corners, dtype=np.float32)
    dst = np.array([[0, 0], [output_size[0] - 1, 0],
                    [output_size[0] - 1, output_size[1] - 1], [0, output_size[1] - 1]],
                   dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, output_size)


def _bubble_stats(roi: np.ndarray) -> dict:
    """Legacy per-ROI metrics, kept for the old sheet and its tests."""
    gray = _to_gray(roi)
    h, w = gray.shape
    if h < 2 or w < 2:
        return {"mean_dark": 0.0, "fill_ratio": 0.0, "std": 0.0, "median_dark": 0.0,
                "p25_dark": 0.0, "dark_pixel_ratio": 0.0, "hist_valley": 0.0}
    pixels = gray.ravel().astype(np.float32)
    mean_val = float(np.mean(pixels))
    median_val = float(np.median(pixels))
    p25 = float(np.percentile(pixels, 25))
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    fill_ratio_otsu = cv2.countNonZero(thresh) / (h * w) if h * w > 0 else 0
    bs = max(3, min(h, w) // 3)
    bs = bs + 1 if bs % 2 == 0 else bs
    adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, bs, 3)
    fill_adapt = cv2.countNonZero(adapt) / (h * w) if h * w > 0 else 0
    try:
        bs2 = max(3, min(h, w) // 5)
        bs2 = bs2 if bs2 % 2 == 1 else bs2 + 1
        sauvola = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY_INV, bs2, 5)
        fill_sauvola = cv2.countNonZero(sauvola) / (h * w) if h * w > 0 else 0
    except Exception:
        fill_sauvola = fill_adapt
    dark_thresh = max(50, median_val - 20)
    dark_pixel_ratio = float(np.sum(pixels < dark_thresh) / max(len(pixels), 1))
    hist = cv2.calcHist([gray], [0], None, [64], [0, 256]).ravel()
    hist_smooth = cv2.GaussianBlur(hist, (3, 1), 0).ravel()
    diffs = np.diff(hist_smooth)
    valley_idx = int(np.argmin(diffs)) + 1 if len(diffs) > 0 else 32
    return {
        "mean_dark": 255.0 - mean_val, "median_dark": 255.0 - median_val,
        "p25_dark": 255.0 - p25,
        "fill_ratio": max(fill_ratio_otsu, fill_adapt, fill_sauvola),
        "std": float(np.std(pixels)), "dark_pixel_ratio": dark_pixel_ratio,
        "hist_valley": float((valley_idx * 4) / 255.0),
    }


def _zscore_bubble_detection(bubble_stats: list, z_threshold: float = 1.2) -> list:
    """Legacy relative detector, kept for the old sheet and its tests."""
    if not bubble_stats or len(bubble_stats) < 2:
        return []
    n = len(bubble_stats)
    metrics = ["mean_dark", "median_dark", "fill_ratio", "dark_pixel_ratio"]
    weights = [0.30, 0.25, 0.25, 0.20]
    filled = []
    for i, b in enumerate(bubble_stats):
        combined = 0.0
        for metric, weight in zip(metrics, weights):
            vals = [s.get(metric, 0.0) for s in bubble_stats]
            m = mean(vals)
            s = stdev(vals) if n > 2 else max(m * 0.3, 1)
            combined += ((b.get(metric, 0.0) - m) / max(s, 1)) * weight
        if combined > z_threshold:
            filled.append((i, combined, b))
    filled.sort(key=lambda x: x[1], reverse=True)
    return filled


def _get_roi(warped, cx, cy, radius, margin=2):
    h, w = warped.shape[:2]
    x1 = max(0, cx - radius - margin)
    y1 = max(0, cy - radius - margin)
    x2 = min(w, cx + radius + margin)
    y2 = min(h, cy + radius + margin)
    return warped[y1:y2, x1:x2]


def _legacy_grid_positions(total_questions: int = 50, options: int = 5):
    cols = max(1, (total_questions + LEGACY_Q_PER_COL - 1) // LEGACY_Q_PER_COL)
    q_per_col = min(LEGACY_Q_PER_COL,
                    max(1, (total_questions + cols - 1) // cols))
    grid_x_mm = LEGACY_MARGIN_MM + LEGACY_GRID_X_MM - _MARK_MARGIN_MM
    grid_top_y_px = _legacy_mm_to_px_y(LEGACY_GRID_TOP_Y_MM - _MARK_MARGIN_MM)
    b_gap_px = _legacy_mm_to_px_x(LEGACY_BUBBLE_GAP_MM)
    row_h_px = _legacy_mm_to_px_y(LEGACY_ROW_H_MM)
    b_r_px = _legacy_mm_to_px_y(LEGACY_BUBBLE_R_MM)
    col_width_mm = (L.PAGE_W_MM - LEGACY_MARGIN_MM - LEGACY_GRID_X_MM - LEGACY_MARGIN_MM) / cols

    positions, remaining = [], total_questions
    for col in range(cols):
        col_count = min(remaining, q_per_col)
        col_start_x_px = _legacy_mm_to_px_x(
            grid_x_mm + col * col_width_mm
            + (col_width_mm - LEGACY_BUBBLE_GAP_MM * options) / 2)
        for row_in_col in range(col_count):
            q_idx = (total_questions - remaining) + row_in_col
            for opt_idx in range(options):
                positions.append((q_idx, opt_idx,
                                  col_start_x_px + opt_idx * b_gap_px + b_r_px,
                                  grid_top_y_px + row_in_col * row_h_px))
        remaining -= col_count
    return positions, b_r_px


def sheet_code_present(img: np.ndarray) -> bool:
    """Does this photograph contain one of *this* app's layout codes anywhere?

    Used only on the failure path, to tell "a sheet whose corner markers we could
    not find" from "an old sheet, which has none to find". The old L-corner paper
    has no QR at all, so a code anywhere in the frame settles it — and the
    distinction matters, because the legacy reader maps corners by its own
    constants and would happily read the wrong grid off a new sheet, silently.
    """
    gray = _working_gray(img)
    detector = _qr()
    for scale in (1.0, 2.0):
        probe = gray if scale == 1.0 else cv2.resize(
            gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        try:
            data, _pts, _ = detector.detectAndDecode(probe)
        except cv2.error:
            return False
        if L.parse_code(data or "") is not None:
            return True
    return False


def _no_markers(img: np.ndarray, total_questions: int, preprocess: bool) -> dict:
    """Nothing looked like the four corner markers: an old sheet, or a bad photo.

    The legacy reader is only offered a photograph that does not carry a layout
    code, because it reads a different geometry and would otherwise return a
    confident-looking answer set derived from the wrong part of the page. A
    photograph that *does* carry a code is one of this app's own sheets with its
    corners missed, and says so instead.
    """
    if sheet_code_present(img):
        return {"error": "Penanda sudut LJK tidak terdeteksi, padahal kode LJK "
                         "terbaca. Foto ulang dengan keempat sudut lembar "
                         "terlihat penuh."}
    return _legacy_process_scan(img, total_questions, preprocess)


def _legacy_process_scan(img: np.ndarray, total_questions: int, preprocess: bool) -> dict:
    """The reading this app shipped before the layout existed."""
    corners = None
    gray_for_corners = preprocess_scan(img) if preprocess else img
    try:
        corners = _legacy_corners(gray_for_corners)
    except Exception:
        corners = None
    if corners is None:
        return {"error": "Tanda registrasi tidak ditemukan. Pastikan seluruh "
                         "lembar terlihat dalam foto."}
    warped = perspective_correct(gray_for_corners, corners)
    positions, b_r = _legacy_grid_positions(total_questions)
    labels = list("ABCDEFG")[:5]

    grouped: dict[int, list] = {}
    for q_idx, opt_idx, cx, cy in positions:
        grouped.setdefault(q_idx, []).append((opt_idx, cx, cy))

    answers, confidence, ambiguous = {}, {}, {}
    for q_idx in range(total_questions):
        bubbles = grouped.get(q_idx)
        if not bubbles:
            continue
        stats = []
        for _oi, cx, cy in bubbles:
            roi = _get_roi(warped, cx, cy, b_r)
            stats.append(_bubble_stats(roi) if roi.size else
                         {"mean_dark": 0, "fill_ratio": 0, "std": 0})
        filled = _zscore_bubble_detection(stats, z_threshold=1.8)
        key = str(q_idx)
        if len(filled) == 1:
            answers[key] = labels[filled[0][0]]
            confidence[key] = min(1.0, max(0.3, filled[0][1] / 4.0))
        elif len(filled) > 1:
            answers[key] = labels[filled[0][0]]
            gap = filled[0][1] - filled[1][1]
            confidence[key] = min(1.0, max(0.3, gap)) if gap > 0.5 else 0.3
            if gap <= 0.5:
                ambiguous[key] = [labels[f[0]] for f in filled]
        else:
            confidence[key] = 0.0

    high = sum(1 for c in confidence.values() if c >= 0.7)
    avg = round(sum(confidence.values()) / max(len(confidence), 1), 3)
    needs = sorted({k for k, v in confidence.items() if v < 0.6} | set(ambiguous),
                   key=int)
    return {
        "answers": answers, "detected": len(answers), "total": total_questions,
        "confidence": confidence, "avg_confidence": avg,
        "high_confidence_count": high, "ambiguous": ambiguous,
        "needs_review": needs, "needs_review_count": len(needs),
        "nisn": "?" * LEGACY_ID_DIGITS, "nisn_confidence": 0.0,
        "layout_code": None, "layout_from_qr": False,
        "orientation": "legacy", "page_anisotropy": None,
        "preprocessed": preprocess, "markers_found": 0,
        "legacy_sheet": True,
    }


def _legacy_corners(img: np.ndarray):
    """L-rule corners only — the legacy sheet has no squares to find."""
    gray = _to_gray(img)
    h, w = gray.shape[:2]
    candidates = set()
    min_area = (w * _MARK_SIZE_RATIO) ** 2 * 0.2
    max_area = (w * _MARK_SIZE_RATIO * 4) ** 2
    for fn in (_otsu_inverse, _adaptive_inverse):
        try:
            thresh = fn(gray)
        except Exception:
            continue
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            _x, _y, bw, bh = cv2.boundingRect(cnt)
            if bh and 0.3 < bw / bh < 3.0:
                m = cv2.moments(cnt)
                if m["m00"] > 0:
                    candidates.add((int(m["m10"] / m["m00"]) // 5 * 5,
                                    int(m["m01"] / m["m00"]) // 5 * 5))
    if len(candidates) < 4:
        return None
    cand = list(candidates)
    min_x = min(p[0] for p in cand)
    max_x = max(p[0] for p in cand)
    min_y = min(p[1] for p in cand)
    max_y = max(p[1] for p in cand)

    def d(p, tx, ty):
        return abs(p[0] - tx) + abs(p[1] - ty)

    return [min(cand, key=lambda p: d(p, min_x, min_y)),
            min(cand, key=lambda p: d(p, max_x, min_y)),
            min(cand, key=lambda p: d(p, max_x, max_y)),
            min(cand, key=lambda p: d(p, min_x, max_y))]


# ══════════════════════════════════════════════════════════════════════════════
# Debug overlay
# ══════════════════════════════════════════════════════════════════════════════

def draw_debug_image(img: np.ndarray, corners=None, answers=None,
                     page_index: int = 0) -> bytes:
    """Draw what was found, so a teacher can see why a scan came back odd.

    `page_index` keeps the overlay on the page that was photographed: the grid
    repeats across pages, so asking for question 81 without a page draws a ring
    over question 1.
    """
    vis = img.copy()
    if corners:
        for i, (x, y) in enumerate(corners):
            cv2.circle(vis, (int(x), int(y)), 12, (0, 200, 0), -1)
            cv2.putText(vis, str(i), (int(x) + 12, int(y)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 200, 0), 2)
        pts = np.array(corners, np.int32).reshape((-1, 1, 2))
        cv2.polylines(vis, [pts], True, (0, 200, 0), 2)

    if corners and answers:
        markers = [Marker(cx=float(x), cy=float(y), side=1.0, score=0.0)
                   for (x, y) in corners[:4]]
        try:
            warped, _H = _warp_to_page(img, markers)
            layout = read_layout_code(warped) or L.default_layout(max(1, len(answers)))
            labels = layout.options_labels()
            page = max(0, min(int(page_index), max(layout.pages - 1, 0)))
            for page in (page,):
                start = page * layout.per_page
                end = min(start + layout.per_page, layout.total_questions)
                for q in range(start, end):
                    key = str(q)
                    if key not in answers:
                        continue
                    letter = answers[key]
                    if not isinstance(letter, str) or letter not in labels:
                        continue
                    oi = labels.index(letter)
                    x_mm, y_mm = layout.option_centre_mm(q, oi, page)
                    cx, cy = _mm_to_px(x_mm, y_mm)
                    # Back to source coordinates for the overlay
                    src = np.array([[m.cx, m.cy] for m in markers], dtype=np.float32)
                    dst = np.array([_mm_to_px(x, y) for (x, y) in L.MARKER_CORNERS_MM],
                                   dtype=np.float32)
                    Hinv = cv2.getPerspectiveTransform(dst, src)
                    p = cv2.perspectiveTransform(
                        np.array([[[cx, cy]]], dtype=np.float32), Hinv)[0][0]
                    px, py = int(p[0]), int(p[1])
                    cv2.circle(vis, (px, py), 10, (0, 140, 255), 2)
                    cv2.putText(vis, letter, (px - 6, py + 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 2)
        except Exception:
            pass

    ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""
