"""Daily on-disk history of collected biometric readings.

Writes one JSON file per day, named ``YYYY-MM-DD.json``, into a flat history
directory (git-ignored). Each file is a JSON array of reading records:

    [
      {
        "t": "2026-06-26T10:15:03.123-07:00",
        "participantId": "mike-koss",
        "deviceId": "16CD9E3C",
        "bpm": 78,
        "rrIntervalsMs": [776.4]
      },
      ...
    ]

Records are buffered in memory and flushed atomically (temp file + rename) on a
debounced background task and on close, so a crash can't leave a half-written
file. If a file for the current day already exists (e.g. the server was
restarted), it is loaded and appended to rather than overwritten. The writer
rolls over to a new file automatically at midnight.

Only real collected data is recorded; the simulator does not use this writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from .telemetry import ParticipantState

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_INTERVAL_S = 5.0


class DailyHistoryWriter:
    def __init__(
        self, directory: str | Path, flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S
    ) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval_s = flush_interval_s
        self._date: str | None = None
        self._records: list[dict] = []
        self._dirty = False
        self._flush_task: asyncio.Task | None = None

    # -- public API -------------------------------------------------------

    def record(
        self,
        state: ParticipantState,
        bpm: int,
        rr_intervals_ms: list[float],
        at: datetime,
    ) -> None:
        """Append one reading. Matches the telemetry Recorder signature."""
        date_str = at.strftime("%Y-%m-%d")
        if date_str != self._date:
            self._rollover(date_str)
        self._records.append(
            {
                "t": at.isoformat(timespec="milliseconds"),
                "participantId": state.participant_id,
                "deviceId": state.device_id,
                "bpm": bpm,
                "rrIntervalsMs": list(rr_intervals_ms),
            }
        )
        self._dirty = True

    def start(self) -> None:
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def close(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        self.flush()

    def path_for(self, date_str: str) -> Path:
        return self._dir / f"{date_str}.json"

    # -- internals --------------------------------------------------------

    def _rollover(self, date_str: str) -> None:
        # Flush the previous day before switching files.
        if self._dirty:
            self.flush()
        self._date = date_str
        path = self.path_for(date_str)
        if path.exists():
            try:
                self._records = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                logger.warning("could not read %s, starting fresh: %s", path, exc)
                self._records = []
        else:
            self._records = []
        self._dirty = False

    def flush(self) -> None:
        if not self._dirty or self._date is None:
            return
        path = self.path_for(self._date)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._records, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(tmp, path)  # atomic on POSIX
            self._dirty = False
        except OSError as exc:
            logger.warning("failed to write history %s: %s", path, exc)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            self.flush()
