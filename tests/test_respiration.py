"""Validation tests for the respiration estimator.

These synthesize an RR-interval series with a known breathing frequency (RSA
modulation) and confirm the estimator recovers it. This validates the algorithm
math; real-world accuracy during exercise still needs field validation.
"""

import math

import pytest

from bio_overlay.respiration import estimate_respiration


def _synthetic_rr(breaths_per_min, *, mean_bpm=70, amp_ms=40.0, duration_s=60.0):
    """Generate an RR series whose RR oscillates at the given breathing rate."""
    mean_rr = 60000.0 / mean_bpm  # ms
    f_resp = breaths_per_min / 60.0  # Hz
    rr = []
    t = 0.0
    while t < duration_s:
        # RR modulated sinusoidally by breathing (RSA).
        value = mean_rr + amp_ms * math.sin(2 * math.pi * f_resp * t)
        rr.append(value)
        t += value / 1000.0
    return rr


@pytest.mark.parametrize("brpm", [10, 12, 15, 20])
def test_recovers_known_breathing_rate(brpm):
    rr = _synthetic_rr(brpm)
    est = estimate_respiration(rr)
    assert est is not None
    # Recover within ~1.5 br/min and with high confidence on a clean signal.
    assert abs(est.breaths_per_min - brpm) <= 1.5
    assert est.confidence > 0.3


def test_returns_none_with_too_few_beats():
    assert estimate_respiration([800.0] * 5) is None


def test_returns_none_with_short_span():
    # Many beats but only a few seconds of data.
    assert estimate_respiration([300.0] * 25) is None


def test_artifacts_are_filtered_but_estimate_survives():
    rr = _synthetic_rr(15)
    # Inject a couple of obvious artifacts (missed/ectopic beats).
    rr[10] = 2500.0  # too long
    rr[20] = 150.0  # too short
    est = estimate_respiration(rr)
    assert est is not None
    assert abs(est.breaths_per_min - 15) <= 2.0
