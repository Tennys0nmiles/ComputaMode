"""Main hand gesture control loop.

Wires together: camera tracking → gesture classification → input injection.

Usage:
    python -m src.main [--camera 0] [--calibrate] [--no-overlay]
"""

import argparse
import math
import os
import subprocess
import time

import cv2

from src.tracker import HandTracker
from src.gestures import GestureClassifier, Gesture
from src.smoothing import PointSmoother
from evdev import ecodes
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
    print("Gestures: OPEN_PALM=move, PINCH=click, FIST=pause, SHAKA=alt-tab")

    paused = False
    pinch_was_active = False
    right_click_was_active = False
    prev_gesture = Gesture.NONE
    scroll_prev_y = None
    tab_switch_active = False
    tab_switch_anchor_tilt = None   # tilt value when tab switch started
    tab_switch_anchor_spread = None # initial thumb-pinky 2D distance (for scaling)
    tab_switch_step = 0             # which tab position we're at
    pinch_zoom_anchor = None        # hand size when pinch started
    pinch_zoom_step = 0             # cumulative zoom steps sent
    swipe_prev_x = None             # previous palm x for velocity tracking
    swipe_prev_time = None          # timestamp of previous frame
    swipe_cooldown = 0.0            # cooldown after a swipe fires
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
                    now = time.time()

                    # Swipe detection: fast horizontal palm movement
                    swiped = False
                    if swipe_prev_x is not None and swipe_prev_time is not None:
                        dt = now - swipe_prev_time
                        if dt > 0 and now > swipe_cooldown:
                            velocity_x = (sx - swipe_prev_x) / dt
                            # Threshold: ~3.0 normalized units/sec = deliberate flick
                            if abs(velocity_x) > 3.0:
                                if velocity_x > 0:
                                    # Swipe right (mirrored) = previous workspace
                                    mouse.key_press(ecodes.KEY_LEFTCTRL)
                                    mouse.key_press(ecodes.KEY_LEFTALT)
                                    mouse.key_tap(ecodes.KEY_LEFT)
                                    mouse.key_release(ecodes.KEY_LEFTALT)
                                    mouse.key_release(ecodes.KEY_LEFTCTRL)
                                else:
                                    # Swipe left (mirrored) = next workspace
                                    mouse.key_press(ecodes.KEY_LEFTCTRL)
                                    mouse.key_press(ecodes.KEY_LEFTALT)
                                    mouse.key_tap(ecodes.KEY_RIGHT)
                                    mouse.key_release(ecodes.KEY_LEFTALT)
                                    mouse.key_release(ecodes.KEY_LEFTCTRL)
                                swiped = True
                                swipe_cooldown = now + 0.8  # prevent rapid re-trigger
                                smoother.reset()

                    swipe_prev_x = sx
                    swipe_prev_time = now

                    if not swiped:
                        screen_x, screen_y = map_to_screen(
                            sx, sy, active_region, screen_w, screen_h)
                        mouse.move_to(screen_x, screen_y)
                    scroll_prev_y = None

                elif gesture == Gesture.PINCH:
                    # Move cursor to pinch position
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)

                    # Hand size proxy: wrist (0) to middle MCP (9) distance
                    hand_size = math.sqrt(
                        (landmarks[0][0] - landmarks[9][0])**2 +
                        (landmarks[0][1] - landmarks[9][1])**2)

                    if not pinch_was_active:
                        # First frame of pinch: click and set zoom anchor
                        mouse.click()
                        pinch_zoom_anchor = hand_size
                        pinch_zoom_step = 0
                        mouse.key_press(ecodes.KEY_LEFTCTRL)
                    else:
                        # Subsequent frames: track size change for zoom
                        # Each 0.015 of size change = one scroll tick
                        delta = hand_size - pinch_zoom_anchor
                        new_step = int(delta / 0.015)
                        steps_needed = new_step - pinch_zoom_step
                        if steps_needed != 0:
                            for _ in range(abs(steps_needed)):
                                mouse.scroll(1 if steps_needed > 0 else -1)
                            pinch_zoom_step = new_step
                    scroll_prev_y = None

                elif gesture == Gesture.RIGHT_CLICK:
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)
                    if not right_click_was_active:
                        mouse.click(ecodes.BTN_RIGHT)
                    scroll_prev_y = None

                elif gesture == Gesture.TWO_FINGER_SCROLL:
                    if scroll_prev_y is not None:
                        delta = scroll_prev_y - sy  # up = positive scroll
                        # Scale: small hand movement → reasonable scroll
                        scroll_amount = delta * 50
                        if abs(scroll_amount) > 0.5:
                            mouse.scroll(int(scroll_amount))
                    scroll_prev_y = sy

                elif gesture == Gesture.TAB_SWITCH:
                    # Track hand tilt via horizontal offset between
                    # thumb tip (4) and pinky MCP base (17).
                    # Pinky base is more stable than pinky tip.
                    tilt = landmarks[4][0] - landmarks[17][0]
                    spread = math.sqrt(
                        (landmarks[4][0] - landmarks[17][0])**2 +
                        (landmarks[4][1] - landmarks[17][1])**2)

                    if not tab_switch_active:
                        mouse.key_press(ecodes.KEY_LEFTALT)
                        mouse.key_tap(ecodes.KEY_TAB)
                        tab_switch_active = True
                        tab_switch_anchor_tilt = tilt
                        tab_switch_anchor_spread = max(spread, 0.01)
                        tab_switch_step = 0
                    else:
                        # Normalize tilt change by initial spread so each
                        # tab switch is a consistent fraction of rotation.
                        # ~15% of the initial spread = one tab switch.
                        delta = tilt - tab_switch_anchor_tilt
                        step_size = tab_switch_anchor_spread * 0.15
                        new_step = int(delta / step_size)
                        steps_needed = new_step - tab_switch_step
                        if steps_needed != 0:
                            for _ in range(abs(steps_needed)):
                                mouse.key_tap(ecodes.KEY_TAB)
                            tab_switch_step = new_step
                    scroll_prev_y = None

                elif gesture == Gesture.FIST:
                    scroll_prev_y = None

                # Reset swipe tracking when not in open palm
                if gesture != Gesture.OPEN_PALM:
                    swipe_prev_x = None
                    swipe_prev_time = None

            # Release Alt when leaving tab switch gesture
            if tab_switch_active and gesture != Gesture.TAB_SWITCH:
                mouse.key_release(ecodes.KEY_LEFTALT)
                tab_switch_active = False
                tab_switch_anchor_tilt = None
                tab_switch_anchor_spread = None
                tab_switch_step = 0

            if gesture == Gesture.FIST:
                if prev_gesture != Gesture.FIST:
                    paused = True
                    smoother.reset()
                    scroll_prev_y = None
            elif paused and gesture == Gesture.OPEN_PALM:
                paused = False

            if pinch_was_active and gesture != Gesture.PINCH:
                mouse.key_release(ecodes.KEY_LEFTCTRL)
                pinch_zoom_anchor = None
                pinch_zoom_step = 0
            pinch_was_active = (gesture == Gesture.PINCH)
            right_click_was_active = (gesture == Gesture.RIGHT_CLICK)

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
        if tab_switch_active:
            mouse.key_release(ecodes.KEY_LEFTALT)
        if pinch_was_active:
            mouse.key_release(ecodes.KEY_LEFTCTRL)
        tracker.release()
        mouse.close()
        cv2.destroyAllWindows()
        print("Shut down cleanly.")


if __name__ == "__main__":
    main()
