"""Configuration loading for bio-overlay.

A config file binds participant identities to physical BLE straps. The preferred
binding is `deviceId` — the Polar ID printed on the strap (e.g. "16CD9E3C"),
which also appears in the advertised name "Polar H10 16CD9E3C". It identifies
the physical sensor and is portable across machines.

`address` (the macOS CoreBluetooth UUID) is a Mac-specific fallback; it is not
printed on the strap and can differ on another computer.

Example config.json:

    {
      "staleAfterSeconds": 5.0,
      "participants": [
        {
          "id": "participant-1",
          "displayName": "Alice",
          "deviceId": "16CD9E3C"
        },
        {
          "id": "participant-2",
          "displayName": "Bob",
          "deviceId": null
        }
      ]
    }

If neither `deviceId` nor `address` is set, the collector falls back to matching
the first strap whose advertised name starts with `namePrefix`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NAME_PREFIX = "Polar H10"


@dataclass
class ParticipantConfig:
    id: str
    display_name: str
    # Preferred binding: the Polar device ID printed on the strap (e.g.
    # "16CD9E3C"), matched against the advertised name. Portable across Macs.
    device_id: str | None = None
    # Fallback binding: the macOS CoreBluetooth UUID (Mac-specific, not on the
    # strap). Only used when device_id is unset.
    address: str | None = None
    name_prefix: str = DEFAULT_NAME_PREFIX

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "displayName": self.display_name,
            "deviceId": self.device_id,
            "namePrefix": self.name_prefix,
        }
        if self.address:
            out["address"] = self.address
        return out


@dataclass
class AppConfig:
    participants: list[ParticipantConfig] = field(default_factory=list)
    stale_after_seconds: float = 5.0
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        participants = [
            ParticipantConfig(
                id=p["id"],
                display_name=p.get("displayName", p["id"]),
                device_id=p.get("deviceId"),
                address=p.get("address"),
                name_prefix=p.get("namePrefix", DEFAULT_NAME_PREFIX),
            )
            for p in data.get("participants", [])
        ]
        return cls(
            participants=participants,
            stale_after_seconds=float(data.get("staleAfterSeconds", 5.0)),
            host=data.get("host", "127.0.0.1"),
            port=int(data.get("port", 8080)),
        )

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "staleAfterSeconds": self.stale_after_seconds,
            "participants": [p.to_dict() for p in self.participants],
        }

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str | Path) -> None:
        """Write the config to disk as pretty JSON (atomic replace)."""
        import os

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def default(cls) -> "AppConfig":
        """A sane two-participant default usable without a config file."""
        return cls(
            participants=[
                ParticipantConfig(id="participant-1", display_name="Participant 1"),
                ParticipantConfig(id="participant-2", display_name="Participant 2"),
            ]
        )
