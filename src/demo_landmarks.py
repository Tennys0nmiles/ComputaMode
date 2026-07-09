"""Stage 1 demo: camera feed + hand landmark visualization.

Run this to confirm your webcam and MediaPipe are working before
wiring up gesture recognition or input injection.

Usage:
    python -m src.demo_landmarks [--camera 0]

Press 'q' to quit.
"""

import argparse
import cv2
from src.tracker import HandTracker


def main():
    parser = argparse.ArgumentParser(description="Hand landmark demo")
    parser.add_argument("--camera", type=int, default=0,
                        help="Camera device index (default: 0)")
    args = parser.parse_args()

    tracker = HandTracker(camera_index=args.camera)
    print("Hand landmark demo running. Press 'q' in the window to quit.")

    try:
        while True:
            ok, frame = tracker.read_frame()
            if not ok:
                print("Failed to read frame, exiting.")
                break

            # Mirror the frame so it feels natural
            frame = cv2.flip(frame, 1)

            results = tracker.process(frame)
            tracker.draw_landmarks(frame, results)

            landmarks = tracker.get_landmarks(results)
            if landmarks:
                # Show palm center (landmark 9 = middle finger MCP) coords
                palm = landmarks[9]
                cv2.putText(frame, f"Palm: ({palm[0]:.2f}, {palm[1]:.2f})",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (0, 255, 0), 2)
            else:
                cv2.putText(frame, "No hand detected", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            cv2.imshow("Hand Tracking - Stage 1", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        tracker.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
