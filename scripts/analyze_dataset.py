r"""0.4a - analyze the labeled corpus before dedup/merge/rebalance (READ-ONLY, prints only).

Run (host):
    python scripts/analyze_dataset.py
"""
import collections
import json
import os

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))


def load(name):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        return []
    return [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]


def domain_of(r):
    for x in r.get("intent_facets", []):
        if x.lower().startswith("domain"):
            return x.split(":", 1)[1].strip().lower()
    return "?"


def report(name, rows):
    print(f"\n=== {name}: {len(rows)} records ===")
    if not rows:
        return
    inp = [r["input"].strip().lower() for r in rows]
    pref = collections.Counter(i[:40] for i in inp)
    clusters = sorted([(k, v) for k, v in pref.items() if v >= 4], key=lambda x: -x[1])
    print("class:", dict(collections.Counter(r.get("class", "?") for r in rows)))
    print("domain:", collections.Counter(domain_of(r) for r in rows).most_common())
    print("exact-dup inputs:", len(inp) - len(set(inp)))
    print(f"prefix-clusters (>=4 share first 40 chars): {len(clusters)}")
    for k, v in clusters[:12]:
        print(f"   {v:4d}  {k!r}")


if __name__ == "__main__":
    wild, seed = load("vaine-dataset.wildchat.jsonl"), load("vaine-dataset.jsonl")
    report("wildchat (labeled)", wild)
    report("seed (hand-authored)", seed)
    both = wild + seed
    dom = collections.Counter(domain_of(r) for r in both)
    print(f"\n=== merged {len(both)} | domain mix ===")
    for d, n in dom.most_common():
        print(f"   {n:4d}  {d}  ({100 * n / len(both):.1f}%)")
