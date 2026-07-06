from app.services.vaine_format_filter import content_words, format_prompt


def test_format_strips_xml_and_markdown():
    out = format_prompt("<task> Do it </task>\n### Heading\n- bullet", {})
    assert "<task>" not in out and "###" not in out and "Do it" in out


def test_format_normalizes_whitespace():
    assert format_prompt("a    b", {}) == "a b"


def test_content_words():
    assert content_words("Hello, World!") == ["hello", "world"]
