"""
Vaine Claude fallback — alignment-enforced pipeline
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
        return {"sections": ["role", "instructions", "context", "task", "constraints"]}
    return {"sections": ["role", "task"]}

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
# FORMAT (Word clarity — Claude XML 4-block)
# -------------------------
def format_prompt(raw_input, intent, decision):
    topic = intent["topic"]
    return (
        f"<role>You are an expert explaining {topic} clearly and accurately.</role>\n\n"
        f"<instructions>\n"
        f"- Search for current information on {topic} before responding, "
        f"as this field changes over time\n"
        f"- If unsure about any fact, say so explicitly rather than guessing\n"
        f"</instructions>\n\n"
        f"<context>\n"
        f"Audience: {intent['audience']} at a {intent['depth']} level "
        f"who needs a clear understanding of {topic}\n"
        f"</context>\n\n"
        f"<task>\n"
        f"Respond using these three sections:\n"
        f"**Concept** — core definition of {topic}\n"
        f"**Example** — one concrete, real-world example\n"
        f"**Requirements** — key facts, depth, and what matters most\n"
        f"</task>\n\n"
        f"<constraints>\n"
        f"Stay grounded. Avoid speculation. Favor layered reasoning. No buzzwords.\n"
        f"</constraints>"
    )

def align_format(prompt, raw_input, intent):
    required_tags = ["<role>", "<instructions>", "<context>", "<task>", "<constraints>"]
    ok = all(tag in prompt for tag in required_tags) and intent["topic"] in prompt
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
        "core_structure": "<role>" in output and "<task>" in output,
        "intent_match": intent["topic"].lower() in lower,
        "clarity": len(output.split()) > 30,
        "has_guidance": "<instructions>" in output
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
