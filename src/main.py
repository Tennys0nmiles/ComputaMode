"""Main hand gesture control loop.

Wires together: camera tracking → gesture classification → input injection.

Usage:
    python -m src.main [--camera 0] [--calibrate] [--no-overlay]
"""

import argparse
import math
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2

from src.tracker import HandTracker
from src.gestures import GestureClassifier, Gesture
from src.smoothing import PointSmoother
from evdev import ecodes
from src.injector import VirtualMouse
from src.calibration import load_calibration, run_calibration
from src.config import load_config
from src.dispatcher import ActionDispatcher


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
    parser.add_argument("--no-voice", action="store_true",
                        help="Disable voice command layer (skip Whisper load)")
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

    # Shared mutable state (allows dispatcher to toggle paused)
    state = {"paused": False}
    dispatcher = ActionDispatcher(mouse, state)

    # Voice command layer (optional)
    voice_enabled = not args.no_voice
    ptt_recorder = None
    voice_matcher = None
    if voice_enabled:
        try:
            from src.voice.listener import PTTRecorder
            from src.voice.transcriber import transcribe, preload
            from src.voice.intent import IntentMatcher
            CONFIG_PATH = Path(__file__).resolve().parent.parent / "voice_commands.yaml"
            print("Loading Whisper model (downloads ~74 MB on first run)...")
            preload()
            voice_matcher = IntentMatcher(str(CONFIG_PATH))
            ptt_recorder = PTTRecorder()
            print("Voice commands ready. Touch thumb to pinky tip to record.")
        except Exception as exc:
            print(f"Voice layer unavailable: {exc}")
            voice_enabled = False

    print("Hand gesture control active. Press 'q' in overlay to quit.")
    print("Gestures: OPEN_PALM=move, PINCH=drag, FIST=pause, SHAKA=alt-tab, "
          "THUMB+PINKY=voice PTT")

    ptt_was_active = False
    pinch_was_active = False
    zoom_was_active = False
    right_click_was_active = False
    prev_gesture = Gesture.NONE
    tab_switch_active = False
    tab_switch_anchor_tilt = None   # tilt value when tab switch started
    tab_switch_anchor_spread = None # initial thumb-pinky 2D distance (for scaling)
    tab_switch_last_fire = 0.0      # time of last tab press
    TAB_SWITCH_TEMPO = 0.45         # seconds between tab presses when tilted
    TAB_SWITCH_DEADZONE = 0.12      # fraction of spread to ignore (neutral zone)
    zoom_anchor = None              # hand size when zoom started
    zoom_step = 0                   # cumulative zoom steps sent
    swipe_prev_x = None             # previous palm x for velocity tracking
    swipe_prev_time = None          # timestamp of previous frame
    swipe_cooldown = 0.0            # cooldown after a swipe fires
    scroll_anchor_y = None          # y position when TWO_FINGER_SCROLL entered
    scroll_last_fire = 0.0          # time of last scroll tick fired
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

            if landmarks and not state["paused"]:
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
                    scroll_anchor_y = None

                elif gesture == Gesture.PINCH:
                    # Move cursor and hold left button for drag/highlight
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)
                    if not pinch_was_active:
                        mouse.press()
                    scroll_anchor_y = None

                elif gesture == Gesture.ZOOM:
                    # Three-finger pinch (thumb+index+middle tips): Ctrl+scroll zoom
                    # Hand size proxy: wrist (0) to middle MCP (9) distance
                    hand_size = math.sqrt(
                        (landmarks[0][0] - landmarks[9][0])**2 +
                        (landmarks[0][1] - landmarks[9][1])**2)
                    if not zoom_was_active:
                        zoom_anchor = hand_size
                        zoom_step = 0
                        mouse.key_press(ecodes.KEY_LEFTCTRL)
                    else:
                        delta = hand_size - zoom_anchor
                        new_step = int(delta / 0.015)
                        steps_needed = new_step - zoom_step
                        if steps_needed != 0:
                            for _ in range(abs(steps_needed)):
                                mouse.scroll(1 if steps_needed > 0 else -1)
                            zoom_step = new_step
                    scroll_anchor_y = None

                elif gesture == Gesture.RIGHT_CLICK:
                    screen_x, screen_y = map_to_screen(
                        sx, sy, active_region, screen_w, screen_h)
                    mouse.move_to(screen_x, screen_y)
                    if not right_click_was_active:
                        mouse.click(ecodes.BTN_RIGHT)
                    scroll_anchor_y = None

                elif gesture == Gesture.TWO_FINGER_SCROLL:
                    now_s = time.time()
                    if scroll_anchor_y is None:
                        scroll_anchor_y = sy  # capture neutral position on entry
                        scroll_last_fire = now_s

                    offset = scroll_anchor_y - sy  # + = hand moved up = scroll up
                    SCROLL_DEADZONE = 0.03          # ~3% of frame height, ignore jitter
                    if abs(offset) > SCROLL_DEADZONE:
                        # Speed: interval shrinks as offset grows (faster = more tilt)
                        # 0.03 offset → 0.18s interval; 0.10+ offset → 0.06s interval
                        t = min((abs(offset) - SCROLL_DEADZONE) / 0.07, 1.0)
                        interval = 0.18 - t * 0.12
                        if now_s - scroll_last_fire >= interval:
                            mouse.scroll(1 if offset > 0 else -1)
                            scroll_last_fire = now_s

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
                        tab_switch_last_fire = time.time()
                    elif classifier.raw_gesture == Gesture.TAB_SWITCH:
                        # Only cycle while raw gesture is still shaka
                        delta = tilt - tab_switch_anchor_tilt
                        deadzone = tab_switch_anchor_spread * TAB_SWITCH_DEADZONE
                        now = time.time()
                        if abs(delta) > deadzone and now - tab_switch_last_fire >= TAB_SWITCH_TEMPO:
                            mouse.key_tap(ecodes.KEY_TAB)
                            tab_switch_last_fire = now
                    scroll_anchor_y = None

                elif gesture == Gesture.FIST:
                    scroll_anchor_y = None

                # Reset swipe tracking when not in open palm
                if gesture != Gesture.OPEN_PALM:
                    swipe_prev_x = None
                    swipe_prev_time = None

            # Reset scroll anchor when leaving two-finger scroll
            if gesture != Gesture.TWO_FINGER_SCROLL:
                scroll_anchor_y = None

            # Release Alt when leaving tab switch gesture
            if tab_switch_active and gesture != Gesture.TAB_SWITCH:
                mouse.key_release(ecodes.KEY_LEFTALT)
                tab_switch_active = False
                tab_switch_anchor_tilt = None
                tab_switch_anchor_spread = None
                tab_switch_last_fire = 0.0

            # PTT: voice recording (works regardless of paused state)
            if voice_enabled and ptt_recorder is not None:
                if gesture == Gesture.PTT_RECORD:
                    if not ptt_was_active:
                        ptt_recorder.start_recording()
                elif ptt_was_active:
                    # Just released PTT — transcribe + dispatch in background
                    audio = ptt_recorder.stop_recording()
                    if len(audio) >= 1600:
                        def _voice_task(audio_data):
                            text = transcribe(audio_data)
                            if text:
                                matches = voice_matcher.match_all(text)
                                for action, score, phrase in matches:
                                    dispatcher.execute(action)
                        threading.Thread(
                            target=_voice_task, args=(audio,), daemon=True
                        ).start()
                ptt_was_active = (gesture == Gesture.PTT_RECORD)

            if gesture == Gesture.FIST:
                if prev_gesture != Gesture.FIST:
                    state["paused"] = True
                    smoother.reset()
                    scroll_anchor_y = None
            elif state["paused"] and gesture == Gesture.OPEN_PALM:
                state["paused"] = False

            if pinch_was_active and gesture != Gesture.PINCH:
                mouse.release()
            pinch_was_active = (gesture == Gesture.PINCH)

            if zoom_was_active and gesture != Gesture.ZOOM:
                mouse.key_release(ecodes.KEY_LEFTCTRL)
                zoom_anchor = None
                zoom_step = 0
            zoom_was_active = (gesture == Gesture.ZOOM)

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
                status = "PAUSED" if state["paused"] else gesture.name
                color = (0, 0, 255) if state["paused"] else (0, 255, 0)
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
            mouse.release()
        if zoom_was_active:
            mouse.key_release(ecodes.KEY_LEFTCTRL)
        tracker.release()
        mouse.close()
        cv2.destroyAllWindows()
        print("Shut down cleanly.")


if __name__ == "__main__":
    main()
