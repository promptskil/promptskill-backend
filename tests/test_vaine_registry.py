from unittest.mock import MagicMock, patch

import httpx

from app.services.generate_service import (
    VAINE_LEXICON,
    VAINE_RULE_PROFILES,
    VAINE_TAXONOMY,
    load_vaine_data,
    vaine_health_check,
)


def test_load_vaine_data_populates():
    load_vaine_data()
    assert VAINE_LEXICON.get("entries")
    assert VAINE_TAXONOMY.get("class_boundary_max_chars") == 120
    assert "claude" in VAINE_RULE_PROFILES


def test_vaine_health_check_ok():
    with patch("app.services.generate_service.httpx.get") as m:
        m.return_value = MagicMock(status_code=200)
        ok, elapsed = vaine_health_check()
    assert ok is True and elapsed >= 0


def test_vaine_health_check_handles_error():
    with patch(
        "app.services.generate_service.httpx.get",
        side_effect=httpx.ConnectError("boom"),
    ):
        ok, _ = vaine_health_check()
    assert ok is False
