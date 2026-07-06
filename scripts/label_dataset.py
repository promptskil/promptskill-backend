r"""G3 — LLM-draft labels for the WildChat candidate pool.

The LLM (teacher) drafts intent_facets + target_rewrite and drops off-topic prompts
(keep=false). The class is set DETERMINISTICALLY by the classifier (120-char rule),
not the LLM. Technical is capped to rebalance. Writes a SEPARATE file for review.

Run (host, needs ANTHROPIC_API_KEY):
    python scripts/label_dataset.py --limit 50        # start small, review, then scale
    python scripts/label_dataset.py --limit 100000    # full pool once the bar is set
"""
import argparse
import datetime
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
from app.services.vaine_classifier import classify, threshold_from_taxonomy  # noqa: E402

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))
MODEL = os.environ.get("VAINE_LABEL_MODEL", "claude-sonnet-4-6")

with open(os.path.join(BASE, "labeling-instruction.txt"), encoding="utf-8") as f:
    SYS = f.read()
taxonomy = json.load(open(os.path.join(BASE, "facet-taxonomy.mock.json"), encoding="utf-8"))
MAXCHARS = threshold_from_taxonomy(taxonomy)

client = anthropic.Anthropic(max_retries=6)


def _extract_json(text: str) -> dict:
    i = text.find("{")
    if i < 0:
        raise ValueError("no JSON object in response")
    obj, _ = json.JSONDecoder().raw_decode(text[i:])  # parse first object, ignore trailing
    return obj


def label(topic: str) -> dict:
    m = client.messages.create(
        model=MODEL, max_tokens=500, system=SYS,
        messages=[{"role": "user", "content": topic}],
    )
    return _extract_json(m.content[0].text)


def label_one(c: dict) -> tuple:
    """Worker: returns ('kept', record) | ('dropped', None) | ('error', msg)."""
    r = None
    for attempt in range(4):
        try:
            r = label(c["input"])
            break
        except anthropic.APIConnectionError as e:
            if attempt == 3:
                return ("error", f"conn: {e}")
            time.sleep(2 ** attempt)
        except Exception as e:  # noqa: BLE001
            return ("error", f"err: {e}")
    if r is None:
        return ("error", "no result")
    if not r.get("keep"):
        return ("dropped", None)
    return ("kept", {
        "id": c["id"],
        "input": c["input"],
        "class": classify(c["input"], MAXCHARS),
        "intent_facets": r.get("intent_facets", []),
        "target_rewrite": r.get("target_rewrite", ""),
        "polarity": "positive",
        "source": "wildchat",
        "version": "v1",
        "created_at": str(datetime.date.today()),
    })


def main(limit: int, tech_cap: int, workers: int) -> None:
    cands = [json.loads(l) for l in open(os.path.join(BASE, "wildchat-candidates.jsonl"), encoding="utf-8")]
    tech = [c for c in cands if c["domain"] == "technical"][:tech_cap]
    pool = ([c for c in cands if c["domain"] != "technical"] + tech)[:limit]
    total = len(pool)
    print(f"pool: {total} records | {workers} workers | model {MODEL}", flush=True)

    out_path = os.path.join(BASE, "vaine-dataset.wildchat.jsonl")
    kept = dropped = errors = done = 0
    with open(out_path, "w", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(label_one, c) for c in pool]
        for fut in as_completed(futures):
            status, payload = fut.result()
            done += 1
            if status == "kept":
                f.write(json.dumps(payload) + "\n")
                f.flush()
                kept += 1
            elif status == "dropped":
                dropped += 1
            else:
                errors += 1
                if errors <= 20:
                    print("skip:", payload, flush=True)
            if done % 10 == 0:
                print(f"... {done}/{total} | kept {kept} dropped {dropped} err {errors}", flush=True)
    print(f"kept {kept}, dropped {dropped} off-topic, {errors} errors -> {out_path}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--tech-cap", type=int, default=150)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    main(a.limit, a.tech_cap, a.workers)
