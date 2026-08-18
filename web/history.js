// Web history page: the shared history UI (overlay/history-ui.js) backed by
// the readings recorded in IndexedDB by the live page. Zone banding comes
// from the strap profiles in localStorage; straps without a profile fall
// back to the default assumed-age zones, matching the desktop server.

import { createHistoryPage } from "./overlay/history-ui.js";
import { openDb, getAllReadings, deleteReadings } from "./db.js";
import { loadProfiles } from "./profiles.js";
import {
  listSessions, sessionDetail, sessionKeyOf, sessionToJsonl,
} from "./history-data.js";
import { zonesFor } from "./zones.js";

const dbPromise = openDb();

function zonesForPid(pid) {
  const profile = loadProfiles()[pid];
  return zonesFor(profile?.birthYear ?? null, profile?.maxHr ?? null);
}

createHistoryPage({
  listView: document.getElementById("list-view"),
  detailView: document.getElementById("detail-view"),
  sessionsEl: document.getElementById("sessions"),
  detailEl: document.getElementById("detail"),
  backEl: document.getElementById("back"),
  api: {
    async list() {
      const rows = await getAllReadings(await dbPromise);
      return { sessions: listSessions(rows, (pid) => zonesForPid(pid).divisors) };
    },
    async get(id) {
      const rows = await getAllReadings(await dbPromise);
      const session = sessionDetail(rows, id, zonesForPid);
      if (!session) throw new Error("session not found");
      return session;
    },
    async delete(id) {
      await deleteReadings(await dbPromise, (row) => sessionKeyOf(row) === id);
    },
    // Save one session as a file in the desktop history format.
    async download(s) {
      const rows = await getAllReadings(await dbPromise);
      const text = sessionToJsonl(rows, s.id);
      if (!text) return;
      const stamp = new Date(s.startedAt).toISOString()
        .slice(0, 16).replace("T", "-").replace(":", "");
      const blob = new Blob([text], { type: "application/jsonl" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `bio-overlay-${stamp}.jsonl`;
      a.click();
      URL.revokeObjectURL(a.href);
    },
  },
});
