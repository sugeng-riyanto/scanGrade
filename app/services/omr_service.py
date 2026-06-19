"""OMR (Optical Mark Recognition) service — ZipGrade-inspired pipeline.
Handles low-resolution cameras, uneven lighting, and tilted scans.
"""
import io
import cv2
import numpy as np
from typing import Optional, Tuple
from statistics import stdev, mean

# ── Geometry (empirically calibrated from camera scans) ──
# These values were derived from test scans of printed LJKs,
# NOT directly from ljk_generator.py dimensions. Perspective
# correction + lens distortion shifts coordinates slightly.
LJK_MARGIN = 15.0
LJK_GRID_X = 50.0
LJK_GRID_TOP_Y = 67.2    # calibrated (not 70 from ljk_generator)
LJK_BUBBLE_GAP = 6.5
LJK_ROW_H = 8.5
LJK_BUBBLE_R = 2.8
LJK_Q_PER_COL = 25

OUT_W, OUT_H = 850, 1100
PAGE_W_MM = 210.0
PAGE_H_MM = 297.0
MARK_MARGIN_MM = 10.0
PX_PER_MM_X = OUT_W / (PAGE_W_MM - 2 * MARK_MARGIN_MM)
PX_PER_MM_Y = OUT_H / (PAGE_H_MM - 2 * MARK_MARGIN_MM)
MARK_SIZE_RATIO = 0.015


def _mm_to_px_x(mm_val: float) -> int: return int(round(mm_val * PX_PER_MM_X))
def _mm_to_px_y(mm_val: float) -> int: return int(round(mm_val * PX_PER_MM_Y))


# ── Preprocessing ──

def deskew(img: np.ndarray) -> np.ndarray:
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
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def gray_world_normalize(img: np.ndarray) -> np.ndarray:
    """Correct color cast from colored lighting (ZipGrade-style)."""
    result = img.copy()
    for i in range(3):
        avg = np.mean(img[:, :, i])
        if avg > 0:
            result[:, :, i] = np.clip(img[:, :, i] * (128.0 / avg), 0, 255).astype(np.uint8)
    return result


def denoise_bilateral(img: np.ndarray) -> np.ndarray:
    """Bilateral filter — preserves edges while removing noise (better than Gaussian)."""
    return cv2.bilateralFilter(img, 9, 50, 50)


