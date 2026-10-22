"""Fit actual encoded bytes, with interpolation instead of a fixed DPI bisection."""

from __future__ import annotations

import math

from .models import Encoded

ENOUGH = 0.90


def fit_scale(candidate, full: Encoded, limit: int, minimum: float) -> tuple[Encoded, float]:
    """Prefer resolution; every returned fitting candidate has been encoded in full.

    The byte/area interpolation is only a proposal. Bracketing handles codec
    discontinuities and metadata overhead without trusting a predicted size.
    Stop at 90% utilization or a subpixel interval, never by exceeding the cap.
    Spending the last few percent is not worth more full image encodes.
    """
    if full.size_bytes <= limit:
        return full, 1.0
    best = None
    low, high, low_bytes, high_bytes = 0.0, 1.0, 0, full.size_bytes
    best_scale = minimum
    for _ in range(8):
        fraction = (limit * 0.96 - low_bytes) / max(1, high_bytes - low_bytes)
        estimate = math.sqrt(low * low + max(0, fraction) * (high * high - low * low))
        # Keep making progress when a format has large headers or size jumps.
        scale = max(
            minimum, min(high - (high - low) * 0.08, max(low + (high - low) * 0.08, estimate))
        )
        trial = candidate(scale)
        if trial.size_bytes <= limit:
            best, best_scale = trial, scale
            low, low_bytes = scale, trial.size_bytes
            if trial.size_bytes >= limit * ENOUGH or high - low <= minimum:
                break
        else:
            high, high_bytes = scale, trial.size_bytes
            if high - low <= minimum:
                break
    if best is None:
        best, best_scale = candidate(minimum), minimum
        best.target_met = best.size_bytes <= limit
    return best, best_scale


def fit_quality(candidate, best: Encoded, floor: int, limit: int) -> Encoded:
    """Improve clearly undersized results with at most four additional encodes."""
    if best.size_bytes >= limit * ENOUGH or floor == 100:
        return best
    low, high = floor + 1, 100
    for _ in range(4):
        if low > high:
            break
        quality = (low + high + 1) // 2
        trial = candidate(quality)
        if trial.size_bytes <= limit:
            best, low = trial, quality + 1
            if trial.size_bytes >= limit * ENOUGH:
                break
        else:
            high = quality - 1
    return best
