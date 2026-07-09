"""Main hand gesture control loop.

Wires together: camera tracking → gesture classification → input injection.

Usage:
    python -m src.main [--camera 0] [--calibrate] [--no-overlay]
"""

import argparse
import os
import subprocess
import time

import cv2

from src.tracker import HandTracker
from src.gestures import GestureClassifier, Gesture
from src.smoothing import PointSmoother
from src.injector import VirtualMouse
from src.calibration import load_calibration, run_calibration
from src.config import load_config


def get_screen_resolution():
    """Detect screen resolution via xrandr."""
    try:
        result = subprocess.run(["xrandr", "--current"], capture_output=True,
                                text=True, timeout=5)
        for line in result.stdout.splitlines():
            if " connected" in line:
                for part in line.split():
                    if "x" in part and "+" in part:
                        res = part.split("+")[0]
                        w, h = res.split("x")
                        return int(w), int(h)
    except Exception:
        pass
    return 1920, 1080  # fallback


def map_to_screen(palm_x, palm_y, active_region, screen_w, screen_h):
    """Map normalized palm position within active_region to screen coordinates."""
    left, top, right, bottom = active_region
    # Clamp to active region
    nx = max(0.0, min(1.0, (palm_x - left) / (right - left)))
    ny = max(0.0, min(1.0, (palm_y - top) / (bottom - top)))
    return nx * screen_w, ny * screen_h


def main():
    parser = argparse.ArgumentParser(description="Hand gesture cursor control")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration before starting")
    parser.add_argument("--no-overlay", action="store_true",
                        help="Hide the camera overlay window")
    args = parser.parse_args()

    config = load_config()

    # Calibration
    if args.calibrate:
        cal = run_calibration(args.camera)
    else:
        cal = load_calibration()

    pinch_thresh = config["gestures"]["pinch_threshold"]
    fist_thresh = config["gestures"]["fist_open_threshold"]
    if cal:
        pinch_thresh = cal["pinch_threshold"]
        fist_thresh = cal["fist_open_threshold"]
        print(f"Calibrated thresholds: pinch={pinch_thresh:.4f}, "
              f"fist_open={fist_thresh}")
    else:
        print(f"Default thresholds: pinch={pinch_thresh}, fist_open={fist_thresh}")
        print("Tip: run with --calibrate for better accuracy.")

    # Screen resolution
    screen_w, screen_h = get_screen_resolution()
    print(f"Screen: {screen_w}x{screen_h}")

    active_region = config["screen"]["active_region"]
    actions = config["actions"]

    # Components
    classifier = GestureClassifier(pinch_thresh, fist_thresh)
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
    mouse = VirtualMouse(screen_w, screen_h)

    print("Hand gesture control active. Press 'q' in overlay to quit.")
    print("Gestures: OPEN_PALM=move, PINCH=click, FIST=pause")

    paused = False
    pinch_was_active = False
    prev_gesture = Gesture.NONE
    scroll_prev_y = None
    show_overlay = not args.no_overlay

    try:
        while True:
            ok, frame = tracker.read_frame()
            if not ok:
                break

            if config["camera"]["mirror"]:
                frame = cv2.flip(frame, 1)

            results = tracker.process(frame)
            landmarks = tracker.get_landmarks(results)
            gesture, debug = classifier.classify(landmarks)

            if landmarks and not paused:
                palm_x, palm_y = classifier.palm_center(landmarks)
                sx, sy = smoother(palm_x, palm_y)

                if gesture == Gesture.OPEN_PALM:
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)
                    scroll_prev_y = None

                elif gesture == Gesture.PINCH:
                    # Move cursor to pinch position
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)
                    # Click on transition to pinch
                    if not pinch_was_active:
                        mouse.click()
                    scroll_prev_y = None

                elif gesture == Gesture.TWO_FINGER_SCROLL:
                    if scroll_prev_y is not None:
                        delta = scroll_prev_y - sy  # up = positive scroll
                        # Scale: small hand movement → reasonable scroll
                        scroll_amount = delta * 50
                        if abs(scroll_amount) > 0.5:
                            mouse.scroll(int(scroll_amount))
                    scroll_prev_y = sy

                elif gesture == Gesture.FIST:
                    scroll_prev_y = None

            if gesture == Gesture.FIST:
                if prev_gesture != Gesture.FIST:
                    paused = True
                    smoother.reset()
                    scroll_prev_y = None
            elif paused and gesture == Gesture.OPEN_PALM:
                paused = False

            pinch_was_active = (gesture == Gesture.PINCH)

            # Handle custom shell command gesture
            if gesture != Gesture.NONE and gesture != prev_gesture:
                gesture_key = gesture.name.lower()
                action = actions.get(gesture_key, "")
                if isinstance(action, str) and action not in (
                        "move_cursor", "left_click", "pause", "scroll", ""):
                    # It's a custom shell command
                    try:
                        subprocess.Popen(action, shell=True,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                    except Exception:
                        pass

            prev_gesture = gesture

            # Overlay window
            if show_overlay:
                tracker.draw_landmarks(frame, results)
                status = "PAUSED" if paused else gesture.name
                color = (0, 0, 255) if paused else (0, 255, 0)
                cv2.putText(frame, status, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Hand Gesture Control", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
            else:
                # Still need a small delay to not spin CPU
                time.sleep(0.01)

    finally:
        tracker.release()
        mouse.close()
        cv2.destroyAllWindows()
        print("Shut down cleanly.")


if __name__ == "__main__":
    main()
