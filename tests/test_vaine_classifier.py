from app.services.vaine_classifier import classify, threshold_from_taxonomy


def test_classify_boundary():
    assert classify("x" * 120) == "simple"
    assert classify("x" * 121) == "complex"


def test_classify_trims_before_measuring():
    assert classify("  " + "x" * 118 + "  ") == "simple"


def test_threshold_from_taxonomy():
    assert threshold_from_taxonomy({"class_boundary_max_chars": 90}) == 90
    assert threshold_from_taxonomy({}) == 120  # fallback
