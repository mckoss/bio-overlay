"""Workout-intensity zones from the Tanaka maximum-heart-rate formula.

HRmax = 208 - 0.7 x age (Tanaka, Monahan & Seals 2001), unless the participant
configures a measured maximum. Bands are named rest / light / medium / heavy,
with everything above HRmax reported as "over max". The divisors between bands
sit at fixed fractions of HRmax (roughly geometrically spaced, per design):

    rest < 50% <= light < 65% <= medium < 80% <= heavy <= 100% < over max
"""

from __future__ import annotations

from datetime import datetime

TANAKA_INTERCEPT = 208.0
TANAKA_SLOPE = 0.7

# Fractions of HRmax at the rest|light, light|medium, medium|heavy and
# heavy|over-max boundaries.
DIVISOR_FRACTIONS = (0.50, 0.65, 0.80, 1.00)

# Sanity window for configured birth years (a typo like "65" would otherwise
# produce a nonsense negative HRmax).
_MIN_BIRTH_YEAR = 1900
_MIN_AGE = 5


def max_heart_rate(birth_year: int | None, year: int | None = None) -> int | None:
    """Tanaka HRmax for someone born in `birth_year`, or None if unknown/implausible."""
    if birth_year is None:
        return None
    year = year if year is not None else datetime.now().year
    if birth_year < _MIN_BIRTH_YEAR or birth_year > year - _MIN_AGE:
        return None
    age = year - birth_year
    return round(TANAKA_INTERCEPT - TANAKA_SLOPE * age)


def zones_for(
    birth_year: int | None, max_hr: int | None = None, year: int | None = None
) -> dict | None:
    """Zone description for the overlay, or None when nothing is configured.

    Returns {"maxHr": int, "divisors": [rest|light, light|medium,
    medium|heavy, heavy|over]} — the camelCase shape sent to clients.
    A configured max_hr overrides the Tanaka estimate.
    """
    hr_max = max_hr if max_hr else max_heart_rate(birth_year, year=year)
    if not hr_max or hr_max <= 0:
        return None
    return {
        "maxHr": hr_max,
        "divisors": [round(hr_max * f) for f in DIVISOR_FRACTIONS],
    }


__all__ = ["max_heart_rate", "zones_for", "DIVISOR_FRACTIONS"]
