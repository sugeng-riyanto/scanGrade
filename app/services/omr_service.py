"""OMR (Optical Mark Recognition) service for bubble sheet scanning."""
import io
import cv2
import numpy as np
from typing import Optional, Tuple


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


# ── Preprocessing pipeline ──────────────────────────────

def deskew(img: np.ndarray) -> np.ndarray:
    """Auto-deskew a scanned image using cv2.minAreaRect."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 10:
        return img

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return rotated


def adaptive_normalize(img: np.ndarray) -> np.ndarray:
    """Apply adaptive thresholding + CLAHE for consistent lighting."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # CLAHE for contrast normalization
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Adaptive threshold to handle dark/light photos
    block_size = max(3, (min(img.shape[:2]) // 10) | 1)  # odd number
    binary = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, block_size, 5,
    )

    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def denoise(img: np.ndarray) -> np.ndarray:
    """Remove noise via morphological operations."""
    kernel = np.ones((2, 2), np.uint8)
    opened = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel, iterations=1)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
    return cv2.medianBlur(closed, 3)


def preprocess_scan(img: np.ndarray) -> np.ndarray:
    """Full preprocessing pipeline: deskew → adaptive normalize → denoise.

    Returns preprocessed image in BGR format.
    """
    img = deskew(img)
    img = adaptive_normalize(img)
    img = denoise(img)
    return img


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


def _bubble_filled(roi: np.ndarray, threshold: float = 0.40) -> tuple:
    """Check if a bubble region is filled (dark enough).

    Returns (is_filled: bool, fill_ratio: float, mean_darkness: float).
    Uses both OTSU and adaptive thresholding for higher accuracy.
    """
    if roi.size == 0:
        return False, 0.0, 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi
    h, w = gray.shape
    if h == 0 or w == 0:
        return False, 0.0, 0.0

    mean_darkness = 255 - np.mean(gray)

    _, thresh_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    filled_otsu = cv2.countNonZero(thresh_otsu) / (h * w)

    block_size = max(3, min(h, w) // 2)
    if block_size % 2 == 0:
        block_size += 1
    thresh_adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY_INV, block_size, 5)
    filled_adapt = cv2.countNonZero(thresh_adapt) / (h * w)

    fill_ratio = max(filled_otsu, filled_adapt)
    is_filled = fill_ratio > threshold
    return is_filled, fill_ratio, mean_darkness


def _get_nisn_positions():
    """Compute NISN bubble centers (10 digits × 10 options = 0-9).
    Matches answer_sheet_generator.py _draw_student_id_grid layout.
    Returns: list of (digit_idx, option_value, cx, cy), and bubble radius in px.
    """
    # NISN grid position (from answer_sheet_generator.py)
    nisn_x_mm = LJK_MARGIN + 3.0  # from left margin
    nisn_y_mm = LJK_GRID_TOP_Y - 10.0  # above answer grid

    # Convert to warped image pixel coords (relative to registration marks)
    nisn_x = _mm_to_px_x(nisn_x_mm - MARK_MARGIN_MM)
    nisn_y = _mm_to_px_y(nisn_y_mm - MARK_MARGIN_MM)

    id_col_w = 4.8  # mm (ID_COL_W)
    id_gap_x = 0.6  # mm (ID_GAP_X)
    id_gap_y = 4.0  # mm (ID_GAP_Y)
    id_circle_r = 1.6  # mm (ID_CIRCLE_R)

    b_r_px = _mm_to_px_y(id_circle_r)
    col_step_px = _mm_to_px_x(id_col_w + id_gap_x)
    row_step_px = _mm_to_px_y(id_gap_y)

    positions = []
    for digit in range(10):  # 10 digit positions
        for opt in range(10):  # options 0-9
            cx = nisn_x + digit * col_step_px + _mm_to_px_x(id_circle_r)
            cy = nisn_y + opt * row_step_px  # 0 at top, 9 at bottom
            positions.append((digit, opt, cx, cy))

    return positions, b_r_px


