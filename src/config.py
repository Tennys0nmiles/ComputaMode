"""YAML config loader."""

import glob
import os
import re
import yaml

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.yaml"
)

BY_ID_DIR = "/dev/v4l/by-id"

# Name fragments that identify a laptop's built-in camera (never preferred
# over a plugged-in USB webcam).
_BUILTIN_HINTS = ("integrated", "built-in", "builtin", "facetime")


def _external_camera_index() -> "int | None":
    """Return the /dev/videoN index of a plugged-in USB webcam, preferring
    one whose stable by-id name doesn't look like a built-in camera.

    Uses .../by-id/*-video-index0 links, since a webcam's first video node
    (index0) is normally the actual capture device; index1+ are usually
    metadata-only nodes."""
    try:
        links = sorted(glob.glob(os.path.join(BY_ID_DIR, "*-video-index0")))
    except OSError:
        return None

    externals = []
    for link in links:
        name = os.path.basename(link).lower()
        if any(hint in name for hint in _BUILTIN_HINTS):
            continue
        try:
            target = os.readlink(link)
        except OSError:
            continue
        m = re.search(r"video(\d+)$", target)
        if m:
            externals.append(int(m.group(1)))

    return min(externals) if externals else None


def resolve_camera_index(config: dict) -> int:
    """Pick the camera device index to open.

    If  camera.prefer_external  is true (default) and a USB webcam is
    currently plugged in, use it. Otherwise fall back to the configured
    camera.index (or 0)."""
    cam_cfg = config.get("camera", {})
    if cam_cfg.get("prefer_external", True):
        external = _external_camera_index()
        if external is not None:
            return external
    return cam_cfg.get("index", 0)


def load_config(path=None):
    """Load and return the config dict from YAML."""
    path = path or DEFAULT_CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)
