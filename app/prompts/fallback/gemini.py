"""Gemini fallback — synthesis-driven even in degraded path."""


def generate_fallback(topic: str) -> str:
    return (
        f"Synthesize across multiple perspectives on {topic}. "
        f"Angles to cover: technical, practical, historical, future-looking. "
        f"Request supporting research or authoritative sources. "
        f"Produce a prompt a user could paste into Gemini to get a broad, "
        f"well-researched synthesis on {topic}."
    )
