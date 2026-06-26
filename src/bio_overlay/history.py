"""Daily on-disk history of collected biometric readings (JSON Lines).

One append-only file per day, ``YYYY-MM-DD.jsonl`` (git-ignored). A session
header line lists the participants and an absolute start time; each later data
line carries a time offset in whole seconds (``s``) and the participant's index
(``p``) into that header — so lines stay tiny with no repeated metadata:

    {"session": "2026-06-26T10:15:03.123-07:00",
     "participants": [{"id": "mike-koss", "name": "Mike", "deviceId": "16CD9E3C"}]}
    {"s": 0, "p": 0, "bpm": 78, "rr": [776.4]}
    {"s": 5, "p": 0, "bpm": 80, "rr": [751.0, 769.2, 742.1, 760.5, 733.8]}

A new session header is written at startup, on a live config change, and at
midnight rollover, so each file is self-describing. ``read_records`` resolves the
header + offsets back into absolute records.

Readings arrive ~1/s, but the writer keeps at most one data line per participant
every ``min_interval_s`` seconds (5s → 12/min). RR intervals between lines are
batched into the next line so no beat-to-beat data is lost. Each line is flushed
as written, so a crash loses at most the last buffered line. Only real collected
data is recorded; the simulator does not use this writer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .telemetry import ParticipantState

logger = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_S = 5.0


def read_records(directory: str | Path, date_str: str) -> list[dict]:
    """Return the day's readings as absolute records: {t, p, bpm, rr}.

    Resolves the compact ``session`` header + ``s``/``p`` (relative seconds and
    participant index) layout into absolute ISO timestamps and participant ids,
    so callers don't deal with the on-disk format.
    """
    path = Path(directory) / f"{date_str}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    session_start: datetime | None = None
    ids: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # torn final line from a crash
            if "session" in rec:
                try:
                    session_start = datetime.fromisoformat(rec["session"])
                except (ValueError, TypeError):
                    session_start = None
                ids = [p.get("id") for p in rec.get("participants", [])]
                continue
            if session_start is None or "s" not in rec or "p" not in rec:
                continue
            idx = rec["p"]
            if not isinstance(idx, int) or idx < 0 or idx >= len(ids):
                continue
            t = session_start + timedelta(seconds=rec["s"])
            out.append(
                {
                    "t": t.isoformat(timespec="milliseconds"),
                    "p": ids[idx],
                    "bpm": rec.get("bpm"),
                    "rr": rec.get("rr") or [],
                }
            )
    except OSError as exc:
        logger.warning("could not read history %s: %s", path, exc)
    return out


def _parse_sessions(path: Path) -> list[dict]:
    """Split one day's file into sessions: each header line starts a new one."""
    sessions: list[dict] = []
    cur: dict | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "session" in rec:
                cur = {
                    "start": rec.get("session"),
                    "participants": rec.get("participants", []),
                    "data": [],
                }
                sessions.append(cur)
            elif cur is not None and "s" in rec and "p" in rec:
                cur["data"].append(rec)
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
    return sessions


def list_sessions(directory: str | Path) -> list[dict]:
    """Summaries of all recorded sessions, newest first."""
    out: list[dict] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        date = path.stem
        for i, sess in enumerate(_parse_sessions(path)):
            data = sess["data"]
            offs = [d["s"] for d in data if isinstance(d.get("s"), (int, float))]
            if not offs:
                continue  # skip empty sessions (header but no readings)
            parts = sess["participants"]
            present = sorted({d["p"] for d in data if isinstance(d.get("p"), int)})
            names = [
                (parts[p].get("name") or parts[p].get("id"))
                for p in present
                if 0 <= p < len(parts)
            ]
            out.append(
                {
                    "id": f"{date}__{i}",
                    "date": date,
                    "startedAt": sess["start"],
                    "durationS": max(offs) - min(offs),
                    "participants": names,
                    "samples": len(data),
                }
            )
    out.sort(key=lambda s: s.get("startedAt") or "", reverse=True)
    return out


