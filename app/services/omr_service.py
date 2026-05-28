"""OMR (Optical Mark Recognition) service for bubble sheet scanning."""
import io
import cv2
import numpy as np
from typing import Optional


# LJK geometry constants (in mm, matching ljk_generator.py)
LJK_MARGIN = 15.0
LJK_GRID_X = 50.0       # grid offset from margin (mm)
LJK_GRID_TOP_Y = 67.2   # grid offset from page top (mm) = 70 - 2.8 (bubble center, not grid top)
LJK_BUBBLE_GAP = 6.5    # horizontal spacing between options (mm)
LJK_ROW_H = 8.5         # vertical spacing between questions (mm)
LJK_BUBBLE_R = 2.8      # bubble radius (mm)
LJK_Q_PER_COL = 25

# Corrected output size in pixels (matching perspective_correct)
OUT_W, OUT_H = 850, 1100
# Page size in mm (A4)
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0
# Registration mark center is 10mm from each page edge
MARK_MARGIN_MM = 10.0
# Pixels per mm (area between registration marks maps to warped output)
PX_PER_MM_X = OUT_W / (PAGE_W_MM - 2 * MARK_MARGIN_MM)  # ~4.474
PX_PER_MM_Y = OUT_H / (PAGE_H_MM - 2 * MARK_MARGIN_MM)  # ~3.971

MARK_SIZE_RATIO = 0.015


def _mm_to_px_x(mm_val: float) -> int:
    return int(round(mm_val * PX_PER_MM_X))

def _mm_to_px_y(mm_val: float) -> int:
    return int(round(mm_val * PX_PER_MM_Y))


