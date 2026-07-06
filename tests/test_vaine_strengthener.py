from app.services.vaine_strengthener import new_tokens, strengthen

_PROFILE = {
    "strength_set": {"good": "rigorous", "warm": "heartfelt", "a lot": "substantially"},
    "strip_only": ["very", "thing"],
}
_LEXICON = {
    "entries": [
        {"base_term": "good", "pos": "adjective"},
        {"base_term": "warm", "pos": "adjective"},
        {"base_term": "a lot", "pos": "phrase"},
    ],
}


def test_strengthen_substitutes_adjectives_and_strips():
    assert strengthen("a good warm thing", _PROFILE, _LEXICON) == "a rigorous heartfelt"


def test_strengthen_preserves_case():
    assert strengthen("Good", _PROFILE, _LEXICON) == "Rigorous"


def test_strengthen_leaves_phrases_untouched():
    out = strengthen("a lot of good", _PROFILE, _LEXICON)
    assert "substantially" not in out and "rigorous" in out


def test_new_tokens_invariant_holds():
    src = "a good thing"
    out = strengthen(src, _PROFILE, _LEXICON)
    assert new_tokens(src, out, ["rigorous"]) == set()
