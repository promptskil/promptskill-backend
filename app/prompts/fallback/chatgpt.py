"""
Vaine ChatGPT fallback — alignment-enforced pipeline
Baseline: Heart (Intent) → Truth (Alignment) → Fruit (Result)
If any layer breaks alignment → trace root → restart
"""
from typing import Any, Dict

MAX_RESTARTS = 2

_VALID_ACTIONS = frozenset(
    {"analyze_market", "compare", "instruction", "explain", "research"}
)

_PATTERNS: Dict[str, list] = {
    "analyze_market": [
        "market", "opportunity", "worth", "size", "revenue",
        "how big", "potential", "tam", "sam", "valuation", "value",
    ],
    "compare": [
        "compare", "difference", "vs", "better", "which one",
    ],
    "instruction": [
        "how", "build", "create", "step by step", "guide",
    ],
    "explain": [
        "why", "explain", "what is", "understand",
    ],
}

_OUTPUT_MAP: Dict[str, str] = {
    "analyze_market": "quantified_analysis",
    "compare": "comparison",
    "instruction": "step_by_step",
    "explain": "conceptual",
    "research": "general",
}

# -------------------------
# ROLE (normalize from intent)
# -------------------------
def normalize_role(intent, topic):
    action = intent.get("action", "research")
    if action == "analyze_market":
        base = "Market Analyst specializing in economics and competitive intelligence"
        return f"{base} for {topic}"
    if action == "compare":
        return f"Analyst specializing in comparative evaluation of {topic}"
    if action == "instruction":
        return f"Engineer providing step-by-step guidance on {topic}"
    if action == "explain":
        return f"Domain expert explaining {topic} clearly"
    return f"Research analyst covering {topic}"

# -------------------------
# READ (Word)
# -------------------------
def read_input(topic: str) -> Dict[str, Any]:
    return {"raw_input": topic.strip(), "complete": bool(topic and topic.strip())}

def align_read(x):
    return x["complete"], "Input incomplete"

# -------------------------
# INTENT (Heart) — pattern scoring
# -------------------------
def interpret_intent(raw: str) -> Dict[str, Any]:
    raw_lower = raw.lower()
    scores = {k: 0 for k in _PATTERNS}
    for action, words in _PATTERNS.items():
        for w in words:
            if w in raw_lower:
                scores[action] += 1

    best = max(scores, key=scores.get)
    if scores[best] > 0:
        action = best
        confidence = scores[best] / max(1, sum(scores.values()))
    else:
        action = "research"
        confidence = 0.0

    if action == "analyze_market":
        goal = f"analyze and quantify {raw}"
    elif action == "compare":
        goal = f"compare {raw}"
    elif action == "instruction":
        goal = f"provide step-by-step guidance for {raw}"
    elif action == "explain":
        goal = f"explain {raw} clearly"
    else:
        goal = f"research and summarize {raw}"

    dimensions = []
    if any(w in raw_lower for w in ["problem", "issue", "challenge", "fail"]):
        dimensions.append("problem")
    if any(w in raw_lower for w in ["compare", "vs", "difference"]):
        dimensions.append("comparison")
    if any(w in raw_lower for w in ["solution", "solve", "fit"]):
        dimensions.append("solution")
    _quant = ["quantify", "measure", "value", "estimate", "size"]
    if any(w in raw_lower for w in _quant):
        dimensions.append("quantification")
    if not dimensions:
        dimensions = ["general"]

    verbs = []
    if "calculate" in raw_lower:
        verbs.append("calculate")
    if "estimate" in raw_lower:
        verbs.append("estimate")
    if "measure" in raw_lower:
        verbs.append("measure")
    if "compare" in raw_lower:
        verbs.append("compare")
    if not verbs:
        verbs = ["explain"]

    return {
        "action": action,
        "output_type": _OUTPUT_MAP[action],
        "goal": goal,
        "audience": "general audience",
        "depth": "basic to intermediate",
        "topic": raw,
        "confidence": confidence,
        "dimensions": dimensions,
        "verbs": verbs,
    }

def align_intent(intent):
    ok = all([
        intent.get("action") in _VALID_ACTIONS,
        intent.get("goal"),
        intent.get("topic"),
    ])
    return ok, "Intent misaligned"

