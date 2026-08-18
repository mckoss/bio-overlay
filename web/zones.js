// Workout-intensity zones from the Tanaka maximum-heart-rate formula.
//
// Direct port of src/bio_overlay/zones.py — see that module for the model.
// Rounding matters: Python's round() is half-to-even ("banker's rounding"),
// so 162.5 -> 162 where JS Math.round gives 163. pyRound() reproduces the
// Python behavior so both versions compute identical divisors.

export const TANAKA_INTERCEPT = 208.0;
export const TANAKA_SLOPE = 0.7;

// Fractions of HRmax at the rest|Z1, Z1|Z2, ... Z4|Z5 and Z5|over-max
// boundaries.
export const DIVISOR_FRACTIONS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0];

// Sanity window for configured birth years.
const MIN_BIRTH_YEAR = 1900;
const MIN_AGE = 5;

// Age assumed when no birth year / max HR is configured.
export const DEFAULT_AGE = 65;

// Bucket count for zone-time accounting: rest + Z1..Z5 + over max.
export const N_ZONE_BUCKETS = DIVISOR_FRACTIONS.length + 1;

/** Round half-to-even, matching Python's round() on .5 boundaries. */
export function pyRound(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** Tanaka HRmax for someone born in birthYear, or null if unknown/implausible. */
export function maxHeartRate(birthYear, year = null) {
  if (birthYear == null) return null;
  year = year ?? new Date().getFullYear();
  if (birthYear < MIN_BIRTH_YEAR || birthYear > year - MIN_AGE) return null;
  const age = year - birthYear;
  return pyRound(TANAKA_INTERCEPT - TANAKA_SLOPE * age);
}

/**
 * Zone description for the overlay: {maxHr, divisors, [assumedAge]}.
 * A configured maxHr overrides the Tanaka estimate. With neither configured
 * (or an implausible birth year), zones assume DEFAULT_AGE and the object
 * carries assumedAge so clients can warn about it.
 */
export function zonesFor(birthYear, maxHr = null, year = null) {
  let hrMax = maxHr || maxHeartRate(birthYear, year);
  const assumed = !hrMax || hrMax <= 0;
  if (assumed) hrMax = pyRound(TANAKA_INTERCEPT - TANAKA_SLOPE * DEFAULT_AGE);
  const zones = {
    maxHr: hrMax,
    divisors: DIVISOR_FRACTIONS.map((f) => pyRound(hrMax * f)),
  };
  if (assumed) zones.assumedAge = DEFAULT_AGE;
  return zones;
}

/**
 * Bucket index for a reading: 0 = rest, 1..5 = Z1..Z5, 6 = over max.
 * Boundaries are inclusive upward (a reading exactly at a divisor belongs to
 * the zone above it), except HRmax itself which still counts as Z5 — matching
 * the renderer's zoneFor().
 */
export function zoneIndex(bpm, divisors) {
  if (bpm > divisors[divisors.length - 1]) return divisors.length;
  for (let i = 0; i < divisors.length; i++) {
    if (bpm < divisors[i]) return i;
  }
  return divisors.length - 1; // bpm == divisors[-1] (HRmax) -> Z5
}
