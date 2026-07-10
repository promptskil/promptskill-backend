from app.services.vaine_format_filter import content_words, format_prompt


def test_format_strips_xml_and_markdown():
    out = format_prompt("<task> Do it </task>\n### Heading\n- bullet", {})
    assert "<task>" not in out and "###" not in out and "Do it" in out


def test_format_normalizes_whitespace():
    assert format_prompt("a    b", {}) == "a b"


def test_content_words():
    assert content_words("Hello, World!") == ["hello", "world"]


def test_numbered_structure():
    out = format_prompt(
        "Create a plan, covering promotion, engagement, and retention",
        {"format_template": {"structure": "numbered"}},
    )
    assert out.startswith("Create a plan:")
    assert "1. promotion" in out and "3. retention" in out


def test_xml_structure():
    out = format_prompt(
        "Create a plan, covering promotion, engagement, and retention",
        {"format_template": {"structure": "xml"}},
    )
    assert "<task>Create a plan</task>" in out and "- promotion" in out


def test_structure_fallback_no_list():
    p = {"format_template": {"structure": "xml"}}
    assert format_prompt("Do it precisely", p) == "<task>Do it precisely</task>"


def test_structure_only_invariant():
    strong = "Create a plan, covering promotion, engagement, and retention"
    out = format_prompt(strong, {"format_template": {"structure": "xml"}})
    extra = set(content_words(out)) - set(content_words(strong))
    assert all(w in {"task", "requirements"} or w.isdigit() for w in extra)
