"""ChatGPT fallback — structure-driven format even in degraded path."""


def generate_fallback(topic: str) -> str:
    return (
        f"Format: numbered sections. "
        f"Structure: (1) definition of {topic}, (2) three key sub-areas, "
        f"(3) one concrete example per sub-area. "
        f"Produce a prompt a user could paste into ChatGPT to get a "
        f"well-structured response about {topic}."
    )
