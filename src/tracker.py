"""Hand tracking using MediaPipe Hands + OpenCV."""

import cv2
import mediapipe as mp


class HandTracker:
    """Wraps MediaPipe Hands to detect and track a single hand."""

    def __init__(self, camera_index=0, max_hands=1, detection_conf=0.7,
                 tracking_conf=0.6):
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {camera_index}")

        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_hands,
            min_detection_confidence=detection_conf,
            min_tracking_confidence=tracking_conf,
        )
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_styles = mp.solutions.drawing_styles

    def read_frame(self):
        """Read a frame from the camera. Returns (success, bgr_frame)."""
        return self.cap.read()

    def process(self, bgr_frame):
        """Run hand detection on a BGR frame.

        Returns the MediaPipe results object (results.multi_hand_landmarks).
        """
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = self.hands.process(rgb)
        return results

    def draw_landmarks(self, frame, results):
        """Draw hand landmarks + connections on the frame (mutates in place)."""
        if results.multi_hand_landmarks:
            for hand_lms in results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(
                    frame,
                    hand_lms,
                    self.mp_hands.HAND_CONNECTIONS,
                    self.mp_styles.get_default_hand_landmarks_style(),
                    self.mp_styles.get_default_hand_connections_style(),
                )
        return frame

    def get_landmarks(self, results):
        """Extract the first hand's 21 landmarks as a list of (x, y, z).

        Coordinates are normalized [0, 1] relative to frame dimensions.
        Returns None if no hand detected.
        """
        if not results.multi_hand_landmarks:
            return None
        hand = results.multi_hand_landmarks[0]
        return [(lm.x, lm.y, lm.z) for lm in hand.landmark]

    def release(self):
        """Release camera and MediaPipe resources."""
        self.cap.release()
        self.hands.close()
