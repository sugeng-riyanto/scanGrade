"""Generate dummy LJK test images — no printer needed!
Creates realistic LJK images with filled bubbles for OMR testing.

Usage:
  python test_scripts/generate_test_data.py --count 20 --output test_omr/batch1
  python test_scripts/generate_test_data.py --count 100 --output test_omr/batch_big
"""
import os
import io
import json
import random
import argparse
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont


# Constants matching ljk_generator.py
PAGE_W, PAGE_H = 210, 297  # mm (A4)
MARGIN = 15
MARK_SIZE = 8  # mm
BUBBLE_R = 2.8  # mm
BUBBLE_GAP = 6.5  # mm
ROW_H = 8.5  # mm
QUESTIONS_PER_COL = 25
ID_BUBBLE_R = 2.0  # mm for NISN
ID_DIGIT_GAP = 4.2  # mm
MM_TO_PX = 4  # 4 pixels per mm → 840x1188 ≈ A4


def _mm(val):
    return int(val * MM_TO_PX)


def _draw_registration_mark(draw, cx, cy, size_mm):
    s = _mm(size_mm)
    # Outer black square
    draw.rectangle([cx - s//2, cy - s//2, cx + s//2, cy + s//2], fill=(0, 0, 0))
    # Inner white square
    hs = s // 4
    draw.rectangle([cx - hs, cy - hs, cx + hs, cy + hs], fill=(255, 255, 255))


def _draw_bubble(draw, cx, cy, r_px, fill=False):
    if fill:
        draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], fill=(30, 30, 30), outline=(0, 0, 0))
    else:
        draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], fill=(255, 255, 255), outline=(180, 180, 180))


