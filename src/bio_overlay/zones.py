"""Workout-intensity zones from the Tanaka maximum-heart-rate formula.

HRmax = 208 - 0.7 x age (Tanaka, Monahan & Seals 2001), unless the participant
configures a measured maximum. Bands follow the standard five-zone training
model (Zone 1 through Zone 5 in 10%-of-HRmax steps), plus "rest" below Zone 1
and "over max" above HRmax:

    rest < 50% <= Z1 < 60% <= Z2 < 70% <= Z3 < 80% <= Z4 < 90% <= Z5 <= 100%
    < over max
"""

from __future__ import annotations

from datetime import datetime

TANAKA_INTERCEPT = 208.0
TANAKA_SLOPE = 0.7

# Fractions of HRmax at the rest|Z1, Z1|Z2, ... Z4|Z5 and Z5|over-max
# boundaries.
DIVISOR_FRACTIONS = (0.50, 0.60, 0.70, 0.80, 0.90, 1.00)

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

    Returns {"maxHr": int, "divisors": [rest|z1, z1|z2, z2|z3, z3|z4,
    z4|z5, z5|over]} — the camelCase shape sent to clients.
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
