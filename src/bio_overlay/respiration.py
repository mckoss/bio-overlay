"""Experimental respiration-rate estimation from RR intervals.

Breathing modulates heart rate (respiratory sinus arrhythmia, RSA): the heart
speeds up on inhalation and slows on exhalation. That shows up as a periodic
oscillation of the RR-interval series at the breathing frequency. We recover it
by building the RR tachogram, resampling to a uniform grid, removing the slow
trend, and finding the dominant spectral peak in the respiratory band.

This is **experimental**. RSA is strong at rest but weakens during hard exercise
(sympathetic drive dominates), so estimates degrade exactly when effort is high.
The estimate is always reported with a confidence in [0, 1] so callers can hide
or de-emphasize low-confidence values rather than show misleading numbers.

Pure and dependency-light (numpy only) so it can be unit-tested against a
synthetic RR series with a known breathing frequency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

# Respiratory band: 0.1-0.5 Hz == 6-30 breaths/min.
RESP_BAND_HZ = (0.1, 0.5)
# Uniform resample rate for the tachogram (Hz). 4 Hz comfortably covers the band.
RESAMPLE_HZ = 4.0
# Physiologically plausible RR range (ms); values outside are treated as artifacts.
RR_MIN_MS = 300.0
RR_MAX_MS = 2000.0
# Reject beats whose RR jumps more than this fraction from the running median
# (ectopic / missed beats).
ARTIFACT_REL_JUMP = 0.30

# Minimum data needed before we attempt an estimate.
MIN_BEATS = 20
MIN_SPAN_S = 30.0


@dataclass
class RespirationEstimate:
    breaths_per_min: float
    confidence: float  # 0..1: peak power as a fraction of in-band power


def _clean_rr(rr_ms: Sequence[float]) -> list[float]:
    """Drop out-of-range and abrupt (ectopic) RR values."""
    cleaned: list[float] = []
    median = None
    for rr in rr_ms:
        if rr < RR_MIN_MS or rr > RR_MAX_MS:
            continue
        if median is not None and abs(rr - median) > ARTIFACT_REL_JUMP * median:
            continue
        cleaned.append(rr)
        # Running median over a short tail keeps the reference adaptive.
        tail = cleaned[-8:]
        median = float(np.median(tail))
    return cleaned


def estimate_respiration(rr_ms: Sequence[float]) -> RespirationEstimate | None:
    """Estimate breathing rate from a window of consecutive RR intervals.

    `rr_ms` is the recent RR-interval series (oldest first), in milliseconds.
    Returns None when there isn't enough clean data to make an estimate.
    """
    rr = _clean_rr(rr_ms)
    if len(rr) < MIN_BEATS:
        return None

    rr_arr = np.asarray(rr, dtype=float)
    # Beat times are the cumulative sum of RR intervals (seconds).
    beat_times = np.cumsum(rr_arr) / 1000.0
    beat_times -= beat_times[0]
    span_s = float(beat_times[-1])
    if span_s < MIN_SPAN_S:
        return None

    # Resample the tachogram onto a uniform grid.
    n = int(span_s * RESAMPLE_HZ)
    if n < 8:
        return None
    grid = np.linspace(0.0, span_s, n, endpoint=False)
    resampled = np.interp(grid, beat_times, rr_arr)

    # Remove DC + slow trend, then window to reduce spectral leakage.
    detrended = resampled - np.polyval(np.polyfit(grid, resampled, 1), grid)
    windowed = detrended * np.hanning(len(detrended))

    spectrum = np.abs(np.fft.rfft(windowed)) ** 2
    freqs = np.fft.rfftfreq(len(windowed), d=1.0 / RESAMPLE_HZ)

    band = (freqs >= RESP_BAND_HZ[0]) & (freqs <= RESP_BAND_HZ[1])
    if not np.any(band):
        return None

    band_power = spectrum[band]
    band_freqs = freqs[band]
    peak_idx = int(np.argmax(band_power))
    peak_freq = float(band_freqs[peak_idx])

    total = float(np.sum(band_power))
    confidence = float(band_power[peak_idx] / total) if total > 0 else 0.0

    return RespirationEstimate(
        breaths_per_min=round(peak_freq * 60.0, 1),
        confidence=round(confidence, 3),
    )