def _get_grid_positions(total_questions: int = 50, options: int = 5):
    """Calculate all bubble center positions based on LJK geometry.

    Dynamically computes columns matching ljk_generator.py layout.
    Returns list of (q_idx, opt_idx, cx, cy) for every bubble, and bubble radius in px.
    """
    cols = max(1, (total_questions + LJK_Q_PER_COL - 1) // LJK_Q_PER_COL)
    q_per_col = min(LJK_Q_PER_COL, max(1, (total_questions + cols - 1) // cols))

    grid_x_mm = LJK_MARGIN + LJK_GRID_X - MARK_MARGIN_MM
    grid_top_y_mm = LJK_GRID_TOP_Y - MARK_MARGIN_MM
    grid_top_y_px = _mm_to_px_y(grid_top_y_mm)
    b_gap_px = _mm_to_px_x(LJK_BUBBLE_GAP)
    row_h_px = _mm_to_px_y(LJK_ROW_H)
    b_r_px = _mm_to_px_y(LJK_BUBBLE_R)

    col_width_mm = (PAGE_W_MM - LJK_MARGIN - LJK_GRID_X - LJK_MARGIN) / cols

    positions = []
    remaining = total_questions
    for col in range(cols):
        col_count = min(remaining, q_per_col)
        col_start_x_mm = grid_x_mm + col * col_width_mm + (col_width_mm - LJK_BUBBLE_GAP * options) / 2
        col_start_x_px = _mm_to_px_x(col_start_x_mm)

        for row_in_col in range(col_count):
            q_idx = (total_questions - remaining) + row_in_col
            for opt_idx in range(options):
                cx = col_start_x_px + opt_idx * b_gap_px + _mm_to_px_x(LJK_BUBBLE_R)
                cy = grid_top_y_px + row_in_col * row_h_px  # LJK_GRID_TOP_Y already includes bubble radius offset
                positions.append((q_idx, opt_idx, cx, cy))

        remaining -= col_count

    return positions, b_r_px


def load_image(image_data: bytes) -> Optional[np.ndarray]:
    """Load image from bytes, return as color."""
    arr = np.frombuffer(image_data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def find_registration_marks(img: np.ndarray):
    """Find the 4+ corner registration marks in the bubble sheet image.

    Returns list of (x, y) corner points sorted: top-left, top-right, bottom-right, bottom-left.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    h, w = img.shape[:2]
    mark_area_min = (w * MARK_SIZE_RATIO) ** 2 * 0.3
    mark_area_max = (w * MARK_SIZE_RATIO * 3) ** 2

    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < mark_area_min or area > mark_area_max:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        aspect = bw / bh if bh > 0 else 0
        if 0.5 < aspect < 2.0:
            M = cv2.moments(cnt)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                candidates.append((cx, cy))

    if len(candidates) < 4:
        return None

    # Find the 4 extreme corners from candidates (handles 5th mark at top-center)
    min_x = min(p[0] for p in candidates)
    max_x = max(p[0] for p in candidates)
    min_y = min(p[1] for p in candidates)
    max_y = max(p[1] for p in candidates)

    def dist_to(p, target_x, target_y):
        return abs(p[0] - target_x) + abs(p[1] - target_y)

    top_left = min(candidates, key=lambda p: dist_to(p, min_x, min_y))
    top_right = min(candidates, key=lambda p: dist_to(p, max_x, min_y))
    bottom_right = min(candidates, key=lambda p: dist_to(p, max_x, max_y))
    bottom_left = min(candidates, key=lambda p: dist_to(p, min_x, max_y))

    return [top_left, top_right, bottom_right, bottom_left]


def perspective_correct(img: np.ndarray, corners, output_size=(OUT_W, OUT_H)):
    """Apply perspective transform to get a top-down view of the sheet."""
    src = np.array(corners, dtype=np.float32)
    dst = np.array([
        [0, 0],
        [output_size[0] - 1, 0],
        [output_size[0] - 1, output_size[1] - 1],
        [0, output_size[1] - 1],
    ], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, M, output_size)
    return warped


def _bubble_filled(roi: np.ndarray, threshold: float = 0.40) -> bool:
    """Check if a bubble region is filled (dark enough)."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    filled_ratio = cv2.countNonZero(thresh) / (roi.shape[0] * roi.shape[1])
    return filled_ratio > threshold


def detect_answers(
    warped: np.ndarray,
    total_questions: int = 50,
    options: int = 5,
) -> dict:
    """Detect filled bubbles using known LJK geometry.

    Uses pre-calculated bubble positions from the LJK generator layout
    instead of HoughCircles, for much more reliable detection.
    """
    h, w = warped.shape[:2]
    opt_labels = ["A", "B", "C", "D", "E", "F", "G"][:options]

    positions, b_r = _get_grid_positions(total_questions, options)

    # Group positions by question
    questions = {}
    for q_idx, opt_idx, cx, cy in positions:
        if q_idx not in questions:
            questions[q_idx] = []
        questions[q_idx].append((opt_idx, cx, cy))

    answers = {}
    confidence_map = {}

    for q_idx in range(total_questions):
        if q_idx not in questions:
            continue

        filled_opts = []
        for opt_idx, cx, cy in questions[q_idx]:
            x1 = max(0, cx - b_r - 2)
            y1 = max(0, cy - b_r - 2)
            x2 = min(w, cx + b_r + 2)
            y2 = min(h, cy + b_r + 2)
            roi = warped[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            if _bubble_filled(roi):
                filled_opts.append(opt_idx)

        if len(filled_opts) == 1:
            answers[str(q_idx)] = opt_labels[filled_opts[0]]
            confidence_map[str(q_idx)] = 1.0
        elif len(filled_opts) > 1:
            # Multiple filled: pick darkest one
            best_opt = filled_opts[0]
            best_dark = 0
            for opt_idx in filled_opts:
                _, _, cx, cy = questions[q_idx][opt_idx]
                x1 = max(0, cx - b_r)
                y1 = max(0, cy - b_r)
                x2 = min(w, cx + b_r)
                y2 = min(h, cy + b_r)
                roi = warped[y1:y2, x1:x2]
                if roi.size == 0:
                    continue
                gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                mean_dark = 255 - np.mean(gray_roi)
                if mean_dark > best_dark:
                    best_dark = mean_dark
                    best_opt = opt_idx
            answers[str(q_idx)] = opt_labels[best_opt]

    return {
        "answers": answers,
        "detected": len(answers),
        "total": total_questions,
    }


def process_scan(image_data: bytes, total_questions: int = 50) -> dict:
    """Full OMR pipeline: load -> find marks -> correct -> detect answers."""
    img = load_image(image_data)
    if img is None:
        return {"error": "Could not decode image"}

    corners = find_registration_marks(img)
    if corners is None:
        return {"error": "Could not find registration marks. Ensure the sheet is fully visible with corner marks."}

    warped = perspective_correct(img, corners)
    return detect_answers(warped, total_questions=total_questions)


def draw_debug_image(img: np.ndarray, corners=None, answers=None) -> bytes:
    """Draw debug visualization on the image."""
    vis = img.copy()
    if corners:
        for i, (x, y) in enumerate(corners):
            cv2.circle(vis, (x, y), 10, (0, 255, 0), -1)
            cv2.putText(vis, str(i), (x + 10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        pts = np.array(corners, np.int32).reshape((-1, 1, 2))
        cv2.polylines(vis, [pts], True, (0, 255, 0), 2)

    _, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()
