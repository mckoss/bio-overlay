"""Tests for config serialization and save/load roundtrip."""

from bio_overlay.config import AppConfig, ParticipantConfig


def test_to_dict_roundtrip():
    cfg = AppConfig(
        participants=[
            ParticipantConfig(id="mike-koss", display_name="Mike", device_id="16CD9E3C"),
            ParticipantConfig(id="debbie-koss", display_name="Debbie", device_id="16CDAA3B"),
        ],
        port=8085,
    )
    again = AppConfig.from_dict(cfg.to_dict())
    assert [p.id for p in again.participants] == ["mike-koss", "debbie-koss"]
    assert again.participants[0].device_id == "16CD9E3C"
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
