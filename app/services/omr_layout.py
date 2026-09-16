"""The answer sheet's geometry, defined exactly once.

Before this module the printer and the scanner each carried their own idea of the
sheet, and the two disagreed. `answer_sheet_generator.py` drew bubbles 7.2 mm
apart inside a 12 mm margin with L-shaped corner rules; `omr_service.py` read
them 6.5 mm apart from constants its comments called "empirically calibrated",
because it corrected perspective by mapping whatever corners it found onto the
*image* edges — so the transform was never the page's, and every offset had to be
hand-tuned against the error it introduced. A sheet printed by this app was not
the sheet the scanner was reading.

Here the page is described in millimetres, and both sides read that description:

* the generator draws at these coordinates;
* the scanner warps the photograph onto the same coordinates and samples there.

A millimetre is now the only unit, and there is no second set of numbers to drift.

**The sheet describes itself.** The layout's geometry is a pure function of
`total_questions`, `options`, `mark_type` and `columns`, and the page constants
are frozen under a layout id. `code()` packs exactly that into ~20 bytes, which
the generator prints as a QR code; `parse_code()` reads it back. So a scanner
does not have to already know how a sheet was made in order to read it — which is
what makes it possible to change the sheet later without stranding the copies
already on a school's photocopier.

Nothing here imports OpenCV or reportlab: this is arithmetic, so it can be used
by the printer, the scanner and a test without dragging in either.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterator

# ── Page ──────────────────────────────────────────────────────────────────────
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

# ── Registration marks ───────────────────────────────────────────────────────
# Four solid squares, one near each corner. Solid squares, not the L-shaped
# corner rules they replace: a rule is two thin strokes whose enclosed area
# depends on how thick the pen looked at that distance, while a square is a
# filled region that survives blur, perspective and a phone's JPEG. The scanner
# finds their *centres*, and because the centres sit at fixed page coordinates
# they define the page's coordinate system rather than being fitted to it.
MARKER_SIZE_MM = 6.0          # side of the filled square
MARKER_INSET_MM = 9.0         # centre distance from the page edge
MARKER_MARGIN_MM = 16.0       # nothing else is printed inside this, so each
                              # marker keeps a quiet zone of ~4 mm of bare paper

# ── Content ──────────────────────────────────────────────────────────────────
CONTENT_MARGIN_MM = 16.0
CONTENT_RIGHT_MM = PAGE_W_MM - CONTENT_MARGIN_MM      # 194.0
CONTENT_BOTTOM_MM = PAGE_H_MM - CONTENT_MARGIN_MM     # 281.0

# ── Answer grid ──────────────────────────────────────────────────────────────
OPTION_RADIUS_MM = 2.7        # the printed circle/square half-size
OPTION_PITCH_MM = 7.0         # centre-to-centre across the five options
ROW_PITCH_MM = 8.0            # centre-to-centre down the question rows
ROWS_MAX_PER_COLUMN = 20      # with 4 columns that is 80 questions on one page
NUMBER_COL_MM = 8.0           # the "1." gutter to the left of the bubbles
GRID_FIRST_ROW_MM = 60.0      # centre of the first question row

# ── Student-ID band ──────────────────────────────────────────────────────────
# Ten digit positions across, ten values down: the student fills one bubble per
# position. It sits below the answer grid so the grid can use the full page
# width, which is worth more columns than the band's height costs.
ID_RADIUS_MM = 1.7
ID_COL_PITCH_MM = 4.6
ID_ROW_PITCH_MM = 3.6
ID_BAND_TOP_MM = 222.0        # centre of the band's label row
ID_DIGITS = 10                # positions printed
ID_VALUES = 10                # 0-9
ID_MAX_MM = 20.0              # the widest ID this sheet can carry (2 per position)

# ── Header ───────────────────────────────────────────────────────────────────
QR_SIZE_MM = 20.0
QR_TOP_MM = 18.0
HEADER_RULE_MM = 44.0         # a horizontal rule under the header block

# ── Footer ───────────────────────────────────────────────────────────────────
FOOTER_Y_MM = 270.0

SCHEMA = "SG1"
SUPPORTED_SCHEMAS = ("SG1",)
MARK_TYPES = ("C", "S")       # circle, square


# ── The four markers, in millimetres, from the page's top-left corner ────────
# Order is load-bearing: it is the order a perspective transform expects, so
# `warp_to_template` can map index i to `MARKER_CORNERS_MM[i]` without guessing.
def marker_centres_mm() -> list[tuple[float, float]]:
    """Marker centres as (x_mm, y_mm) in page space, beginning top-left."""
    i = MARKER_INSET_MM
    return [
        (i, i),
        (PAGE_W_MM - i, i),
        (PAGE_W_MM - i, PAGE_H_MM - i),
        (i, PAGE_H_MM - i),
    ]


MARKER_CORNERS_MM = marker_centres_mm()


def marker_side_mm() -> float:
    return MARKER_SIZE_MM


def qr_box_mm() -> tuple[float, float, float, float]:
    """The QR's home on the page as (x0, y0, x1, y1), top-left origin.

    A page constant, not a layout one: the code has to be findable *before* the
    layout is known, which is the whole reason it is printed.
    """
    x1 = CONTENT_RIGHT_MM
    return (x1 - QR_SIZE_MM, QR_TOP_MM, x1, QR_TOP_MM + QR_SIZE_MM)


# ── The layout ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SheetLayout:
    """How one sheet was made, and therefore where everything on it is.

    `columns` is stored rather than derived because it is what the *sheet* has;
    a scanner that recomputed it from `total_questions` would disagree with a
    sheet printed before the rule changed.
    """

    total_questions: int
    options: int = 5
    mark_type: str = "C"
    columns: int = 0            # 0 → derive from total_questions
    rows: int = 0               # 0 → derive from total_questions / columns
    version: str = "A"

    def __post_init__(self) -> None:
        if self.total_questions < 1:
            raise ValueError("total_questions must be at least 1")
        if not 2 <= self.options <= 8:
            raise ValueError("options must be between 2 and 8")
        if self.mark_type not in MARK_TYPES:
            raise ValueError(f"mark_type must be one of {MARK_TYPES}")
        # Bounds, not a capacity check: a sheet with more questions than one
        # page holds simply gets more pages, so `columns × rows` is allowed to
        # be smaller than `total_questions`. What must not happen is a layout
        # claiming a grid the paper cannot carry — that is how a corrupt QR
        # turns into bubbles sampled off the edge of the page.
        if not 0 <= self.columns <= 4:
            raise ValueError("columns must be 0-4")
        if not 0 <= self.rows <= ROWS_MAX_PER_COLUMN:
            raise ValueError(f"rows must be 0-{ROWS_MAX_PER_COLUMN}")

    # ── derived shape ────────────────────────────────────────────────────────
    @property
    def n_columns(self) -> int:
        if self.columns:
            return self.columns
        return min(4, max(1, math.ceil(self.total_questions / ROWS_MAX_PER_COLUMN)))

    @property
    def n_rows(self) -> int:
        if self.rows:
            return self.rows
        # Capped, not free: `ROWS_MAX_PER_COLUMN` is where the grid would run
        # into the student-ID band. A sheet with more questions than one page
        # can print gets more pages, never taller rows — the alternative is a
        # grid drawn over the ID bubbles, which reads as answers nobody made.
        return min(ROWS_MAX_PER_COLUMN,
                   max(1, math.ceil(self.total_questions / self.n_columns)))

    @property
    def per_page(self) -> int:
        return self.n_columns * self.n_rows

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total_questions / self.per_page))

    def options_labels(self) -> list[str]:
        return list("ABCDEFGH"[: self.options])

    # ── geometry ─────────────────────────────────────────────────────────────
    @property
    def column_width_mm(self) -> float:
        return NUMBER_COL_MM + self.options * OPTION_PITCH_MM

    @property
    def grid_left_mm(self) -> float:
        """The left edge of the whole column block, centred on the page."""
        block = self.n_columns * self.column_width_mm
        return CONTENT_MARGIN_MM + (self.usable_width_mm - block) / 2.0

    @property
    def usable_width_mm(self) -> float:
        return CONTENT_RIGHT_MM - CONTENT_MARGIN_MM

    def column_left_mm(self, column: int) -> float:
        return self.grid_left_mm + column * self.column_width_mm

    def option_centre_mm(self, question_index: int, option_index: int,
                         page_index: int = 0) -> tuple[float, float]:
        """The printed centre of one bubble, in page millimetres.

        This is the function the printer draws from and the scanner samples at.
        There is deliberately no second implementation of it anywhere.
        """
        per_page = self.per_page
        slot = question_index - page_index * per_page
        if slot < 0 or slot >= per_page:
            raise ValueError("question is not on this page")

        col = slot // self.n_rows
        row = slot % self.n_rows
        col_left = self.column_left_mm(col)
        x = col_left + NUMBER_COL_MM + OPTION_RADIUS_MM + option_index * OPTION_PITCH_MM
        y = GRID_FIRST_ROW_MM + row * ROW_PITCH_MM
        return (x, y)

    def id_centre_mm(self, digit: int, value: int) -> tuple[float, float]:
        """The student-ID band: `digit` position, `value` 0-9."""
        if not 0 <= digit < ID_DIGITS:
            raise ValueError("digit position out of range")
        if not 0 <= value < ID_VALUES:
            raise ValueError("digit value out of range")
        x = CONTENT_MARGIN_MM + ID_RADIUS_MM + digit * ID_COL_PITCH_MM
        y = ID_BAND_TOP_MM + ID_ROW_PITCH_MM + value * ID_ROW_PITCH_MM
        return (x, y)

    def bubbles(self, page_index: int = 0) -> Iterator[tuple[int, int, float, float]]:
        """(question, option, x_mm, y_mm) for every bubble printed on a page."""
        start = page_index * self.per_page
        end = min(start + self.per_page, self.total_questions)
        for q in range(start, end):
            for o in range(self.options):
                x, y = self.option_centre_mm(q, o, page_index)
                yield (q, o, x, y)

    def id_bubbles(self) -> Iterator[tuple[int, int, float, float]]:
        for d in range(ID_DIGITS):
            for v in range(ID_VALUES):
                x, y = self.id_centre_mm(d, v)
                yield (d, v, x, y)

    # ── the code the sheet carries ───────────────────────────────────────────
    def code(self) -> str:
        """A short, self-describing string for the QR code.

        Kept to ~20 bytes on purpose: fewer bytes is a lower QR version, which
        is fewer, larger modules, which is a code a phone can still read from a
        photograph at an angle.
        """
        return ":".join([
            SCHEMA, str(self.total_questions), str(self.options),
            self.mark_type, str(self.n_columns), str(self.n_rows),
            self.version or "A",
        ])


def parse_code(text: str) -> SheetLayout | None:
    """Read a layout back out of a QR payload, or None if it is not ours.

    Tolerant by design — a scanner that cannot read the sheet's own description
    falls back to the legacy path, and returning None is how it says so.
    """
    if not text:
        return None
    text = text.strip()
    parts = text.split(":")
    # Exactly, not at least: the payload is written by us, so trailing bytes
    # mean this is not our code — a truncated read or a different scheme's
    # payload that happens to start the same way.
    if len(parts) != 7 or parts[0] not in SUPPORTED_SCHEMAS:
        return None
    try:
        _schema, q, opts, mark, cols, rows, version = parts
        layout = SheetLayout(
            total_questions=int(q), options=int(opts), mark_type=mark.upper(),
            columns=int(cols), rows=int(rows), version=version[:4] or "A",
        )
    except (ValueError, IndexError):
        return None
    if layout.n_columns * layout.n_rows > 4 * ROWS_MAX_PER_COLUMN:
        return None                                 # beyond one page's worth
    return layout


def default_layout(total_questions: int, **kwargs) -> SheetLayout:
    """A layout for a fresh sheet, with columns/rows chosen to fill the page."""
    columns = min(4, max(1, math.ceil(total_questions / ROWS_MAX_PER_COLUMN)))
    rows = min(ROWS_MAX_PER_COLUMN, math.ceil(total_questions / columns))
    return SheetLayout(total_questions=total_questions, columns=columns,
                       rows=rows, **kwargs)


# ── The legacy sheet ─────────────────────────────────────────────────────────
# The sheet this app printed before the layout above. Its marks are L-shaped
# rules rather than squares and its pitch is 7.2 × 7.0 mm, so it cannot be read
# from the new geometry — but copies are already on paper, so the scanner keeps
# a path for it. Nothing new is ever printed this way.
LEGACY_MARKER_CORNERS_MM = [(11.0, 11.0), (199.0, 11.0), (199.0, 286.0), (11.0, 286.0)]
LEGACY_MARKER_SHAPE = "L"
