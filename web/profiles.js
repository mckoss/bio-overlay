// Strap-to-person profiles (name + birth year), persisted in localStorage
// keyed by strap deviceId. Shared by the live page (editing) and the history
// page (zone banding for known participants).

const PROFILES_KEY = "bio-overlay-web.profiles"; // deviceId -> {name, birthYear}

export function loadProfiles() {
  try {
    return JSON.parse(localStorage.getItem(PROFILES_KEY)) || {};
  } catch {
    return {};
  }
}

export function saveProfile(deviceId, profile) {
  const all = loadProfiles();
  all[deviceId] = profile;
  localStorage.setItem(PROFILES_KEY, JSON.stringify(all));
}