def load_session(directory: str | Path, session_id: str) -> dict | None:
    """Full detail for one session: per-participant bpm series and stats."""
    date, sep, idx_s = session_id.partition("__")
    if not sep:
        return None
    try:
        idx = int(idx_s)
    except ValueError:
        return None
    path = Path(directory) / f"{date}.jsonl"
    if not path.exists():
        return None
    sessions = _parse_sessions(path)
    if idx < 0 or idx >= len(sessions):
        return None

    sess = sessions[idx]
    parts = sess["participants"]
    per: dict[int, dict] = {}
    for d in sess["data"]:
        p, s, bpm = d.get("p"), d.get("s"), d.get("bpm")
        if not isinstance(p, int) or not isinstance(s, (int, float)) or bpm is None:
            continue
        entry = per.setdefault(p, {"points": [], "bpms": []})
        entry["points"].append([s, bpm])
        entry["bpms"].append(bpm)

    participants = []
    for p in sorted(per):
        meta = parts[p] if 0 <= p < len(parts) else {}
        bpms = per[p]["bpms"]
        participants.append(
            {
                "id": meta.get("id"),
                "name": meta.get("name") or meta.get("id") or f"#{p}",
                "deviceId": meta.get("deviceId"),
                "points": per[p]["points"],
                "stats": {
                    "min": min(bpms),
                    "max": max(bpms),
                    "avg": round(sum(bpms) / len(bpms)),
                    "count": len(bpms),
                },
            }
        )

    offs = [d["s"] for d in sess["data"] if isinstance(d.get("s"), (int, float))]
    return {
        "id": session_id,
        "date": date,
        "startedAt": sess["start"],
        "durationS": (max(offs) - min(offs)) if offs else 0,
        "participants": participants,
    }


@dataclass
class _Pending:
    last_ms: int | None = None
    rr: list[float] = field(default_factory=list)
    bpm: int = 0
    at: datetime | None = None


class DailyHistoryWriter:
    def __init__(
        self, directory: str | Path, min_interval_s: float = DEFAULT_MIN_INTERVAL_S
    ) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._min_interval_ms = int(min_interval_s * 1000)
        self._date: str | None = None
        self._fh = None
        self._pending: dict[str, _Pending] = {}
        self._meta: list[dict] = []
        self._index: dict[str, int] = {}
        self._session_start: datetime | None = None
        self._need_header = False

    # -- public API -------------------------------------------------------

    def start_session(self, participants) -> None:
        """Record the participant set; a session header is written before the
        next data line (anchored to that reading's time). Call at startup and
        whenever the config changes."""
        self._meta = [
            {"id": p.id, "name": p.display_name, "deviceId": p.device_id}
            for p in participants
        ]
        self._index = {p.id: i for i, p in enumerate(participants)}
        self._need_header = True
        self._session_start = None

    def record(
        self,
        state: ParticipantState,
        bpm: int,
        rr_intervals_ms: list[float],
        at: datetime,
    ) -> None:
        """Buffer one reading; write a line at most once per interval per participant."""
        date_str = at.strftime("%Y-%m-%d")
        if date_str != self._date:
            self._rollover(date_str)
        if self._need_header:
            self._write_header(at)

        at_ms = int(at.timestamp() * 1000)
        pend = self._pending.setdefault(state.participant_id, _Pending())
        pend.rr.extend(rr_intervals_ms or [])
        pend.bpm = bpm
        pend.at = at
        if pend.last_ms is None or at_ms - pend.last_ms >= self._min_interval_ms:
            self._write_line(state.participant_id, pend)
            pend.last_ms = at_ms
            pend.rr = []

    def start(self) -> None:  # kept for API symmetry; nothing to schedule
        pass

    async def close(self) -> None:
        for pid, pend in self._pending.items():
            if pend.rr and pend.at is not None:
                self._write_line(pid, pend)
                pend.rr = []
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def path_for(self, date_str: str) -> Path:
        return self._dir / f"{date_str}.jsonl"

    # -- internals --------------------------------------------------------

    def _rollover(self, date_str: str) -> None:
        if self._fh is not None:
            self._fh.close()
        self._date = date_str
        self._pending.clear()
        self._need_header = True  # re-describe the session in the new file
        self._session_start = None
        try:
            self._fh = open(self.path_for(date_str), "a", encoding="utf-8")
        except OSError as exc:
            logger.warning("could not open history %s: %s", self.path_for(date_str), exc)
            self._fh = None

    def _write_header(self, at: datetime) -> None:
        self._session_start = at
        self._need_header = False
        self._emit({"session": at.isoformat(timespec="milliseconds"), "participants": self._meta})

    def _write_line(self, participant_id: str, pend: _Pending) -> None:
        idx = self._index.get(participant_id)
        if idx is None or pend.at is None or self._session_start is None:
            return
        secs = int((pend.at - self._session_start).total_seconds())
        rec = {"s": max(0, secs), "p": idx, "bpm": pend.bpm}
        if pend.rr:
            rec["rr"] = [round(x, 1) for x in pend.rr]
        self._emit(rec)

    def _emit(self, rec: dict) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(rec) + "\n")
            self._fh.flush()
        except OSError as exc:
            logger.warning("failed to write history line: %s", exc)
