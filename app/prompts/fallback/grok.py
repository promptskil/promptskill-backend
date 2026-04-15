"""Grok fallback — direct, no preamble even in degraded path."""


def generate_fallback(topic: str) -> str:
    return f"Explain {topic}. Direct. No preamble. No filler."
