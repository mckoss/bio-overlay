// IndexedDB storage for readings, shared by the live page (write-through)
// and the history page (review/delete). One row per valid reading:
//   {day, sessionStart, pid, name, index, atMs, bpm, rr}

export const DB_NAME = "bio-overlay-web";
export const DB_STORE = "readings";

export function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const store = req.result.createObjectStore(DB_STORE, { autoIncrement: true });
      store.createIndex("day", "day");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function addReading(db, row) {
  db.transaction(DB_STORE, "readwrite").objectStore(DB_STORE).add(row);
}

export function getAllReadings(db, day = null) {
  return new Promise((resolve, reject) => {
    const store = db.transaction(DB_STORE).objectStore(DB_STORE);
    const req = day ? store.index("day").getAll(day) : store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Delete every row for which predicate(row) is true. Resolves with the count. */
export function deleteReadings(db, predicate) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    const req = tx.objectStore(DB_STORE).openCursor();
    let deleted = 0;
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) return;
      if (predicate(cursor.value)) {
        cursor.delete();
        deleted += 1;
      }
      cursor.continue();
    };
    tx.oncomplete = () => resolve(deleted);
    tx.onerror = () => reject(tx.error);
  });
}

/** Local calendar day for a timestamp, as YYYY-MM-DD (matches history filenames). */
export function localDay(atMs) {
  const d = new Date(atMs);
  return (
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-` +
    String(d.getDate()).padStart(2, "0")
  );
}
