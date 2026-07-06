r"""Vaine Phase 3 gate test — classifier matches the dataset labels.

Reads the canonical threshold from the taxonomy, then checks classify() against the
`class` label on every dataset + eval record. Any mismatch is a mislabeled row.

Run:
    cd promptskill-backend
    python scripts/test_classify.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.vaine_classifier import classify, threshold_from_taxonomy  # noqa: E402

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))

with open(os.path.join(BASE, "facet-taxonomy.mock.json"), encoding="utf-8") as f:
    taxonomy = json.load(f)
max_chars = threshold_from_taxonomy(taxonomy)
print(f"threshold (from taxonomy): {max_chars}")

mism = 0
rows = 0
for path in ("vaine-dataset.jsonl", "vaine-dataset.eval.jsonl"):
    for line in open(os.path.join(BASE, path), encoding="utf-8"):
        r = json.loads(line)
        rows += 1
        got = classify(r["input"], max_chars)
        if got != r["class"]:
            mism += 1
            print(f"{r['id']}: label={r['class']} classify={got} len={len(r['input'])} :: {r['input'][:55]}")

print(f"OK — {rows} rows, {mism} mismatches" if mism == 0 else f"{mism} MISMATCHES of {rows}")
