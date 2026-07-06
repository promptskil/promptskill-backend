r"""Vaine Phase 0.1 — READ-ONLY extraction of the raw user-topic sample (G-1).

Pulls distinct, non-deleted topics from `prompts.topic` (real user inputs) and
writes them topic-only (NO labels) to the Vaine data folder. Sync psycopg2 driver
— no asyncpg needed. Labels/facets/rewrites come later (Phase 0.2 / 0.3).

Run (PowerShell; READ-ONLY creds):
    $env:DATABASE_URL = "postgresql://<read-only-user>@<host>:<port>/<db>"
    python scripts/collect_raw_sample.py
    Remove-Item Env:\DATABASE_URL
"""
from __future__ import annotations

import json
import os
import statistics

from sqlalchemy import create_engine, text

OUT = os.environ.get("RAW_OUT") or os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data", "raw-sample.jsonl")
)


def _sync_url(url: str) -> str:
    """This one-off uses the sync psycopg2 driver. Coerce any scheme to psycopg2."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def main() -> None:
    url = _sync_url(os.environ["DATABASE_URL"])  # READ-ONLY creds recommended
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT DISTINCT trim(topic) AS topic
                    FROM prompts
                    WHERE deleted_at IS NULL
                      AND topic IS NOT NULL
                      AND length(trim(topic)) BETWEEN 1 AND 500
                    """
                )
            )
            raw = [row.topic for row in result]
    finally:
        engine.dispose()

    seen: set[str] = set()
    clean: list[str] = []
    for topic in raw:
        topic = " ".join(topic.split())
        if topic and topic.lower() not in seen:
            seen.add(topic.lower())
            clean.append(topic)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for i, topic in enumerate(clean, 1):
            f.write(json.dumps({"id": f"raw-{i:05d}", "input": topic}) + "\n")

    lens = [len(t) for t in clean]
    print(f"wrote {len(clean)} raw topics -> {OUT}")
    if lens:
        print(f"len min/median/max: {min(lens)}/{int(statistics.median(lens))}/{max(lens)}")
        print("sample:")
        for topic in clean[:15]:
            print("  -", topic)


if __name__ == "__main__":
    main()
