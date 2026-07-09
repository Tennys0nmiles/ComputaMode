"""Calibration mode: samples hand gestures to set detection thresholds.

Walks the user through holding each gesture (open palm, pinch, fist) and
records landmark distances to compute per-device thresholds. Saves results
to calibration/thresholds.json (gitignored, device-local).
"""

import json
import os
import time

import cv2

from src.gestures import _dist, _finger_is_extended, _thumb_is_extended
from src.tracker import HandTracker

CALIBRATION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "calibration"
)
CALIBRATION_FILE = os.path.join(CALIBRATION_DIR, "thresholds.json")

SAMPLE_DURATION = 3.0   # seconds per gesture
SAMPLE_INTERVAL = 0.05  # seconds between samples


def _collect_samples(tracker, gesture_name, duration=SAMPLE_DURATION):
    """Show camera feed and collect landmark samples for a gesture.

    Returns list of landmark arrays collected over `duration` seconds.
    """
    samples = []
    start = time.time()
    print(f"\n  Hold '{gesture_name}' gesture steady for {duration:.0f} seconds...")

    while time.time() - start < duration:
        ok, frame = tracker.read_frame()
        if not ok:
            continue

        frame = cv2.flip(frame, 1)
        results = tracker.process(frame)
        tracker.draw_landmarks(frame, results)

        elapsed = time.time() - start
        remaining = max(0, duration - elapsed)
        progress = int((elapsed / duration) * 30)
        bar = "#" * progress + "-" * (30 - progress)

        cv2.putText(frame, f"CALIBRATION: {gesture_name.upper()}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
        cv2.putText(frame, f"[{bar}] {remaining:.1f}s", (10, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        landmarks = tracker.get_landmarks(results)
        if landmarks:
            samples.append(landmarks)
            cv2.putText(frame, f"Samples: {len(samples)}", (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        else:
            cv2.putText(frame, "No hand detected - show your hand!", (10, 95),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)

        cv2.imshow("Calibration", frame)
        cv2.waitKey(int(SAMPLE_INTERVAL * 1000))

    print(f"  Collected {len(samples)} samples for '{gesture_name}'.")
    return samples


def _compute_pinch_distances(samples):
    """Compute thumb-tip to index-tip distances from pinch samples."""
    return [_dist(s[4], s[8]) for s in samples]


def _compute_extended_counts(samples):
    """Count extended fingers per sample."""
    counts = []
    for lm in samples:
        exts = [
            _thumb_is_extended(lm),
            _finger_is_extended(lm, 5, 6, 7, 8),
            _finger_is_extended(lm, 9, 10, 11, 12),
            _finger_is_extended(lm, 13, 14, 15, 16),
            _finger_is_extended(lm, 17, 18, 19, 20),
        ]
        counts.append(sum(exts))
    return counts


def run_calibration(camera_index=0):
    """Run interactive calibration. Returns thresholds dict and saves to file."""
    tracker = HandTracker(camera_index=camera_index)

    print("\n=== HAND GESTURE CALIBRATION ===")
    print("You'll be asked to hold 3 gestures. Keep your hand steady and visible.")
    print("Press any key in the camera window to start each gesture.\n")

    # --- Wait screen ---
    while True:
        ok, frame = tracker.read_frame()
        if ok:
            frame = cv2.flip(frame, 1)
            cv2.putText(frame, "CALIBRATION: Press any key to start",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            cv2.putText(frame, "1) Open palm  2) Pinch  3) Fist",
                        (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.imshow("Calibration", frame)
        if cv2.waitKey(1) & 0xFF != 255:
            break

    # --- Collect open palm ---
    _show_instruction(tracker, "OPEN PALM: spread all fingers wide")
    open_samples = _collect_samples(tracker, "open palm")

    # --- Collect pinch ---
    _show_instruction(tracker, "PINCH: touch thumb tip to index tip")
    pinch_samples = _collect_samples(tracker, "pinch")

    # --- Collect fist ---
    _show_instruction(tracker, "FIST: close all fingers tightly")
    fist_samples = _collect_samples(tracker, "fist")

    tracker.release()
    cv2.destroyAllWindows()

    # --- Compute thresholds ---
    thresholds = _compute_thresholds(open_samples, pinch_samples, fist_samples)

    # Save
    os.makedirs(CALIBRATION_DIR, exist_ok=True)
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(thresholds, f, indent=2)

    print(f"\nCalibration saved to {CALIBRATION_FILE}")
    print(f"  pinch_threshold: {thresholds['pinch_threshold']:.4f}")
    print(f"  fist_open_threshold: {thresholds['fist_open_threshold']}")
    return thresholds


def _show_instruction(tracker, text):
    """Show instruction text and wait for keypress."""
    while True:
        ok, frame = tracker.read_frame()
        if ok:
            frame = cv2.flip(frame, 1)
            results = tracker.process(frame)
            tracker.draw_landmarks(frame, results)
            cv2.putText(frame, text, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
            cv2.putText(frame, "Press any key when ready...", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            cv2.imshow("Calibration", frame)
        if cv2.waitKey(1) & 0xFF != 255:
            break


def _compute_thresholds(open_samples, pinch_samples, fist_samples):
    """Derive thresholds from collected samples."""
    # Pinch threshold: midpoint between pinch distances and open-palm distances
    if pinch_samples:
        pinch_dists = _compute_pinch_distances(pinch_samples)
        avg_pinch = sum(pinch_dists) / len(pinch_dists)
    else:
        avg_pinch = 0.03

    if open_samples:
        open_dists = _compute_pinch_distances(open_samples)
        avg_open = sum(open_dists) / len(open_dists)
    else:
        avg_open = 0.15

    # Threshold is halfway between average pinch distance and average open distance
    pinch_threshold = (avg_pinch + avg_open) / 2.0

    # Fist/open threshold: based on extended finger counts
    if open_samples:
        open_counts = _compute_extended_counts(open_samples)
        avg_open_count = sum(open_counts) / len(open_counts)
    else:
        avg_open_count = 5.0

    if fist_samples:
        fist_counts = _compute_extended_counts(fist_samples)
        avg_fist_count = sum(fist_counts) / len(fist_counts)
    else:
        avg_fist_count = 0.0

    # Threshold is midpoint, at least 2
    fist_open_threshold = max(2, int(round((avg_open_count + avg_fist_count) / 2.0)))

    return {
        "pinch_threshold": round(pinch_threshold, 4),
        "fist_open_threshold": fist_open_threshold,
        "avg_pinch_dist": round(avg_pinch, 4),
        "avg_open_dist": round(avg_open, 4),
        "avg_open_finger_count": round(avg_open_count, 2),
        "avg_fist_finger_count": round(avg_fist_count, 2),
    }


def load_calibration():
    """Load saved calibration thresholds. Returns dict or None."""
    if os.path.exists(CALIBRATION_FILE):
        with open(CALIBRATION_FILE) as f:
            return json.load(f)
    return None


if __name__ == "__main__":
    run_calibration()
