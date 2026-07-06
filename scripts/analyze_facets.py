r"""0.4c-i - analyze facet distributions to re-freeze taxonomy + lexicon (READ-ONLY).

Run (host):
    python scripts/analyze_facets.py
"""
import collections
import json
import os
import re
import statistics

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))


def load(name):
    p = os.path.join(BASE, name)
    return [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()] if os.path.exists(p) else []


def facet(rows, key):
    c = collections.Counter()
    for r in rows:
        for x in r.get("intent_facets", []):
            k, _, v = x.partition(":")
            if k.strip().lower() == key:
                c[v.strip().lower()] += 1
    return c


def goal_heads(rows):
    c = collections.Counter()
    for r in rows:
        for x in r.get("intent_facets", []):
            k, _, v = x.partition(":")
            if k.strip().lower() == "goal" and v.strip():
                c[v.strip().lower().split()[0]] += 1
    return c


if __name__ == "__main__":
    rows = load("vaine-dataset.train.jsonl") + load("vaine-dataset.eval.jsonl")
    print("corpus:", len(rows))
    for key in ["domain", "artifact", "audience", "tone"]:
        print(f"\n{key} ({len(facet(rows, key))} distinct):", facet(rows, key).most_common(20))
    print("\ngoal-verb heads:", goal_heads(rows).most_common(20))
    for cls in ["simple", "complex"]:
        L = [len(r["input"]) for r in rows if r.get("class") == cls]
        if L:
            print(f"\n{cls}: n={len(L)} min={min(L)} median={int(statistics.median(L))} max={max(L)}")
    lex = json.load(open(os.path.join(BASE, "base-lexicon.mock.json"), encoding="utf-8"))
    inputs = [r["input"].lower() for r in rows]
    print("\nbase-lexicon term hits (whole-word in inputs):")
    for t in [e["base_term"] for e in lex["entries"]]:
        n = sum(1 for i in inputs if re.search(r"\b" + re.escape(t) + r"\b", i))
        print(f"   {n:4d}  {t}")
