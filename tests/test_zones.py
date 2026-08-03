"""Tests for Tanaka HRmax and workout-intensity zone computation."""

from bio_overlay.zones import max_heart_rate, zone_index, zones_for


def test_tanaka_max_heart_rate():
    # Age 66: 208 - 0.7 * 66 = 161.8 -> 162
    assert max_heart_rate(1960, year=2026) == 162
    # Age 30: 208 - 21 = 187
    assert max_heart_rate(1996, year=2026) == 187


def test_implausible_birth_year_rejected():
    assert max_heart_rate(None) is None
    assert max_heart_rate(65, year=2026) is None  # someone typed an age
    assert max_heart_rate(2025, year=2026) is None  # infant
    assert max_heart_rate(1899, year=2026) is None


def test_zones_divisors_at_standard_five_zone_percentages():
    z = zones_for(None, max_hr=160)
    assert z == {"maxHr": 160, "divisors": [80, 96, 112, 128, 144, 160]}


def test_zones_from_birth_year():
    z = zones_for(1960, year=2026)  # HRmax 162
    assert z["maxHr"] == 162
    assert z["divisors"] == [81, 97, 113, 130, 146, 162]


def test_explicit_max_overrides_formula():
    assert zones_for(1960, max_hr=175, year=2026)["maxHr"] == 175


def test_no_config_no_zones():
    assert zones_for(None) is None
    assert zones_for(None, max_hr=0) is None


def test_zone_index_buckets():
    d = zones_for(None, max_hr=160)["divisors"]  # [80, 96, 112, 128, 144, 160]
    assert zone_index(70, d) == 0  # rest
    assert zone_index(80, d) == 1  # boundary is inclusive upward
    assert zone_index(95, d) == 1
    assert zone_index(96, d) == 2
    assert zone_index(130, d) == 4
    assert zone_index(159, d) == 5
    assert zone_index(160, d) == 5  # HRmax itself is still Z5
    assert zone_index(161, d) == 6  # over max