def detect_nisn(warped: np.ndarray) -> dict:
    """Detect NISN from 10-digit bubble grid. Returns dict {digit_idx: option_value}."""
    positions, b_r = _get_nisn_positions()
    h, w = warped.shape[:2]

    digits = {}
    for digit_idx, opt_val, cx, cy in positions:
        x1 = max(0, cx - b_r - 2)
        y1 = max(0, cy - b_r - 2)
        x2 = min(w, cx + b_r + 2)
        y2 = min(h, cy + b_r + 2)
        roi = warped[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        filled, ratio, dark = _bubble_filled(roi)
        if filled:
            # Store darkest filled option per digit
            if digit_idx not in digits or dark > digits[digit_idx].get("dark", 0):
                digits[digit_idx] = {"value": str(opt_val), "dark": dark, "ratio": ratio}

    # Build NISN string
    nisn_parts = []
    confidence_sum = 0
    for i in range(10):
        if i in digits:
            nisn_parts.append(digits[i]["value"])
            confidence_sum += min(1.0, digits[i].get("ratio", 0) / 0.6)
        else:
            nisn_parts.append("?")

    nisn = "".join(nisn_parts)
    avg_conf = confidence_sum / 10 if confidence_sum > 0 else 0

    return {
        "nisn": nisn,
        "nisn_confidence": round(avg_conf, 3),
        "nisn_digits": {str(k): v["value"] for k, v in digits.items()},
    }


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
    ambiguous = {}

    for q_idx in range(total_questions):
        if q_idx not in questions:
            continue

        filled_opts = []
        opt_fill_ratios = {}
        opt_darkness = {}
        for opt_idx, cx, cy in questions[q_idx]:
            x1 = max(0, cx - b_r - 2)
            y1 = max(0, cy - b_r - 2)
            x2 = min(w, cx + b_r + 2)
            y2 = min(h, cy + b_r + 2)
            roi = warped[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            is_filled, fill_ratio, mean_dark = _bubble_filled(roi)
            opt_fill_ratios[opt_idx] = fill_ratio
            opt_darkness[opt_idx] = mean_dark
            if is_filled:
                filled_opts.append(opt_idx)

        if len(filled_opts) == 1:
            answers[str(q_idx)] = opt_labels[filled_opts[0]]
            confidence_map[str(q_idx)] = min(1.0, opt_fill_ratios.get(filled_opts[0], 0.5) / 0.6)
        elif len(filled_opts) > 1:
            best_opt = max(filled_opts, key=lambda o: opt_darkness.get(o, 0))
            best_dark = opt_darkness.get(best_opt, 0)
            second_dark = sorted([opt_darkness.get(o, 0) for o in filled_opts], reverse=True)
            gap = best_dark - (second_dark[1] if len(second_dark) > 1 else 0)
            if gap > 15:
                answers[str(q_idx)] = opt_labels[best_opt]
                confidence_map[str(q_idx)] = min(1.0, gap / 80)
            else:
                ambiguous[str(q_idx)] = [opt_labels[o] for o in filled_opts]
                answers[str(q_idx)] = opt_labels[best_opt]
                confidence_map[str(q_idx)] = max(0.3, gap / 80)
        else:
            unfilled_dark = [(opt_darkness.get(o, 0), o) for o in range(len(questions[q_idx]))]
            unfilled_dark.sort(reverse=True)
            if unfilled_dark and unfilled_dark[0][0] > 30:
                best_d, best_o = unfilled_dark[0]
                answers[str(q_idx)] = opt_labels[best_o]
                confidence_map[str(q_idx)] = max(0.1, best_d / 120)
            else:
                confidence_map[str(q_idx)] = 0.0

    high_conf = sum(1 for c in confidence_map.values() if c >= 0.7)
    avg_conf = round(sum(confidence_map.values()) / max(len(confidence_map), 1), 3)

    # Mark answers needing human review (confidence < 0.6 or ambiguous)
    needs_review = {
        k for k, v in confidence_map.items()
        if v < 0.6 or k in ambiguous
    }

    return {
        "answers": answers,
        "detected": len(answers),
        "total": total_questions,
        "confidence": confidence_map,
        "avg_confidence": avg_conf,
        "high_confidence_count": high_conf,
        "ambiguous": ambiguous,
        "needs_review": sorted(needs_review, key=int),
        "needs_review_count": len(needs_review),
    }


def process_scan(image_data: bytes, total_questions: int = 50, preprocess: bool = True) -> dict:
    """Full OMR pipeline: load -> [preprocess] -> find marks -> correct -> detect answers.

    Args:
        image_data: Raw image bytes.
        total_questions: Number of MCQ questions on the sheet.
        preprocess: Whether to apply deskew/CLAHE/denoise before detection.

    Returns:
        dict with "answers", "confidence", "needs_review", or {"error": ...} on failure.
    """
    try:
        img = load_image(image_data)
        if img is None:
            return {"error": "Gagal membaca gambar. Format tidak didukung."}

        # Optional preprocessing
        if preprocess:
            img = preprocess_scan(img)

        corners = find_registration_marks(img)
        if corners is None:
            return {"error": "Tanda registrasi tidak ditemukan. Pastikan seluruh lembar terlihat dalam foto."}

        warped = perspective_correct(img, corners)
        result = detect_answers(warped, total_questions=total_questions)
        # Detect NISN from answer sheet
        nisn_result = detect_nisn(warped)
        result.update(nisn_result)
        result["preprocessed"] = preprocess
        return result

    except cv2.error as e:
        return {"error": f"Kesalahan pemrosesan gambar: {str(e)[:150]}"}
    except Exception as e:
        return {"error": f"Gagal memproses scan: {str(e)[:200]}"}


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
