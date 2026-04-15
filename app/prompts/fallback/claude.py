"""Claude fallback — used when Anthropic returns APIStatusError (500).

Topic IS injected (per spec). Fallback system_prompt_version = 'fallback'
is written at the prompts table row level by generate_service, not here.
"""


def generate_fallback(topic: str) -> str:
    return (
        f"Context: You are helping a user think through the topic \"{topic}\". "
        f"Constraints: stay grounded, avoid speculation, favor layered reasoning. "
        f"Ask: produce a single focused, high-quality prompt a user could paste "
        f"into Claude to get the best possible answer about {topic}."
    )
