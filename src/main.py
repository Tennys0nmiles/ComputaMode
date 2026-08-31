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

import numpy as np
import sounddevice as sd
import yaml

import cv2

from src.tracker import HandTracker
from src.gestures import GestureClassifier, Gesture
from src.smoothing import PointSmoother
from evdev import ecodes
from src.injector import VirtualMouse
from src.calibration import load_calibration, run_calibration
from src.config import load_config, resolve_camera_index
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


def _notify(title: str, body: str = "", urgency: str = "low") -> None:
    """Fire a desktop notification non-blocking. Never raises."""
    try:
        cmd = ["notify-send", f"--urgency={urgency}",
               "--icon=audio-input-microphone",
               "--app-name=ComputaMode",
               "-t", "2500", title]
        if body:
            cmd.append(body)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _media_is_playing() -> bool:
    """True if some MPRIS player (e.g. Spotify) is currently playing.
    Never raises; returns False if playerctl or a player isn't available."""
    try:
        result = subprocess.run(
            ["playerctl", "status"],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip() == "Playing"
    except Exception:
        return False


def _media_pause() -> None:
    """Pause whatever's playing, so it doesn't talk over the mic during PTT."""
    try:
        subprocess.Popen(["playerctl", "pause"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _media_resume() -> None:
    """Resume playback after a PTT voice command finishes."""
    try:
        subprocess.Popen(["playerctl", "play"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _beep_no_match(freq: int = 440, duration: float = 0.12, volume: float = 0.35) -> None:
    """Play a short low-key beep to signal no command was matched."""
    try:
        sr = 22050
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        tone = (volume * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        # Quick fade-out to avoid click
        fade = np.linspace(1.0, 0.0, len(tone))
        sd.play(tone * fade, samplerate=sr)
        sd.wait()
    except Exception:
        pass  # never block the voice thread


def main():
    parser = argparse.ArgumentParser(description="Hand gesture cursor control")
    parser.add_argument("--camera", type=int, default=None,
                        help="Camera device index. Default: auto-pick a "
                             "plugged-in USB webcam (see config.yaml "
                             "camera.prefer_external), else config.yaml "
                             "camera.index.")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration before starting")
    parser.add_argument("--no-overlay", action="store_true",
                        help="Hide the camera overlay window")
    parser.add_argument("--no-voice", action="store_true",
                        help="Disable voice command layer (skip Whisper load)")
    parser.add_argument("--no-assistant", action="store_true",
                        help="Disable conversational assistant (skip Ollama/TTS load)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print v2 commands instead of executing them")
    args = parser.parse_args()

    config = load_config()

    camera_index = args.camera if args.camera is not None else resolve_camera_index(config)

    # Read interpreter flag and set up v2/legacy path
    CONFIG_PATH = Path(__file__).resolve().parent.parent / "voice_commands.yaml"
    _vcfg = yaml.safe_load(CONFIG_PATH.read_text())
    _interpreter_flag = _vcfg.get("interpreter", "legacy")

    # Start JSONL logger (both paths log from the same logger)
    from src.voice.logger import get_logger
    voice_logger = get_logger()

    # Calibration
    if args.calibrate:
        cal = run_calibration(camera_index)
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

    print(f"[camera] using /dev/video{camera_index}", flush=True)

    # Components
    classifier = GestureClassifier(pinch_thresh, fist_thresh)
    smoother = PointSmoother(
        freq=config["smoothing"]["freq"],
        min_cutoff=config["smoothing"]["min_cutoff"],
        beta=config["smoothing"]["beta"],
        d_cutoff=config["smoothing"]["d_cutoff"],
    )
    tracker = HandTracker(
        camera_index=camera_index,
        detection_conf=config["tracking"]["detection_confidence"],
        tracking_conf=config["tracking"]["tracking_confidence"],
    )
    mouse = VirtualMouse(screen_w, screen_h)

    # Shared mutable state (allows dispatcher to toggle paused)
    state = {"paused": False}
    dispatcher = ActionDispatcher(mouse, state)

    # v2 executor (needs mouse + state; _tts may not be loaded yet — set below)
    if _interpreter_flag == "v2" and not args.no_voice:
        try:
            from src.voice.executor import Executor as VoiceExecutor
            voice_executor = VoiceExecutor(mouse, state, tts=None, dry_run=args.dry_run)
        except Exception:
            voice_executor = None

    # Voice command layer (optional)
    voice_enabled = not args.no_voice
    ptt_recorder = None
    voice_matcher = None
    voice_executor = None
    if voice_enabled:
        try:
            from src.voice.listener import PTTRecorder
            from src.voice.transcriber import transcribe, preload
            print("Loading Whisper model (downloads ~74 MB on first run)...")
            preload()
            ptt_recorder = PTTRecorder()

            if _interpreter_flag == "v2":
                from src.voice.registry import Registry
                from src.voice.interpreter import Interpreter
                from src.voice.executor import Executor as VoiceExecutor
                _registry = Registry(str(CONFIG_PATH))
                if _registry.load_embeddings() is None:
                    print("[v2] WARNING: embedding cache missing — Tier 2 semantic disabled. "
                          "Run setup.sh to precompute.")
                voice_matcher = Interpreter(_registry, _vcfg)
                # voice_executor created after mouse/state are ready (below)
            else:
                from src.voice.intent import IntentMatcher
                voice_matcher = IntentMatcher(str(CONFIG_PATH))

            print("Voice commands ready. Touch thumb to pinky tip to record.")
        except Exception as exc:
            print(f"Voice layer unavailable: {exc}")
            voice_enabled = False

    # Conversational assistant (Stage 3, optional)
    _tts = None
    if voice_enabled and not args.no_assistant:
        try:
            _asst_cfg = _vcfg.get("assistant", {})
            if _asst_cfg.get("enabled", True):
                from src.assistant.tts import TTSEngine
                from src.assistant.brain import AssistantBrain
                from src.assistant import router as _router_mod
                _repo_root = Path(__file__).resolve().parent.parent
                _tts_cfg = _asst_cfg.get("tts", {})
                _llm_cfg = _asst_cfg.get("llm", {})
                print("Loading TTS voice...")
                _tts = TTSEngine(_tts_cfg, repo_root=_repo_root,
                                 logger=voice_logger)
                _brain = AssistantBrain(
                    model=_llm_cfg.get("model", ""),
                    base_url=_llm_cfg.get("base_url", "http://localhost:11434"),
                    max_history_turns=_llm_cfg.get("max_history_turns", 4),
                    temperature=_llm_cfg.get("temperature", 0.7),
                    no_think=_llm_cfg.get("no_think", False),
                    persona=_asst_cfg.get("persona",
                        "You are Nova, a helpful concise female assistant. "
                        "Answer in 1-2 sentences."),
                )
                _router_mod.init(_brain, _tts)
                _llm_model = _llm_cfg.get("model", "")
                print(f"Assistant ready (Nova / {_llm_model}). Ask me anything.")
                # Wire TTS to v2 executor for confirmation prompts
                if voice_executor is not None:
                    voice_executor.tts = _tts
        except Exception as exc:
            print(f"Assistant unavailable: {exc}")

    print("Hand gesture control active. Press 'q' in overlay to quit.")
    print("Gestures: OPEN_PALM=move, PINCH=drag, FIST=pause, SHAKA=alt-tab, "
          "THUMB+PINKY=voice PTT")

    ptt_was_active = False
    ptt_media_was_playing = False
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
    scroll_anchor_y = None          # y position when TWO_FINGER_SCROLL entered
    scroll_last_fire = 0.0          # time of last scroll tick fired
    show_overlay = not args.no_overlay

    # Thread-safe PTT status shown in overlay
    # [text, expire_time, color_bgr]
    _ptt_status = ["", 0.0, (255, 255, 255)]
    _ptt_lock = threading.Lock()

    def _set_ptt_status(text: str, duration: float = 2.5,
                        color=(255, 255, 255)) -> None:
        with _ptt_lock:
            _ptt_status[0] = text
            _ptt_status[1] = time.monotonic() + duration
            _ptt_status[2] = color

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
                        # Duck any playing media so it doesn't talk over the mic.
                        ptt_media_was_playing = _media_is_playing()
                        if ptt_media_was_playing:
                            _media_pause()
                        ptt_recorder.start_recording()
                        _set_ptt_status("● REC", 30.0, (0, 80, 255))
                elif ptt_was_active:
                    # Just released PTT — transcribe + dispatch in background
                    audio = ptt_recorder.stop_recording()
                    resume_media = ptt_media_was_playing
                    if len(audio) < 1600 and resume_media:
                        _media_resume()
                    if len(audio) >= 1600:
                        def _voice_task(audio_data):
                            _set_ptt_status("listening...", 8.0, (255, 220, 0))
                            t0 = time.monotonic()
                            text = transcribe(audio_data)
                            latency_ms = lambda: int((time.monotonic() - t0) * 1000)
                            if not text:
                                _set_ptt_status("(nothing heard)", 1.5,
                                                (100, 100, 255))
                                _notify("ComputaMode", "(nothing heard)")
                                threading.Thread(
                                    target=_beep_no_match, daemon=True
                                ).start()
                                return
                            print(f"[PTT] heard: {text!r}", flush=True)

                            if _interpreter_flag == "v2":
                                # v2 path
                                if voice_executor is not None and \
                                        voice_executor._pending_confirm is not None:
                                    voice_executor.confirm_response(text)
                                    return
                                from src.voice.intent import fallback_interpreter
                                results = voice_matcher.interpret_all(text)
                                if not results:
                                    voice_logger.log(text, "conv", None, 0.0,
                                                     latency_ms())
                                    _set_ptt_status(f"? {text[:40]}", 2.5,
                                                    (80, 80, 255))
                                    _notify("? no match", f'heard: "{text}"',
                                            urgency="normal")
                                    threading.Thread(
                                        target=_beep_no_match, daemon=True
                                    ).start()
                                    fallback_interpreter(text)
                                else:
                                    labels = []
                                    for result in results:
                                        if result.intent_name == "_error_":
                                            if _tts:
                                                _tts.speak("Sorry, say that again?")
                                            voice_logger.log(
                                                text, result.tier, None, 0.0,
                                                latency_ms(), error="tier3_fail"
                                            )
                                            return
                                        voice_logger.log(
                                            text, result.tier, result.intent_name,
                                            result.confidence, latency_ms(),
                                            dry_run=args.dry_run,
                                        )
                                        if voice_executor is not None:
                                            voice_executor.execute(result)
                                        if result.clarification_question and _tts:
                                            _tts.speak(result.clarification_question)
                                        labels.append(result.intent_name)
                                    label_str = ", ".join(labels)
                                    _set_ptt_status(f">> {label_str}", 2.0,
                                                    (0, 255, 128))
                                    _notify(f"✓  {label_str}", f'heard: "{text}"')
                            else:
                                # legacy path — with logging
                                matches = voice_matcher.match_all(text)
                                if matches:
                                    labels = ", ".join(p for _, _, p in matches)
                                    _set_ptt_status(f">> {labels}", 2.0,
                                                    (0, 255, 128))
                                    _notify(f"✓  {labels}", f'heard: "{text}"')
                                    for action, score, phrase in matches:
                                        voice_logger.log(text, "legacy", phrase,
                                                         score, latency_ms())
                                        dispatcher.execute(action)
                                else:
                                    voice_logger.log(text, "legacy", None, 0.0,
                                                     latency_ms())
                                    _set_ptt_status(f"? {text[:40]}", 2.5,
                                                    (80, 80, 255))
                                    _notify("? no match", f'heard: "{text}"',
                                            urgency="normal")
                                    threading.Thread(
                                        target=_beep_no_match, daemon=True
                                    ).start()

                        def _voice_task_and_resume(audio_data,
                                                    resume_media=resume_media):
                            try:
                                _voice_task(audio_data)
                            finally:
                                if resume_media:
                                    _media_resume()

                        threading.Thread(
                            target=_voice_task_and_resume, args=(audio,),
                            daemon=True
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
                # PTT status banner
                with _ptt_lock:
                    ptt_text = _ptt_status[0]
                    ptt_expire = _ptt_status[1]
                    ptt_color = tuple(_ptt_status[2])
                if ptt_text and time.monotonic() < ptt_expire:
                    cv2.putText(frame, ptt_text, (10, 65),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, ptt_color, 2)
                cv2.imshow("Hand Gesture Control", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if cv2.getWindowProperty("Hand Gesture Control",
                                         cv2.WND_PROP_VISIBLE) < 1:
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
