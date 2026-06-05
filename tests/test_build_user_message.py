"""build_user_message — refine composition (Layer 7 v2 refine addendum).

Pure function: connects the original topic with an optional refinement so the
model sees both. No DB / no fixtures needed.
"""
from app.services.generate_service import build_user_message

_CONFIG = {"user_message_template": "Generate a prompt about: '{topic}'"}


def test_no_refinement_returns_base():
    msg = build_user_message(_CONFIG, "cats")
    assert msg == "Generate a prompt about: 'cats'"
    assert "refined" not in msg


def test_refinement_includes_both_topic_and_refinement():
    msg = build_user_message(_CONFIG, "cats", "make it concise")
    assert "cats" in msg                 # original topic preserved
    assert "make it concise" in msg      # refinement included
    assert "refined their request" in msg


def test_empty_refinement_treated_as_none():
    assert build_user_message(_CONFIG, "cats", "") == build_user_message(
        _CONFIG, "cats"
    )
