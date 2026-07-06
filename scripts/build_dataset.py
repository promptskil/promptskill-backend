r"""0.4b - build the final train/eval sets from the labeled corpus + seed.

Steps: merge -> dedup (exact + cap near-dup template clusters to 5) ->
shuffle -> stratified 90/10 split by (domain x class) -> write train + eval.
No domain cap (Option A). Sources are left untouched.

Run (host):
    python scripts/build_dataset.py
"""
import collections
import json
import os
import random

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))
CLUSTER_CAP = 5      # max records sharing the same 40-char input prefix
EVAL_FRAC = 0.10     # held-out fraction, stratified
SEED = 42


def load(name):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        return []
    return [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]


def norm(s):
    return " ".join(s.strip().lower().split())


def domain_of(r):
    for x in r.get("intent_facets", []):
        if x.lower().startswith("domain"):
            return x.split(":", 1)[1].strip().lower()
    return "?"


def dedup(rows):
    """Drop exact-dup inputs; cap each 40-char prefix cluster at CLUSTER_CAP."""
    seen, cluster, out = set(), collections.Counter(), []
    for r in rows:
        key = norm(r["input"])
        if key in seen:
            continue
        pre = key[:40]
        if cluster[pre] >= CLUSTER_CAP:
            continue
        seen.add(key)
        cluster[pre] += 1
        out.append(r)
    return out


def write(name, rows):
    with open(os.path.join(BASE, name), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def dist(rows):
    d = collections.Counter(domain_of(r) for r in rows)
    c = collections.Counter(r.get("class", "?") for r in rows)
    return dict(c), d.most_common()


if __name__ == "__main__":
    random.seed(SEED)
    merged = load("vaine-dataset.wildchat.jsonl") + load("vaine-dataset.jsonl")
    random.shuffle(merged)                      # unbiased which 5 survive per cluster
    kept = dedup(merged)
    print(f"merged {len(merged)} -> deduped {len(kept)} (dropped {len(merged) - len(kept)})")

    strata = collections.defaultdict(list)
    for r in kept:
        strata[(domain_of(r), r.get("class", "?"))].append(r)

    train, evalset = [], []
    for rows in strata.values():
        random.shuffle(rows)
        k = round(len(rows) * EVAL_FRAC)
        evalset += rows[:k]
        train += rows[k:]
    random.shuffle(train)
    random.shuffle(evalset)

    write("vaine-dataset.train.jsonl", train)
    write("vaine-dataset.eval.jsonl", evalset)

    tc, td = dist(train)
    ec, ed = dist(evalset)
    print(f"train {len(train)} -> vaine-dataset.train.jsonl | class {tc}")
    print("       domain", td)
    print(f"eval  {len(evalset)} -> vaine-dataset.eval.jsonl | class {ec}")
    print("       domain", ed)