# -------------------------
# STRUCTURE (Pattern) — routes from intent
# -------------------------
def select_structure(intent: Dict[str, Any]) -> Dict[str, Any]:
    sections = ["Concept"]
    dims = intent.get("dimensions", [])

    if "problem" in dims:
        sections.append("Market Problem")
    if "comparison" in dims:
        sections.append("Comparison")
    if "solution" in dims:
        sections.append("Solution")
    if "quantification" in dims:
        sections.append("Quantified Value")

    # action fallback overrides for instruction and explain
    if intent["action"] == "instruction":
        sections = ["Goal", "Steps", "Example", "Constraints"]
    elif intent["action"] == "explain":
        sections = ["Concept", "Example", "Requirements"]

    sections.append("Conclusion")
    return {"sections": sections}

def align_structure(intent, structure):
    ok = (
        intent["action"] in _VALID_ACTIONS
        and len(structure["sections"]) > 0
        and structure["sections"][0] in ("Concept", "Goal")
    )
    return ok, "Structure misaligned with intent"

# -------------------------
# VALIDATION (Truth)
# -------------------------
def validate(intent, structure):
    return {
        "valid_action": intent["action"] in _VALID_ACTIONS,
        "has_sections": len(structure["sections"]) > 0,
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
        "topic": intent["topic"],
        "verbs": intent.get("verbs", []),
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
# FORMAT (Word clarity — clean output, no internal trace)
# -------------------------
def describe_section(section, verbs):
    instructions = []

    for v in verbs:
        if v == "calculate":
            instructions.append("calculate numeric values using stated assumptions")
        elif v == "estimate":
            instructions.append(
                "estimate values realistically with ranges where applicable"
            )
        elif v == "measure":
            instructions.append("quantify scale and measurable impact")
        elif v == "compare":
            instructions.append("compare directly with differences and trade-offs")
        elif v == "explain":
            instructions.append("explain the underlying mechanism clearly")

    if section == "Market Problem":
        instructions.append("define the core problem precisely")

    if section == "Quantified Value":
        instructions.append("show measurable economic impact")

    if section == "Comparison":
        if "compare directly with differences and trade-offs" not in instructions:
            instructions.append("compare with clear differences and trade-offs")

    if not instructions:
        return "cover this section clearly and thoroughly"

    return ", ".join(dict.fromkeys(instructions))


def format_prompt(raw_input, intent, decision):
    topic = intent["topic"]
    role = normalize_role(intent, topic)
    sections = decision["structure"]["sections"]

    section_instructions = "\n\n".join(
        describe_section(s, decision.get("verbs", []))
        for s in sections
    )

    return (
        f"You are a {role}.\n\n"
        f"Search for current information on {topic} before responding.\n\n"
        f"{section_instructions}\n\n"
        f"Use clear assumptions, provide precise outputs, "
        f"and match the requested depth. "
        f"Write for a {intent['audience']} audience "
        f"at a {intent['depth']} level. "
        f"Identify a single primary domain implied by the topic "
        f"and use its standard terminology consistently; "
        f"avoid mixing domains, avoid vague phrases such as "
        f"'intersection' or 'dimensions', "
        f"and express all analysis using precise domain-specific language."
    )

def align_format(prompt, raw_input, intent):
    ok = (
        prompt.lower().startswith("you are")
        and intent["topic"] in prompt
        and "search" in prompt.lower()
    )
    return ok, "Format misaligned"

# -------------------------
# ACT (Fruit)
# -------------------------
def act(prompt):
    return {"output": prompt}

# -------------------------
# EXAMINE (Fruit validation — structural and intent checks)
# -------------------------
def examine(output, intent):
    lower = output.lower()
    checks = {
        "has_role": lower.startswith("you are"),
        "intent_match": intent["topic"].lower() in lower,
        "has_search": "search" in lower,
        "clarity": len(output.split()) > 30,
    }
    return {"passed": all(checks.values()), "checks": checks}

def align_examine(x):
    return x["passed"], "Output (fruit) failed"

# -------------------------
# CONFIDENCE SCORING (Intent × Structure × Output)
# -------------------------
def score_confidence(intent, output, examine_result):
    score = 0.0

    # intent signal strength
    score += intent.get("confidence", 0.3) * 0.3

    # structure alignment
    if examine_result["passed"]:
        score += 0.4

    # output clarity
    if len(output.split()) > 30:
        score += 0.1

    # section coverage
    bool_checks = [
        v for v in examine_result["checks"].values() if isinstance(v, bool)
    ]
    total = max(1, len(bool_checks))
    hits = sum(bool_checks)
    score += (hits / total) * 0.2

    return round(score, 2)

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

        confidence = score_confidence(intent, out, ex)
        if confidence < 0.7:
            restarts += 1
            continue

        establish(out, intent)
        return out

    return "ERROR: Alignment failed after retries."
