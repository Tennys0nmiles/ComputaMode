"""Stage 2 demo: gesture classification visualization.

Shows the camera feed with detected gesture name, palm position (smoothed),
and debug info (finger states, pinch distance). Run this to verify gesture
detection works before wiring input injection.

Usage:
    python -m src.demo_gestures [--camera 0] [--calibrate]

Press 'q' to quit.
"""

import argparse
import cv2

from src.tracker import HandTracker
from src.gestures import GestureClassifier, Gesture
from src.smoothing import PointSmoother
from src.calibration import load_calibration, run_calibration
from src.config import load_config


GESTURE_COLORS = {
    Gesture.OPEN_PALM: (0, 255, 0),      # green
    Gesture.PINCH: (0, 165, 255),         # orange
    Gesture.FIST: (0, 0, 255),            # red
    Gesture.TWO_FINGER_SCROLL: (255, 255, 0),  # cyan
    Gesture.NONE: (128, 128, 128),        # gray
}


def main():
    parser = argparse.ArgumentParser(description="Gesture classification demo")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration before starting")
    args = parser.parse_args()

    config = load_config()

    if args.calibrate:
        cal = run_calibration(args.camera)
    else:
        cal = load_calibration()

    # Apply calibration or config defaults
    pinch_thresh = config["gestures"]["pinch_threshold"]
    fist_thresh = config["gestures"]["fist_open_threshold"]
    if cal:
        pinch_thresh = cal["pinch_threshold"]
        fist_thresh = cal["fist_open_threshold"]
        print(f"Using calibrated thresholds: pinch={pinch_thresh}, fist_open={fist_thresh}")
    else:
        print(f"No calibration found, using defaults: pinch={pinch_thresh}, fist_open={fist_thresh}")
        print("Run with --calibrate for better accuracy.")

    classifier = GestureClassifier(
        pinch_threshold=pinch_thresh,
        fist_open_threshold=fist_thresh,
    )

    smoother = PointSmoother(
        freq=config["smoothing"]["freq"],
        min_cutoff=config["smoothing"]["min_cutoff"],
        beta=config["smoothing"]["beta"],
        d_cutoff=config["smoothing"]["d_cutoff"],
    )

    tracker = HandTracker(
        camera_index=args.camera,
        detection_conf=config["tracking"]["detection_confidence"],
        tracking_conf=config["tracking"]["tracking_confidence"],
    )

    print("Gesture demo running. Press 'q' to quit.")

    try:
        while True:
            ok, frame = tracker.read_frame()
            if not ok:
                break

            if config["camera"]["mirror"]:
                frame = cv2.flip(frame, 1)

            results = tracker.process(frame)
            tracker.draw_landmarks(frame, results)
            landmarks = tracker.get_landmarks(results)

            gesture, debug = classifier.classify(landmarks)
            color = GESTURE_COLORS[gesture]

            # Gesture name
            cv2.putText(frame, gesture.name, (10, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

            if landmarks:
                # Smoothed palm position
                px, py = classifier.palm_center(landmarks)
                sx, sy = smoother(px, py)
                h, w = frame.shape[:2]
                cx, cy = int(sx * w), int(sy * h)
                cv2.circle(frame, (cx, cy), 8, (255, 0, 255), -1)

                # Debug info
                fingers = debug.get("fingers", [])
                finger_names = ["T", "I", "M", "R", "P"]
                finger_str = " ".join(
                    f"{n}:{'1' if v else '0'}" for n, v in zip(finger_names, fingers)
                )
                cv2.putText(frame, finger_str, (10, 65),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                cv2.putText(frame,
                            f"Pinch dist: {debug.get('pinch_dist', 0):.3f} "
                            f"(thresh: {pinch_thresh:.3f})",
                            (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (200, 200, 200), 1)
            else:
                smoother.reset()

            cv2.imshow("Gesture Demo - Stage 2", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        tracker.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