def generate_ljk_image(
    total_questions=50,
    options=5,
    answers=None,  # dict: {"0": "A", "1": "B", ...}
    nisn_digits=None,  # list of 8 ints (0-9)
    noise_level=0.0,  # 0.0 = clean, 1.0 = noisy
    partial_fill=False,  # simulate incomplete erasures
    rotation_deg=0.0,
    add_noise=False,
):
    """Generate a realistic LJK image with optional filled bubbles and noise."""
    w_px = _mm(PAGE_W)
    h_px = _mm(PAGE_H)

    # White background with subtle paper texture
    img = Image.new("RGB", (w_px, h_px), (250, 247, 240))
    draw = ImageDraw.Draw(img)

    # ── Registration marks (4 corners) ──
    margin_px = _mm(MARGIN)
    reg_positions = [
        (margin_px, margin_px),
        (w_px - margin_px, margin_px),
        (margin_px, h_px - margin_px),
        (w_px - margin_px, h_px - margin_px),
        (w_px // 2, h_px - margin_px),
    ]
    for cx, cy in reg_positions:
        _draw_registration_mark(draw, cx, cy, MARK_SIZE)

    # ── NISN area (left side) ──
    nisn_x = _mm(MARGIN + 2)
    nisn_y = _mm(70)  # t_content
    for d in range(8):
        digit_x = nisn_x + d * _mm(ID_DIGIT_GAP + ID_BUBBLE_R * 2)
        for n in range(10):
            by = nisn_y + n * _mm(ID_BUBBLE_R * 2 + 0.6)
            cx = digit_x + _mm(ID_BUBBLE_R)
            cy = by + _mm(ID_BUBBLE_R)
            fill = nisn_digits and n == nisn_digits[d]
            _draw_bubble(draw, cx, cy, _mm(ID_BUBBLE_R), fill=fill)

    # ── Answer grid ──
    grid_x = _mm(MARGIN + 50)
    grid_top_y = _mm(70)
    cols = max(1, (total_questions + QUESTIONS_PER_COL - 1) // QUESTIONS_PER_COL)
    q_per_col = min(QUESTIONS_PER_COL, max(1, (total_questions + cols - 1) // cols))
    col_width = (w_px - grid_x - margin_px) // cols
    opt_labels = ["A", "B", "C", "D", "E"][:options]

    remaining = total_questions
    for col in range(cols):
        col_count = min(remaining, q_per_col)
        col_start = grid_x + col * col_width + (col_width - _mm(BUBBLE_GAP) * options) // 2
        for row in range(col_count):
            q_idx = total_questions - remaining + row
            for opt_idx in range(options):
                cx = col_start + opt_idx * _mm(BUBBLE_GAP) + _mm(BUBBLE_R)
                cy = grid_top_y + row * _mm(ROW_H) + _mm(BUBBLE_R)
                # Check if this bubble should be filled
                fill = False
                if answers and str(q_idx) in answers:
                    fill = answers[str(q_idx)] == opt_labels[opt_idx]
                _draw_bubble(draw, cx, cy, _mm(BUBBLE_R), fill=fill)

        remaining -= col_count

    # ── Border lines for realism ──
    draw.rectangle([1, 1, w_px - 1, h_px - 1], outline=(200, 200, 200), width=1)

    # ── Add subtle noise (paper texture, dust) ──
    if add_noise:
        arr = np.array(img)
        # Gaussian noise
        noise = np.random.normal(0, 5, arr.shape).astype(np.int16)
        arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        # Random dark specks (like dust on scanner)
        for _ in range(random.randint(5, 30)):
            sx, sy = random.randint(0, w_px - 1), random.randint(0, h_px - 1)
            cv2.circle(arr, (sx, sy), random.randint(1, 3), (random.randint(100, 180),) * 3, -1)
        img = Image.fromarray(arr)

    # ── Rotation (simulate tilted scan) ──
    if rotation_deg != 0:
        img = img.rotate(rotation_deg, expand=False, fill=(250, 247, 240))

    # ── Convert to JPEG bytes ──
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def generate_random_answers(total_questions, options=5):
    """Generate random answer keys for testing."""
    labels = ["A", "B", "C", "D", "E"][:options]
    return {str(i): random.choice(labels) for i in range(total_questions)}


def generate_random_nisn():
    """Generate random 8-digit NISN."""
    return [random.randint(0, 9) for _ in range(8)]


def main():
    parser = argparse.ArgumentParser(description="Generate dummy LJK test images")
    parser.add_argument("--count", type=int, default=10, help="Number of test images to generate")
    parser.add_argument("--output", default="test_omr/batch1", help="Output directory")
    parser.add_argument("--questions", type=int, default=50, help="Questions per LJK")
    parser.add_argument("--options", type=int, default=5, help="Options per question (2-5)")
    parser.add_argument("--noise", type=float, default=0.3, help="Noise level (0-1, default 0.3)")
    parser.add_argument("--with-errors", action="store_true", help="Include some images with errors (rotation, darkness)")
    parser.add_argument("--ground-truth", action="store_true", help="Save ground truth JSON alongside images")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    print(f"📸 Generating {args.count} dummy LJK images in {args.output}/")

    ground_truths = []

    for i in range(args.count):
        answers = generate_random_answers(args.questions, args.options)
        nisn = generate_random_nisn()

        # Vary conditions for realistic testing
        noise = args.noise * (0.5 + random.random())
        rotate = 0.0
        if args.with_errors and i > args.count * 0.7:
            rotate = random.uniform(-3, 3)  # slight rotation
            noise = min(1.0, noise * 1.5)

        img_bytes = generate_ljk_image(
            total_questions=args.questions,
            options=args.options,
            answers=answers,
            nisn_digits=nisn,
            noise_level=noise,
            rotation_deg=rotate,
            add_noise=True,
        )

        fname = f"ljk_{i+1:03d}.jpg"
        with open(os.path.join(args.output, fname), "wb") as f:
            f.write(img_bytes)

        gt = {
            "filename": fname,
            "nisn": "".join(str(d) for d in nisn),
            "answers": answers,
        }
        ground_truths.append(gt)

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{args.count} generated")

    # Save ground truth
    if args.ground_truth:
        gt_path = os.path.join(args.output, "_ground_truth.json")
        with open(gt_path, "w") as f:
            json.dump(ground_truths, f, indent=2)
        print(f"✅ Ground truth saved: {gt_path}")

    # Create ZIP for bulk testing
    zip_path = args.output.rstrip("/") + ".zip"
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(args.output)):
            if fname.endswith(".jpg"):
                zf.write(os.path.join(args.output, fname), arcname=fname)
    print(f"📦 ZIP created: {zip_path} ({os.path.getsize(zip_path)/1024:.0f}KB)")

    # Summary
    print(f"\n=== HASIL ===")
    print(f"Total images: {args.count}")
    print(f"Questions per LJK: {args.questions}")
    print(f"Options: {args.options}")
    print(f"With rotation errors: {'Yes' if args.with_errors else 'No'}")
    print(f"\n💡 Next steps:")
    print(f"   Single test: python test_scripts/stress_omr.py --dir {args.output} --exam <exam_id>")
    print(f"   Bulk test:   python test_scripts/stress_omr.py --zip {zip_path} --bulk --exam <exam_id>")
    print(f"   Upload UI:   Buka Super Admin → Uji OMR → upload file dari {args.output}/")


if __name__ == "__main__":
    main()
