"""One-Euro Filter for cursor smoothing.

Canonical implementation based on the paper:
  Casiez, Roussel, Vogel - "1 Euro Filter: A Simple Speed-based Low-pass Filter
  for Noisy Input in Interactive Systems" (CHI 2012)

The filter balances smoothness (low jitter at rest) with responsiveness (low lag
during fast movement) by adapting its cutoff frequency based on input speed.
"""

import math


class OneEuroFilter:
    """Applies the 1-Euro filter to a single scalar signal."""

    def __init__(self, freq=30.0, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        """
        Args:
            freq: Estimated signal frequency (Hz) — roughly your FPS.
            min_cutoff: Minimum cutoff frequency. Lower = smoother but laggier.
            beta: Speed coefficient. Higher = less lag during fast moves.
            d_cutoff: Cutoff frequency for derivative smoothing.
        """
        self.freq = freq
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev = None
        self._dx_prev = 0.0

    def _smoothing_factor(self, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        te = 1.0 / self.freq
        return 1.0 / (1.0 + tau / te)

    def _exponential_smoothing(self, alpha, x, x_prev):
        return alpha * x + (1.0 - alpha) * x_prev

    def __call__(self, x):
        if self._x_prev is None:
            self._x_prev = x
            self._dx_prev = 0.0
            return x

        # Estimate derivative (speed)
        dx = (x - self._x_prev) * self.freq
        # Smooth the derivative
        alpha_d = self._smoothing_factor(self.d_cutoff)
        dx_hat = self._exponential_smoothing(alpha_d, dx, self._dx_prev)

        # Adapt cutoff based on speed
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        alpha = self._smoothing_factor(cutoff)
        x_hat = self._exponential_smoothing(alpha, x, self._x_prev)

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat

    def reset(self):
        self._x_prev = None
        self._dx_prev = 0.0


class PointSmoother:
    """Applies independent 1-Euro filters to x and y coordinates."""

    def __init__(self, freq=30.0, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        self.fx = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)
        self.fy = OneEuroFilter(freq, min_cutoff, beta, d_cutoff)

    def __call__(self, x, y):
        return self.fx(x), self.fy(y)

    def reset(self):
        self.fx.reset()
        self.fy.reset()
