"""G1 — stream WildChat-1M, extract + filter first-turn user prompts to Vaine domains.

Real user prompts (ODC-BY). Streams (no full download), drops toxic/NSFW, English only,
length 8-500, dedupes, tags a domain. Output feeds G2/G3 (label toward ~1,000).

Run (host, needs network + `pip install datasets`):
    python scripts/fetch_wildchat.py
"""
import json
import os
import re

from datasets import load_dataset

DOMAINS = {
    "startups": ["startup", "founder", "raise", "seed round", "pre-seed", "venture", "vc ",
                 "pitch deck", "cap table", "mvp", "go to market", "product market fit"],
    "entrepreneurship": ["business plan", "small business", "e-commerce", "ecommerce",
                         "side hustle", "franchise", "llc", "sole proprietor"],
    "research": ["research", "literature review", "hypothesis", "methodology", "citation",
                 "survey design", "analyze data", "white paper"],
    "jobs": ["resume", "cover letter", "job application", "interview", "ats", "recruiter",
             "salary", "linkedin", "career"],
    "marketing": ["marketing", "seo", "ad copy", "campaign", "email marketing", "conversion",
                  "funnel", "brand", "landing page"],
    "content_social": ["instagram", "tiktok", "youtube", "caption", "hashtag", "reel",
                       "content calendar", "influencer", "thumbnail"],
    "finance": ["invest", "stock", "etf", "portfolio", "valuation", "roi", "cash flow",
                "budget", " tax", "options", "dividend"],
    "business": ["revenue", "profit", "operations", "strategy", "kpi", "b2b", "saas",
                 "pricing", "market size"],
    "technical": ["python", "sql", " api ", "code", "function", "database", "algorithm",
                  "deploy", "architecture", "agent", "llm"],
}
KW = {k: d for d, ks in DOMAINS.items() for k in ks}
PAT = re.compile("|".join(re.escape(k) for k in KW), re.IGNORECASE)

OUT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data", "wildchat-candidates.jsonl")
)
MIN, MAX = 8, 500


def domain_of(text: str):
    m = PAT.search(text)
    return KW.get(m.group(0).lower().strip()) if m else None


def main(target: int) -> None:
    ds = load_dataset("allenai/WildChat-1M", split="train", streaming=True)
    seen: set[str] = set()
    kept = 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for row in ds:
            if row.get("language") != "English" or row.get("toxic"):
                continue
            conv = row.get("conversation") or []
            first = next((t.get("content") for t in conv if t.get("role") == "user"), None)
            if not first:
                continue
            first = " ".join(first.split())
            if not (MIN <= len(first) <= MAX):
                continue
            dom = domain_of(first)
            if not dom or first.lower() in seen:
                continue
            seen.add(first.lower())
            f.write(json.dumps({"id": f"wc-{kept + 1:05d}", "input": first, "domain": dom}) + "\n")
            kept += 1
            if kept >= target:
                break
    print(f"kept {kept} domain-matched, non-toxic prompts -> {OUT}")
    os._exit(0)  # skip datasets/pyarrow finalization (avoids a harmless GIL shutdown crash)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2500)
    main(ap.parse_args().target)
