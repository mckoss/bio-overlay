"""Configuration loading for bio-overlay.

A config file binds participant identities to BLE devices. On macOS, BLE devices
are addressed by an opaque CoreBluetooth UUID (not a hardware MAC), so the
`address` field is expected to be that UUID once discovered via `scan`.

Example config.json:

    {
      "staleAfterSeconds": 5.0,
      "participants": [
        {
          "id": "participant-1",
          "displayName": "Alice",
          "address": "0000XXXX-0000-0000-0000-000000000000"
        },
        {
          "id": "participant-2",
          "displayName": "Bob",
          "address": null
        }
      ]
    }

An `address` of null means "not yet bound"; the collector will fall back to
matching by advertised device name prefix (see `name_prefix`).
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
    address: str | None = None
    name_prefix: str = DEFAULT_NAME_PREFIX


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

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def default(cls) -> "AppConfig":
        """A sane two-participant default usable without a config file."""
        return cls(
            participants=[
                ParticipantConfig(id="participant-1", display_name="Participant 1"),
                ParticipantConfig(id="participant-2", display_name="Participant 2"),
            ]
        )
