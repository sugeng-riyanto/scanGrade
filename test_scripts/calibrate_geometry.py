"""Kalibrasi geometry OMR — cari offset X/Y optimal untuk tipe HP tertentu.
Usage:
  python test_scripts/calibrate_geometry.py --image test_omr/reference.jpg --truth '{"0":"A","1":"B"}'
"""
import os
import json
import sys
import argparse
import cv2
import numpy as np

# Import OMR service (butuh PYTHONPATH)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.omr_service import (
    process_scan, _get_grid_positions, _get_nisn_positions,
    _bubble_stats, _zscore_bubble_detection, _mm_to_px_x, _mm_to_px_y,
    LJK_GRID_TOP_Y, MARK_MARGIN_MM, LJK_BUBBLE_R, LJK_MARGIN, LJK_GRID_X,
    OUT_W, OUT_H, PX_PER_MM_X, PX_PER_MM_Y, PAGE_W_MM
)


def test_geometry(image_path, ground_truth, total_q=50, offset_x=0, offset_y=0):
    """Test OMR dengan offset geometry tertentu, return accuracy."""
    with open(image_path, "rb") as f:
        raw = f.read()

    result = process_scan(raw, total_questions=total_q, preprocess=True)
    answers = result.get("answers", {})

    matched = 0
    for q, expected in ground_truth.items():
        if str(q) in answers and answers[str(q)] == expected:
            matched += 1

    return {
        "matched": matched,
        "total": len(ground_truth),
        "accuracy": round(matched / max(len(ground_truth), 1) * 100, 1),
        "detected": result.get("detected", 0),
        "nisn": result.get("nisn", "?"),
        "avg_conf": result.get("avg_confidence", 0),
    }


def auto_calibrate(image_path, ground_truth, total_q=50, dx_range=(-5, 5), dy_range=(-5, 5)):
    """Cari offset X/Y optimal dengan grid search."""
    best = {"accuracy": 0, "dx": 0, "dy": 0, "result": None}

    for dx in range(dx_range[0], dx_range[1] + 1):
        for dy in range(dy_range[0], dy_range[1] + 1):
            # Apply offset by modifying global constants temporarily
            global LJK_GRID_TOP_Y_offset
            LJK_GRID_TOP_Y_offset = dy

            result = test_geometry(image_path, ground_truth, total_q, dx, dy)
            if result["accuracy"] > best["accuracy"]:
                best = result
                best["dx"] = dx
                best["dy"] = dy
                print(f"  ↑ New best: dx={dx}, dy={dy}, accuracy={result['accuracy']}%")

    return best


def main():
    parser = argparse.ArgumentParser(description="OMR Geometry Calibrator")
    parser.add_argument("--image", required=True, help="Path to reference LJK image")
    parser.add_argument("--truth", required=True, help="Ground truth JSON: '{\"0\":\"A\",\"1\":\"B\"}'")
    parser.add_argument("--total", type=int, default=50, help="Total questions")
    parser.add_argument("--auto", action="store_true", help="Auto-calibrate with grid search")
    args = parser.parse_args()

    # Parse ground truth
    ground_truth = json.loads(args.truth)

    print(f"\n📐 OMR Geometry Calibrator")
    print(f"   Image: {args.image}")
    print(f"   Ground truth: {len(ground_truth)} questions")
    print(f"   Total questions: {args.total}")

    if args.auto:
        print(f"\n🔍 Auto-calibrating (grid search dx=-5..5, dy=-5..5)...")
        best = auto_calibrate(args.image, ground_truth, args.total)
        print(f"\n=== BEST OFFSET ===")
        print(f"dx (X offset): {best['dx']}")
        print(f"dy (Y offset): {best['dy']}")
        print(f"Accuracy: {best['accuracy']}%")
        print(f"Matched: {best['result']['matched']}/{best['result']['total']}")
        print(f"\n💡 Update omr_service.py constants with:")
        print(f"   LJK_GRID_TOP_Y = {67.2 + best['dy']}  # current 67.2 + offset")
    else:
        # Single test with current geometry
        result = test_geometry(args.image, ground_truth, args.total)
        print(f"\n=== CURRENT GEOMETRY ===")
        print(f"Accuracy: {result['accuracy']}%")
        print(f"Matched: {result['matched']}/{result['total']}")
        print(f"Detected: {result['detected']}/{args.total}")
        print(f"NISN: {result['nisn']}")
        print(f"Avg confidence: {result['avg_conf']*100:.1f}%")
        print(f"\n💡 Run with --auto to find optimal offset")


if __name__ == "__main__":
    main()
