"""Gesture classification from MediaPipe 21-landmark hand data.

Landmark indices (MediaPipe Hands):
    0: WRIST
    1-4: THUMB (CMC, MCP, IP, TIP)
    5-8: INDEX (MCP, PIP, DIP, TIP)
    9-12: MIDDLE (MCP, PIP, DIP, TIP)
    13-16: RING (MCP, PIP, DIP, TIP)
    17-20: PINKY (MCP, PIP, DIP, TIP)

Gestures detected:
    OPEN_PALM  — all fingers extended (cursor movement)
    PINCH      — thumb tip close to index tip (left click)
    FIST       — all fingers curled (pause/hold)
    TWO_FINGER_SCROLL — index + middle extended, others curled (scroll)
    NONE       — unrecognized
"""

import math
from enum import Enum, auto


class Gesture(Enum):
    NONE = auto()
    OPEN_PALM = auto()
    PINCH = auto()
    FIST = auto()
    TWO_FINGER_SCROLL = auto()
    TAB_SWITCH = auto()
    RIGHT_CLICK = auto()


def _dist(a, b):
    """Euclidean distance between two (x, y, z) landmarks."""
    return math.sqrt((a[0] - b[0])**2 + (a[1] - b[1])**2 + (a[2] - b[2])**2)


def _dist_2d(a, b):
    """2D Euclidean distance (x, y only)."""
    return math.sqrt((a[0] - b[0])**2 + (a[1] - b[1])**2)


def _finger_is_extended(landmarks, finger_mcp, finger_pip, finger_dip,
                        finger_tip, wrist_idx=0):
    """Check if a finger is extended using y-coordinate comparison.

    In camera coordinates y increases downward, so an extended finger has
    its tip ABOVE (lower y) its PIP joint. This is robust regardless of
    hand rotation toward/away from the camera.
    """
    tip = landmarks[finger_tip]
    pip = landmarks[finger_pip]
    # Extended = tip is above PIP (lower y value)
    return tip[1] < pip[1]


def _thumb_is_extended(landmarks):
    """Thumb extension: compare x-distance from tip to MCP vs IP to MCP.

    The thumb moves laterally, so we check if the tip is farther from the
    palm (landmark 5, index MCP) than the IP joint is.
    """
    thumb_tip = landmarks[4]
    thumb_ip = landmarks[3]
    index_mcp = landmarks[5]
    # Thumb tip should be farther from index MCP than thumb IP is
    return _dist_2d(thumb_tip, index_mcp) > _dist_2d(thumb_ip, index_mcp)


class GestureClassifier:
    """Classifies hand gestures from 21 landmarks."""

    def __init__(self, pinch_threshold=0.05, fist_open_threshold=3,
                 stability_frames=3):
        """
        Args:
            pinch_threshold: Max distance between thumb tip and index tip
                (normalized coords) to count as a pinch.
            fist_open_threshold: Minimum number of extended fingers to count
                as an open palm.
            stability_frames: Number of consecutive frames a new gesture must
                be seen before switching. Prevents flicker between gestures.
        """
        self.pinch_threshold = pinch_threshold
        self.fist_open_threshold = fist_open_threshold
        self.stability_frames = stability_frames
        self._raw_prev = Gesture.NONE
        self._raw_count = 0
        self._stable_gesture = Gesture.NONE

    def _stabilize(self, raw_gesture):
        """Require a gesture to persist for N frames before switching."""
        if raw_gesture == self._raw_prev:
            self._raw_count += 1
        else:
            self._raw_prev = raw_gesture
            self._raw_count = 1

        if self._raw_count >= self.stability_frames:
            self._stable_gesture = raw_gesture

        return self._stable_gesture

    def classify(self, landmarks):
        """Classify gesture from 21 landmarks [(x,y,z), ...].

        Returns (Gesture, dict) where dict has debug info.
        """
        if landmarks is None or len(landmarks) != 21:
            return self._stabilize(Gesture.NONE), {}

        # Check individual finger extension
        index_ext = _finger_is_extended(landmarks, 5, 6, 7, 8)
        middle_ext = _finger_is_extended(landmarks, 9, 10, 11, 12)
        ring_ext = _finger_is_extended(landmarks, 13, 14, 15, 16)
        pinky_ext = _finger_is_extended(landmarks, 17, 18, 19, 20)
        thumb_ext = _thumb_is_extended(landmarks)

        fingers = [thumb_ext, index_ext, middle_ext, ring_ext, pinky_ext]
        extended_count = sum(fingers)

        # Pinch distances: thumb tip to index tip / middle tip
        pinch_dist = _dist(landmarks[4], landmarks[8])
        mid_pinch_dist = _dist(landmarks[4], landmarks[12])

        debug = {
            "fingers": fingers,
            "extended_count": extended_count,
            "pinch_dist": pinch_dist,
            "mid_pinch_dist": mid_pinch_dist,
        }

        # Priority: right click (mid pinch) > left click (index pinch) >
        #           tab switch > two-finger scroll > open palm > fist
        # Check middle-finger pinch first so it doesn't get eaten by index pinch
        if mid_pinch_dist < self.pinch_threshold:
            return self._stabilize(Gesture.RIGHT_CLICK), debug

        if pinch_dist < self.pinch_threshold:
            return self._stabilize(Gesture.PINCH), debug

        # Shaka: thumb + pinky extended, index + middle + ring curled
        if thumb_ext and pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return self._stabilize(Gesture.TAB_SWITCH), debug

        if index_ext and middle_ext and not ring_ext and not pinky_ext:
            return self._stabilize(Gesture.TWO_FINGER_SCROLL), debug

        if extended_count >= self.fist_open_threshold:
            return self._stabilize(Gesture.OPEN_PALM), debug

        if extended_count <= 1:
            return self._stabilize(Gesture.FIST), debug

        return self._stabilize(Gesture.NONE), debug

    def palm_center(self, landmarks):
        """Return palm center as (x, y) — centroid of wrist + finger MCP landmarks.

        Uses landmarks 0 (wrist), 5 (index MCP), 9 (middle MCP),
        13 (ring MCP), 17 (pinky MCP) for a true palm center.
        """
        if landmarks is None:
            return None
        indices = [0, 5, 9, 13, 17]
        cx = sum(landmarks[i][0] for i in indices) / len(indices)
        cy = sum(landmarks[i][1] for i in indices) / len(indices)
        return cx, cy