def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """Lighting normalization: CLAHE + sharpen — keeps grayscale shading for z-score detection.
    Does NOT binarize (unlike adaptive threshold), preserving relative darkness values."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # CLAHE — normalizes local contrast (handles uneven lighting)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(6, 6))
    enhanced = clahe.apply(gray)

    # Sharpen bubble edges
    kernel = np.array([[-0.5, -0.5, -0.5], [-0.5, 5, -0.5], [-0.5, -0.5, -0.5]], dtype=np.float32)
    sharpened = cv2.filter2D(enhanced, -1, kernel)

    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def preprocess_scan(img: np.ndarray) -> np.ndarray:
    """Full pipeline: deskew → gray world → bilateral → enhance contrast (keeps grayscale)."""
    img = deskew(img)
    img = gray_world_normalize(img)
    img = denoise_bilateral(img)
    img = enhance_contrast(img)
    return img


# ── Registration marks ──

def _adaptive_inverse(gray: np.ndarray, block_size: int = 15) -> np.ndarray:
    """Gaussian adaptive threshold (inverse binary) — fast, handles uneven lighting."""
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, block_size, 3)


def _otsu_inverse(gray: np.ndarray) -> np.ndarray:
    """OTSU threshold (inverse binary)."""
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh


def find_registration_marks(img: np.ndarray):
    """Find 4 corner registration marks with robust multi-method detection."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img.shape[:2]

    # Try multiple thresholding methods and combine contours
    candidates = set()
    min_area = (w * MARK_SIZE_RATIO) ** 2 * 0.2
    max_area = (w * MARK_SIZE_RATIO * 4) ** 2

    for method_fn in [_otsu_inverse, _adaptive_inverse]:
        try:
            thresh = method_fn(gray)
        except Exception:
            continue

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = bw / bh if bh > 0 else 0
            if 0.3 < aspect < 3.0:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    candidates.add((cx // 5 * 5, cy // 5 * 5))  # quantize to 5px grid

    if len(candidates) < 4:
        # Fallback: try edge-based detection (Canny + Hough)
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=min(h,w)//4, maxLineGap=20)
        if lines is not None:
            corners = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                corners.append((x1, y1))
                corners.append((x2, y2))
            if len(corners) >= 4:
                candidates = set((p[0]//10*10, p[1]//10*10) for p in corners)

    if len(candidates) < 4:
        return None

    cand_list = list(candidates)
    min_x = min(p[0] for p in cand_list)
    max_x = max(p[0] for p in cand_list)
    min_y = min(p[1] for p in cand_list)
    max_y = max(p[1] for p in cand_list)

    def dist_to(p, tx, ty):
        return abs(p[0] - tx) + abs(p[1] - ty)

    tl = min(cand_list, key=lambda p: dist_to(p, min_x, min_y))
    tr = min(cand_list, key=lambda p: dist_to(p, max_x, min_y))
    br = min(cand_list, key=lambda p: dist_to(p, max_x, max_y))
    bl = min(cand_list, key=lambda p: dist_to(p, min_x, max_y))
    return [tl, tr, br, bl]


def perspective_correct(img: np.ndarray, corners, output_size=(OUT_W, OUT_H)):
    src = np.array(corners, dtype=np.float32)
    dst = np.array([[0, 0], [output_size[0] - 1, 0],
                    [output_size[0] - 1, output_size[1] - 1], [0, output_size[1] - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, output_size)


# ── Smart Bubble Detection (ZipGrade-inspired) ──

def _bubble_stats(roi: np.ndarray) -> dict:
    """Compute detailed stats for a bubble region.
    Works on grayscale-enhanced image (not binarized).
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi
    h, w = gray.shape
    if h < 2 or w < 2:
        return {"mean_dark": 0.0, "fill_ratio": 0.0, "std": 0.0, "median_dark": 0.0,
                "pct25_dark": 0.0, "dark_pixel_ratio": 0.0, "hist_valley": 0.0}

    # Flatten pixel values
    pixels = gray.ravel().astype(np.float32)

    # Brightness percentiles (0=black, 255=white)
    mean_val = float(np.mean(pixels))
    median_val = float(np.median(pixels))
    p25 = float(np.percentile(pixels, 25))
    std_val = float(np.std(pixels))

    # Convert to darkness (255 - brightness)
    mean_dark = 255.0 - mean_val
    median_dark = 255.0 - median_val
    p25_dark = 255.0 - p25

    # OTSU threshold on the bubble region
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    fill_ratio_otsu = cv2.countNonZero(thresh) / (h * w) if h * w > 0 else 0

    # Gaussian adaptive threshold (catches faint marks, handles uneven lighting)
    bs = max(3, min(h, w) // 3)
    bs = bs + 1 if bs % 2 == 0 else bs
    adapt = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, bs, 3)
    fill_adapt = cv2.countNonZero(adapt) / (h * w) if h * w > 0 else 0

    # Sauvola-style: smaller block for fine detail
    try:
        bs2 = max(3, min(h, w) // 5)
        bs2 = bs2 if bs2 % 2 == 1 else bs2 + 1
        sauvola = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY_INV, bs2, 5)
        fill_sauvola = cv2.countNonZero(sauvola) / (h * w) if h * w > 0 else 0
    except Exception:
        fill_sauvola = fill_adapt

    # Dark pixel ratio: pixels darker than (median - some threshold)
    dark_thresh = max(50, median_val - 20)
    dark_pixel_ratio = float(np.sum(pixels < dark_thresh) / max(len(pixels), 1))

    # Histogram valley detection: find split between filled/unfilled peaks
    hist = cv2.calcHist([gray], [0], None, [64], [0, 256]).ravel()
    hist_smooth = cv2.GaussianBlur(hist, (3, 1), 0).ravel()
    # Find steepest drop (valley between bubble peak and background peak)
    diffs = np.diff(hist_smooth)
    valley_idx = int(np.argmin(diffs)) + 1 if len(diffs) > 0 else 32
    hist_valley = (valley_idx * 4) / 255.0  # normalized darkness at valley

    return {
        "mean_dark": mean_dark,
        "median_dark": median_dark,
        "p25_dark": p25_dark,
        "fill_ratio": max(fill_ratio_otsu, fill_adapt, fill_sauvola),
        "std": std_val,
        "dark_pixel_ratio": dark_pixel_ratio,
        "hist_valley": float(hist_valley),
    }


def _zscore_bubble_detection(bubble_stats: list, z_threshold: float = 1.2) -> list:
    """Detect filled bubbles using multi-metric z-score outlier detection.
    ZipGrade's key insight: compare each bubble RELATIVE to others in the same group,
    not against a fixed threshold. Handles varying lighting automatically.
    Combines mean_dark, median_dark, fill_ratio, and dark_pixel_ratio.
    """
    if not bubble_stats:
        return []
    n = len(bubble_stats)
    if n < 2:
        return []

    # Build z-scores for each metric independently
    metrics = ["mean_dark", "median_dark", "fill_ratio", "dark_pixel_ratio"]
    weights = [0.30, 0.25, 0.25, 0.20]
    filled = []

    for i, b in enumerate(bubble_stats):
        combined_z = 0.0
        for metric, weight in zip(metrics, weights):
            vals = [s[metric] for s in bubble_stats]
            m = mean(vals)
            s = stdev(vals) if n > 2 else max(m * 0.3, 1)
            z = (b[metric] - m) / max(s, 1)
            combined_z += z * weight

        if combined_z > z_threshold:
            filled.append((i, combined_z, b))

    filled.sort(key=lambda x: x[1], reverse=True)
    return filled


def _get_roi(warped, cx, cy, radius, margin=2):
    h, w = warped.shape[:2]
    x1 = max(0, cx - radius - margin)
    y1 = max(0, cy - radius - margin)
    x2 = min(w, cx + radius + margin)
    y2 = min(h, cy + radius + margin)
    return warped[y1:y2, x1:x2]


# ── NISN Detection ──

def _get_nisn_positions():
    # Empirically calibrated position from camera scans
    # These offsets were derived from test photos, not from page DIMs
    nisn_x_mm = 2.0
    nisn_y_mm = 0.0
    nisn_x = _mm_to_px_x(nisn_x_mm)
    nisn_y = _mm_to_px_y(nisn_y_mm) + _mm_to_px_y(LJK_BUBBLE_R * 2 + 2)
    id_r, id_gap, digits = 2.0, 4.2, 8
    b_r_px = _mm_to_px_y(id_r)
    col_step = _mm_to_px_x(id_gap + id_r * 2)
    row_step = _mm_to_px_y(id_r * 2 + 0.6)
    positions = []
    for d in range(digits):
        for o in range(10):
            positions.append((d, o, nisn_x + d * col_step + _mm_to_px_x(id_r), nisn_y + o * row_step))
    return positions, b_r_px


def detect_nisn(warped: np.ndarray) -> dict:
    positions, b_r = _get_nisn_positions()
    h, w = warped.shape[:2]

    # Group by digit position
    digit_bubbles = {}
    for digit_idx, opt_val, cx, cy in positions:
        if digit_idx not in digit_bubbles:
            digit_bubbles[digit_idx] = []
        roi = _get_roi(warped, cx, cy, b_r)
        if roi.size == 0:
            continue
        stats = _bubble_stats(roi)
        digit_bubbles[digit_idx].append((opt_val, stats))

    # Detect filled option per digit using z-score
    nisn_parts = []
    conf_sum = 0
    for d in range(8):
        if d not in digit_bubbles or not digit_bubbles[d]:
            nisn_parts.append("?")
            continue
        bubbles = digit_bubbles[d]
        # Use z-score within this digit's 10 options
        filled = _zscore_bubble_detection([b[1] for b in bubbles], z_threshold=1.2)
        if filled:
            best_opt = bubbles[filled[0][0]][0]
            nisn_parts.append(str(best_opt))
            conf_sum += min(1.0, filled[0][1] / 4.0)
        else:
            nisn_parts.append("?")

    nisn = "".join(nisn_parts)
    avg_conf = conf_sum / 8 if conf_sum > 0 else 0
    return {"nisn": nisn, "nisn_confidence": round(avg_conf, 3)}


# ── Answer Detection ──

def detect_answers(warped: np.ndarray, total_questions: int = 50, options: int = 5) -> dict:
    h, w = warped.shape[:2]
    opt_labels = ["A", "B", "C", "D", "E", "F", "G"][:options]
    positions, b_r = _get_grid_positions(total_questions, options)

    # Group by question
    questions = {}
    for q_idx, opt_idx, cx, cy in positions:
        questions.setdefault(q_idx, []).append((opt_idx, cx, cy))

    answers = {}
    confidence_map = {}
    ambiguous = {}

    for q_idx in range(total_questions):
        if q_idx not in questions:
            continue

        bubbles = questions[q_idx]
        # Collect stats for all options in this question
        all_stats = []
        for opt_idx, cx, cy in bubbles:
            roi = _get_roi(warped, cx, cy, b_r)
            if roi.size == 0:
                all_stats.append({"mean_dark": 0, "fill_ratio": 0, "std": 0})
            else:
                all_stats.append(_bubble_stats(roi))

        # ZipGrade-style: use z-score to detect filled bubbles
        filled = _zscore_bubble_detection(all_stats, z_threshold=1.8)

        if len(filled) == 1:
            best_idx, z, _ = filled[0]
            answers[str(q_idx)] = opt_labels[best_idx]
            confidence_map[str(q_idx)] = min(1.0, max(0.3, z / 4.0))
        elif len(filled) > 1:
            best_idx, z, _ = filled[0]
            second_z = filled[1][1] if len(filled) > 1 else 0
            gap = z - second_z
            if gap > 0.5:
                answers[str(q_idx)] = opt_labels[best_idx]
                confidence_map[str(q_idx)] = min(1.0, max(0.3, gap))
            else:
                ambiguous[str(q_idx)] = [opt_labels[f[0]] for f in filled]
                answers[str(q_idx)] = opt_labels[best_idx]
                confidence_map[str(q_idx)] = 0.3
        else:
            # No filled bubble detected — leave unanswered
            confidence_map[str(q_idx)] = 0.0

    high_conf = sum(1 for c in confidence_map.values() if c >= 0.7)
    avg_conf = round(sum(confidence_map.values()) / max(len(confidence_map), 1), 3)
    needs_review = {k for k, v in confidence_map.items() if v < 0.6 or k in ambiguous}

    return {
        "answers": answers, "detected": len(answers), "total": total_questions,
        "confidence": confidence_map, "avg_confidence": avg_conf,
        "high_confidence_count": high_conf, "ambiguous": ambiguous,
        "needs_review": sorted(needs_review, key=int),
        "needs_review_count": len(needs_review),
    }


def _get_grid_positions(total_questions: int = 50, options: int = 5):
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
                cx = col_start_x_px + opt_idx * b_gap_px + b_r_px
                cy = grid_top_y_px + row_in_col * row_h_px
                positions.append((q_idx, opt_idx, cx, cy))
        remaining -= col_count
    return positions, b_r_px


def load_image(image_data: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(image_data, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


# ── Public API ──

def process_scan(image_data: bytes, total_questions: int = 50, preprocess: bool = True) -> dict:
    """Full OMR pipeline: load → [preprocess] → find marks → correct → detect."""
    try:
        img = load_image(image_data)
        if img is None:
            return {"error": "Gagal membaca gambar. Format tidak didukung."}
        if preprocess:
            img = preprocess_scan(img)
        corners = find_registration_marks(img)
        if corners is None:
            return {"error": "Tanda registrasi tidak ditemukan. Pastikan seluruh lembar terlihat dalam foto."}
        warped = perspective_correct(img, corners)
        result = detect_answers(warped, total_questions=total_questions)
        nisn_result = detect_nisn(warped)
        result.update(nisn_result)
        result["preprocessed"] = preprocess
        return result
    except cv2.error as e:
        return {"error": f"Kesalahan pemrosesan gambar: {str(e)[:150]}"}
    except Exception as e:
        return {"error": f"Gagal memproses scan: {str(e)[:200]}"}


def draw_debug_image(img: np.ndarray, corners=None, answers=None) -> bytes:
    """Draw debug visualization on the image with detected answer overlays."""
    vis = img.copy()
    if corners:
        for i, (x, y) in enumerate(corners):
            cv2.circle(vis, (x, y), 10, (0, 255, 0), -1)
            cv2.putText(vis, str(i), (x + 10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        pts = np.array(corners, np.int32).reshape((-1, 1, 2))
        cv2.polylines(vis, [pts], True, (0, 255, 0), 2)

    # Draw detected answers on the image
    if answers:
        h, w = vis.shape[:2]
        labels = ["A", "B", "C", "D", "E", "F", "G"]
        # Compute positions matching the warped image geometry
        total_q = len(answers)
        if total_q > 0:
            cols = max(1, (total_q + LJK_Q_PER_COL - 1) // LJK_Q_PER_COL)
            q_per_col = min(LJK_Q_PER_COL, max(1, (total_q + cols - 1) // cols))
            grid_x_mm = LJK_MARGIN + LJK_GRID_X - MARK_MARGIN_MM
            grid_top_y_mm = LJK_GRID_TOP_Y - MARK_MARGIN_MM
            scale_x = w / OUT_W
            scale_y = h / OUT_H
            grid_x_px = int(grid_x_mm * PX_PER_MM_X * scale_x)
            grid_top_px = int(grid_top_y_mm * PX_PER_MM_Y * scale_y)
            b_gap_px = int(LJK_BUBBLE_GAP * PX_PER_MM_X * scale_x)
            row_h_px = int(LJK_ROW_H * PX_PER_MM_Y * scale_y)
            b_r_px = int(LJK_BUBBLE_R * PX_PER_MM_X * scale_x)
            col_width = int((PAGE_W_MM - LJK_MARGIN - LJK_GRID_X - LJK_MARGIN) * PX_PER_MM_X * scale_x / cols)

            remaining = total_q
            for col in range(cols):
                col_count = min(remaining, q_per_col)
                col_start_x = grid_x_px + col * col_width + int((col_width - LJK_BUBBLE_GAP * 5 * PX_PER_MM_X * scale_x) / 2)
                for row_in_col in range(col_count):
                    q_idx = str((total_q - remaining) + row_in_col)
                    if q_idx in answers:
                        ans = answers[q_idx]
                        if isinstance(ans, str) and ans in labels:
                            opt_idx = labels.index(ans)
                            cx = col_start_x + opt_idx * b_gap_px + b_r_px
                            cy = grid_top_px + row_in_col * row_h_px
                            # Green circle around detected answer
                            cv2.circle(vis, (cx, cy), b_r_px + 4, (0, 200, 0), 2)
                            cv2.putText(vis, ans, (cx - 4, cy - b_r_px - 4),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 0), 1)
                remaining -= col_count

    _, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()
