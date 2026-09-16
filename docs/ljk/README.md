# Format LJK / Answer Sheet Formats

Printable answer sheets for the ScanGrade OMR scanner. Print one and photocopy it
for the class — the app reads a **photograph** of the filled sheet, no scanner
hardware needed.

| File | Questions | Options | Marks | Pages |
|---|---|---|---|---|
| `LJK-quiz-20-soal.pdf` | 20 | 5 (A–E) | circle | 1 |
| `LJK-latihan-40-soal-4-opsi.pdf` | 40 | 4 (A–D) | square | 1 |
| `LJK-uts-50-soal.pdf` | 50 | 5 (A–E) | circle | 1 |
| `LJK-uas-100-soal.pdf` | 100 | 5 (A–E) | circle | 2 (80 + 20) |
| `LJK-uas-160-soal-2-halaman.pdf` | 160 | 5 (A–E) | circle | 2 (80 + 80) |

Generate your own for any question count in the app: **Alat → Buat LJK**
(`/tools/generate-answer-sheet`). The generator, the preview there and the
scanner all read one description of the sheet's geometry
(`app/services/omr_layout.py`), so what you print is what gets read.

## Printing

- **Paper:** A4, 100% scale. Do **not** use "fit to page" or "shrink to fit" —
  the corner markers must land where the layout says they are.
- **Colour:** any. Greyscale and photocopies read fine; the marks are solid
  squares precisely so that blur and a photocopier cannot erode them.
- **Both sides are not supported.** Print one sheet per page.
- **Do not cut the corners off.** Four solid black squares near the corners are
  how the scanner finds the page; a photocopier or a guillotine that trims the
  margin removes the part the reader needs. Each marker keeps ~4 mm of bare
  paper (a quiet zone) that must stay empty.

## Filling

- Use a dark pencil or pen. Fill the bubble solidly.
- A blank answer is information — leave it blank if the student did not answer.
- The **NISN band** at the bottom identifies the student. Fill one bubble per
  column, from the left; a short number leaves the trailing columns empty (that
  is the standard OMR convention, not an error).
- If the same question is filled twice the reader reports it under
  `needs_review` instead of guessing.
- Scanning a sheet twice, or a mark dark enough to leak through from the reverse
  of a badly printed page, is not a supported case — print single-sided.

## Photographing

The reader is built for a hand-held phone photo, not a flatbed scan. It corrects
perspective and rotation and tolerates a lighting gradient, moderate blur and
JPEG. What it needs is the whole sheet in frame.

- Lay the sheet flat on a contrasting surface.
- Fill the frame with the sheet, all four corner squares visible.
- Shoot from above, roughly straight on; a moderate tilt is fine.
- Avoid a hard shadow across the page and avoid glare where a bubble is.
- **A corner outside the picture is refused**, not guessed — the result says the
  markers were not found. Refusing is deliberate: a wrong mark is invisible in a
  score, a refusal is not.

## Multi-page sheets

Above 80 questions the sheet is printed as more than one page
(`omr_layout` caps the grid at 4 columns × 20 rows). Each page is a separate
photograph, and the app asks **which page** you are photographing — because the
grid repeats: page 2's question 81 is printed at the same place on the paper as
page 1's question 1. Without the page number the reader would return answers for
questions that are not in the picture, and nothing about the result would look
wrong.

Saved pages **merge**: photographing and saving page 2 adds questions 81–160 and
leaves page 1's answers alone.

## Are these files known to work?

Yes, and it is checked rather than asserted:
`tests/unit/test_omr_sheet_scan.py::TestTheCommittedLJKFormatsScanBack` reads
**these files** — rasterised, filled with a known answer set, tilted, JPEG'd and
read back — and fails if any question comes back wrong, missing, or invented, or
if the NISN band stops reading. Every page of every format above passes with zero
errors, so a format added here that does not scan fails the build.
