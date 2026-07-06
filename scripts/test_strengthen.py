r"""Vaine Phase 5 gate test — runs the Strengthener on the real dataset rewrites.

The `assert not leaked` line IS the hard determinism gate:
    tokens(out) subset of tokens(in) union the applied strong values.

Run (from the backend root so `app` is importable):
    cd promptskill-backend
    python scripts/test_strengthen.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.vaine_strengthener import new_tokens, strengthen  # noqa: E402

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))

with open(os.path.join(BASE, "rule-profiles", "claude.json"), encoding="utf-8") as f:
    profile = json.load(f)
with open(os.path.join(BASE, "base-lexicon.mock.json"), encoding="utf-8") as f:
    lexicon = json.load(f)

applied = list(profile["strength_set"].values())
changed = 0
rows = 0

for line in open(os.path.join(BASE, "vaine-dataset.jsonl"), encoding="utf-8"):
    r = json.loads(line)
    rows += 1
    src = r["target_rewrite"]
    out = strengthen(src, profile, lexicon)
    leaked = new_tokens(src, out, applied)
    assert not leaked, f"{r['id']} leaked new tokens: {leaked}"  # HARD GATE
    if out != src:
        changed += 1
        print(f"[{r['id']}]\n  in:  {src}\n  out: {out}\n")

print(f"OK — determinism gate passed on all {rows} rows. {changed} rewrites strengthened.")
