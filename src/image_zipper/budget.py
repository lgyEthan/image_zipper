"""Fit actual encoded bytes, with interpolation instead of a fixed DPI bisection."""

from __future__ import annotations

import math

from .models import Encoded


def fit_scale(candidate, full: Encoded, limit: int, minimum: float) -> tuple[Encoded, float]:
    """Prefer resolution; every returned fitting candidate has been encoded in full.

    The byte/area interpolation is only a proposal. Bracketing handles codec
    discontinuities and metadata overhead without trusting a predicted size.
    Stop at 98.5% utilization or a subpixel interval, never by exceeding the cap.
    """
    if full.size_bytes <= limit:
        return full, 1.0
    best = None
    low, high, low_bytes, high_bytes = 0.0, 1.0, 0, full.size_bytes
    best_scale = minimum
    for _ in range(18):
        fraction = (limit * 0.997 - low_bytes) / max(1, high_bytes - low_bytes)
        estimate = math.sqrt(low * low + max(0, fraction) * (high * high - low * low))
        # Keep making progress when a format has large headers or size jumps.
        scale = max(
            minimum, min(high - (high - low) * 0.08, max(low + (high - low) * 0.08, estimate))
        )
        trial = candidate(scale)
        if trial.size_bytes <= limit:
            best, best_scale = trial, scale
            low, low_bytes = scale, trial.size_bytes
            if trial.size_bytes >= limit * 0.985 or high - low <= minimum:
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
    """Spend spare bytes on quality up to 100, independent of a DPI preset."""
    if best.size_bytes >= limit * 0.985 or floor == 100:
        return best
    top = candidate(100)
    if top.size_bytes <= limit:
        return top
    low, high = floor + 1, 99
    while low <= high:
        quality = (low + high) // 2
        trial = candidate(quality)
        if trial.size_bytes <= limit:
            best, low = trial, quality + 1
            if trial.size_bytes >= limit * 0.985:
                break
        else:
            high = quality - 1
    return best


def fill_quality_gap(candidate, best: Encoded, scale: float, limit: int) -> Encoded:
    """Bridge an integer quality jump with at most 3% less linear resolution."""
    if best.size_bytes >= limit * 0.985 or best.quality is None or best.quality >= 100:
        return best
    quality = best.quality + 1
    low, high = scale * 0.97, scale
    trial = candidate(low, quality)
    if trial.size_bytes > limit or trial.size_bytes <= best.size_bytes:
        return best
    best = trial
    for _ in range(6):
        if best.size_bytes >= limit * 0.985:
            break
        mid = (low + high) / 2
        trial = candidate(mid, quality)
        if trial.size_bytes <= limit:
            best, low = trial, mid
        else:
            high = mid
    return best
