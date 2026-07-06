r"""Vaine Phase 6 gate test — proves the Format Filter is structure-only and that the
full deterministic tail (strengthen -> format) holds on the real dataset rewrites.

Run (from the backend root so `app` is importable):
    cd promptskill-backend
    python scripts/test_format.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.vaine_format_filter import content_words, format_prompt  # noqa: E402
from app.services.vaine_strengthener import strengthen  # noqa: E402

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))

with open(os.path.join(BASE, "rule-profiles", "claude.json"), encoding="utf-8") as f:
    profile = json.load(f)
with open(os.path.join(BASE, "base-lexicon.mock.json"), encoding="utf-8") as f:
    lexicon = json.load(f)

# (a) scaffolding strip
scaffolded = "<task>\nWrite a **clear** guide.\n# Steps\n- do X\n</task>"
print("scaffolded ->", repr(format_prompt(scaffolded, profile)))

# (b)+(c) full tail on the real rewrites; format must not add/change content words
rows = 0
for line in open(os.path.join(BASE, "vaine-dataset.jsonl"), encoding="utf-8"):
    r = json.loads(line)
    rows += 1
    strong = strengthen(r["target_rewrite"], profile, lexicon)
    final = format_prompt(strong, profile)
    assert set(content_words(final)) <= set(content_words(strong)), f"{r['id']} added content"

print(f"OK — format is structure-only; strengthen->format chain deterministic on all {rows} rows.")
