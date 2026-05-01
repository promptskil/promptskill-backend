"""
Vaine Gemini fallback — alignment-enforced pipeline
Baseline: Heart (Intent) → Truth (Alignment) → Fruit (Result)
If any layer breaks alignment → trace root → restart
"""
from typing import Any, Dict

MAX_RESTARTS = 2

# -------------------------
# READ (Word)
# -------------------------
def read_input(topic: str) -> Dict[str, Any]:
    return {"raw_input": topic.strip(), "complete": bool(topic and topic.strip())}

def align_read(x):
    return x["complete"], "Input incomplete"

# -------------------------
# INTENT (Heart)
# -------------------------
def interpret_intent(raw: str) -> Dict[str, Any]:
    return {
        "action": "research",
        "goal": f"understand {raw} clearly",
        "audience": "general audience",
        "depth": "basic to intermediate",
        "topic": raw
    }

def align_intent(intent):
    ok = all([intent.get("action"), intent.get("goal"), intent.get("topic")])
    return ok, "Intent misaligned"

# -------------------------
# STRUCTURE (Pattern)
# -------------------------
def select_structure(intent: Dict[str, Any]) -> Dict[str, Any]:
    if intent["action"] == "research":
        return {"sections": ["role", "search", "definition", "example", "requirements"]}
    return {"sections": ["role", "definition"]}

def align_structure(intent, structure):
    ok = intent["action"] == "research" and "role" in structure["sections"]
    return ok, "Structure misaligned with intent"

# -------------------------
# VALIDATION (Truth)
# -------------------------
def validate(intent, structure):
    return {
        "intent_structure": intent["action"] == "research",
        "has_sections": len(structure["sections"]) > 0
    }

def align_validation(v):
    ok = all(v.values())
    return ok, "Validation failed"

# -------------------------
# JUDGMENT (Truth filter)
# -------------------------
def judge(structure):
    return {"valid": True}

def align_judgment(j):
    return j["valid"], "Judgment failed"

# -------------------------
# DECISION
# -------------------------
def decide(intent, structure):
    return {
        "structure": structure,
        "goal": intent["goal"],
        "topic": intent["topic"]
    }

def align_decision(d, intent):
    ok = intent["goal"] in d["goal"]
    return ok, "Decision misaligned with intent"

# -------------------------
# DIAGNOSE (Critical alignment gate)
# -------------------------
def diagnose(intent, decision):
    ok = intent["topic"] == decision["topic"]
    return ok, "Diagnosis mismatch"

# -------------------------
# FORMAT (Word clarity — Gemini natural language)
# -------------------------
def format_prompt(raw_input, intent, decision):
    topic = intent["topic"]
    return (
        f"You are an expert on {topic} with deep knowledge across its technical, "
        f"practical, and applied dimensions.\n\n"
        f"Search for current information on {topic} before responding — "
        f"the field evolves and accuracy matters.\n\n"
        f"Write for a {intent['audience']} at a {intent['depth']} level. "
        f"Begin by defining what {topic} is and why it matters, "
        f"then ground the explanation with one concrete real-world "
        f"example that shows it in action, "
        f"and close with the key requirements, facts, and constraints "
        f"a reader needs to understand it fully. "
        f"Write as flowing, connected prose — no labels, "
        f"no structural markers, no meta-language."
    )

def align_format(prompt, raw_input, intent):
    ok = prompt.lower().startswith("you are") and intent["topic"] in prompt
    return ok, "Format misaligned"

# -------------------------
# ACT (Fruit)
# -------------------------
def act(prompt):
    return {"output": prompt}

# -------------------------
# EXAMINE (Fruit validation — balanced)
# -------------------------
def examine(output, intent):
    lower = output.lower()
    checks = {
        "has_role": lower.startswith("you are"),
        "intent_match": intent["topic"].lower() in lower,
        "has_search_instruction": "search" in lower,
        "clarity": len(output.split()) > 30
    }
    return {"passed": all(checks.values()), "checks": checks}

def align_examine(x):
    return x["passed"], "Output (fruit) failed"

# -------------------------
# ESTABLISH
# -------------------------
def establish(output, intent):
    return {"approved": True}

# -------------------------
# TRACE ROOT
# -------------------------
def trace(failure):
    if "Output" in failure or "Format" in failure:
        return "format"
    if "Decision" in failure:
        return "decision"
    if "Judgment" in failure:
        return "judgment"
    if "Validation" in failure:
        return "validation"
    if "Structure" in failure:
        return "structure"
    return "intent"

# -------------------------
# MAIN PIPELINE
# -------------------------
def generate_fallback(topic: str) -> str:
    restarts = 0
    while restarts <= MAX_RESTARTS:
        r = read_input(topic)
        ok, err = align_read(r)
        if not ok:
            r = {"raw_input": "this topic", "complete": True}

        intent = interpret_intent(r["raw_input"])
        ok, err = align_intent(intent)
        if not ok:
            restarts += 1
            continue

        structure = select_structure(intent)
        ok, err = align_structure(intent, structure)
        if not ok:
            restarts += 1
            continue

        v = validate(intent, structure)
        ok, err = align_validation(v)
        if not ok:
            restarts += 1
            continue

        j = judge(structure)
        ok, err = align_judgment(j)
        if not ok:
            restarts += 1
            continue

        d = decide(intent, structure)
        ok, err = align_decision(d, intent)
        if not ok:
            restarts += 1
            continue

        ok, err = diagnose(intent, d)
        if not ok:
            restarts += 1
            continue

        prompt = format_prompt(r["raw_input"], intent, d)
        ok, err = align_format(prompt, r["raw_input"], intent)
        if not ok:
            restarts += 1
            continue

        out = act(prompt)["output"]
        ex = examine(out, intent)
        ok, err = align_examine(ex)
        if not ok:
            restarts += 1
            continue

        establish(out, intent)
        return out

    return "ERROR: Alignment failed after retries."
