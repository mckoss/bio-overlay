"""Tests for config serialization and save/load roundtrip."""

from bio_overlay.config import (
    DEFAULT_PORT,
    LEGACY_DEFAULT_PORT,
    AppConfig,
    ParticipantConfig,
)


def test_to_dict_roundtrip():
    cfg = AppConfig(
        participants=[
            ParticipantConfig(
                id="mike-koss", display_name="Mike", device_id="16CD9E3C", birth_year=1960
            ),
            ParticipantConfig(
                id="debbie-koss", display_name="Debbie", device_id="16CDAA3B", max_hr=165
            ),
        ],
        port=8085,
    )
    again = AppConfig.from_dict(cfg.to_dict())
    assert [p.id for p in again.participants] == ["mike-koss", "debbie-koss"]
    assert again.participants[0].device_id == "16CD9E3C"
    assert again.participants[0].birth_year == 1960
    assert again.participants[0].max_hr is None
    assert again.participants[1].birth_year is None
    assert again.participants[1].max_hr == 165
    assert again.port == 8085


def test_save_and_load(tmp_path):
    cfg = AppConfig(
        participants=[ParticipantConfig(id="p1", display_name="One", device_id="ABC")]
    )
    path = tmp_path / "config.json"
    cfg.save(path)
    assert path.exists()

    loaded = AppConfig.load(path)
    assert loaded.participants[0].id == "p1"
    assert loaded.participants[0].device_id == "ABC"


def test_to_dict_omits_unset_address():
    p = ParticipantConfig(id="p1", display_name="One", device_id="ABC")
    assert "address" not in p.to_dict()
    p2 = ParticipantConfig(id="p2", display_name="Two", address="UUID-123")
    assert p2.to_dict()["address"] == "UUID-123"


def test_port_defaults_to_the_apps_own_port():
    """A config with no port uses the default, so the default can move."""
    cfg = AppConfig.from_dict({"participants": []})
    assert cfg.port == DEFAULT_PORT
    assert cfg.port_migrated is False


def test_legacy_default_port_is_migrated():
    """A saved 8080 means "never chose a port" — configs written before 2.0 all
    carry it, and leaving them on the contested default defeats the point."""
    cfg = AppConfig.from_dict({"port": LEGACY_DEFAULT_PORT, "participants": []})
    assert cfg.port == DEFAULT_PORT
    assert cfg.port_migrated is True


def test_explicitly_chosen_port_survives():
    cfg = AppConfig.from_dict({"port": 9123, "participants": []})
    assert cfg.port == 9123
    assert cfg.port_migrated is False


def test_port_migration_flag_is_not_persisted():
    cfg = AppConfig.from_dict({"port": LEGACY_DEFAULT_PORT, "participants": []})
    assert "port_migrated" not in cfg.to_dict()
    assert AppConfig.from_dict(cfg.to_dict()).port_migrated is False
