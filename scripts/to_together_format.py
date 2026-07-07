r"""4.2a - convert the Vaine dataset to Together's conversational JSONL.

system = VAINE_SYSTEM (the transform directive); user = raw topic (input);
assistant = target_rewrite. The system message MUST match inference exactly, so
it is imported from vaine_engine (single source of truth). Writes
*.together.jsonl for check_file + upload.

Run (host):
    python scripts/to_together_format.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.vaine_engine import VAINE_SYSTEM  # noqa: E402

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))


def convert(src: str, dst: str) -> None:
    rows = [
        json.loads(line)
        for line in open(os.path.join(BASE, src), encoding="utf-8")
        if line.strip()
    ]
    n = 0
    with open(os.path.join(BASE, dst), "w", encoding="utf-8") as f:
        for r in rows:
            inp = (r.get("input") or "").strip()
            tgt = (r.get("target_rewrite") or "").strip()
            if not inp or not tgt:
                continue
            f.write(json.dumps({"messages": [
                {"role": "system", "content": VAINE_SYSTEM},
                {"role": "user", "content": inp},
                {"role": "assistant", "content": tgt},
            ]}) + "\n")
            n += 1
    print(f"{src} -> {dst}: {n} records")


if __name__ == "__main__":
    convert("vaine-dataset.train.jsonl", "vaine-train.together.jsonl")
    convert("vaine-dataset.eval.jsonl", "vaine-eval.together.jsonl")
