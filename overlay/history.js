/*
 * Desktop history page: the shared history UI (history-ui.js) backed by the
 * server's /api/history endpoints.
 */

import { createHistoryPage } from "./history-ui.js";

async function checkOk(res) {
  if (!res.ok) throw new Error(await res.text());
  return res;
}

createHistoryPage({
  listView: document.getElementById("list-view"),
  detailView: document.getElementById("detail-view"),
  sessionsEl: document.getElementById("sessions"),
  detailEl: document.getElementById("detail"),
  backEl: document.getElementById("back"),
  api: {
    async list() {
      return (await checkOk(await fetch("/api/history"))).json();
    },
    async get(id) {
      return (await checkOk(await fetch("/api/history/" + encodeURIComponent(id)))).json();
    },
    async delete(id) {
      await checkOk(await fetch("/api/history/" + encodeURIComponent(id), { method: "DELETE" }));
    },
  },
});
