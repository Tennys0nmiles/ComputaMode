"""Virtual input device via kernel uinput + evdev.

Creates a virtual mouse device that works on both Wayland and X11 because
it operates at the kernel input layer — the compositor sees it as a real
hardware mouse. This is the key advantage over pyautogui/pynput which use
X11-specific protocols (XTEST) that Wayland compositors block.

Tradeoff: requires /dev/uinput access (handled by udev rule + input group),
but works universally across display servers.
"""

import evdev
from evdev import UInput, ecodes, AbsInfo


class JsonNamespace:
    """Minimal attribute-access wrapper for a dict (used for screen info)."""
    def __init__(self, d):
        self.__dict__.update(d)


class JsonNamespaceEncoder:
    pass


class VirtualMouse:
    """A uinput-based virtual mouse for cursor movement and clicks."""

    def __init__(self, screen_width=1920, screen_height=1080):
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Create a virtual input device with absolute positioning + buttons + scroll
        cap = {
            ecodes.EV_ABS: [
                (ecodes.ABS_X, AbsInfo(
                    value=0, min=0, max=screen_width - 1,
                    fuzz=0, flat=0, resolution=0)),
                (ecodes.ABS_Y, AbsInfo(
                    value=0, min=0, max=screen_height - 1,
                    fuzz=0, flat=0, resolution=0)),
            ],
            ecodes.EV_KEY: [
                ecodes.BTN_LEFT,
                ecodes.BTN_RIGHT,
                ecodes.BTN_MIDDLE,
                ecodes.KEY_LEFTALT,
                ecodes.KEY_TAB,
                ecodes.KEY_LEFTCTRL,
                ecodes.KEY_LEFT,
                ecodes.KEY_RIGHT,
            ],
            ecodes.EV_REL: [
                ecodes.REL_WHEEL,
            ],
        }
        self.device = UInput(cap, name="hand-gesture-mouse",
                             vendor=0x1234, product=0x5678)

    def move_to(self, x, y):
        """Move cursor to absolute screen position (x, y)."""
        x = max(0, min(int(x), self.screen_width - 1))
        y = max(0, min(int(y), self.screen_height - 1))
        self.device.write(ecodes.EV_ABS, ecodes.ABS_X, x)
        self.device.write(ecodes.EV_ABS, ecodes.ABS_Y, y)
        self.device.syn()

    def click(self, button=ecodes.BTN_LEFT):
        """Send a click (press + release)."""
        self.device.write(ecodes.EV_KEY, button, 1)  # press
        self.device.syn()
        self.device.write(ecodes.EV_KEY, button, 0)  # release
        self.device.syn()

    def press(self, button=ecodes.BTN_LEFT):
        """Press and hold a button."""
        self.device.write(ecodes.EV_KEY, button, 1)
        self.device.syn()

    def release(self, button=ecodes.BTN_LEFT):
        """Release a held button."""
        self.device.write(ecodes.EV_KEY, button, 0)
        self.device.syn()

    def scroll(self, amount):
        """Scroll vertically. Positive = up, negative = down."""
        self.device.write(ecodes.EV_REL, ecodes.REL_WHEEL, int(amount))
        self.device.syn()

    def key_press(self, key):
        """Press a key."""
        self.device.write(ecodes.EV_KEY, key, 1)
        self.device.syn()

    def key_release(self, key):
        """Release a key."""
        self.device.write(ecodes.EV_KEY, key, 0)
        self.device.syn()

    def key_tap(self, key):
        """Press and release a key."""
        self.key_press(key)
        self.key_release(key)

    def close(self):
        """Destroy the virtual device."""
        self.device.close()
