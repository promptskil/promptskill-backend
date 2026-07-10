"""Phase A — Together serverless Multi-LoRA verification for the Vaine v2 fine-tune.

Answers three gating questions before any serving-posture change (A before B):
  1. Does the v2 LoRA adapter actually serve on Together SERVERLESS (vs. dedicated-only)?
  2. Cold-start + warm p50/p95 latency vs. the 30s enterprise SLA.
  3. Per-token basis (prompt/completion tokens) for the cost model.

Run FIRST THING (model idle) so call 1 reflects a cold-ish start.

    pip install openai
    $env:TOGETHER_API_KEY = "<your key>"
    python scripts/verify_serverless.py
    Remove-Item Env:\TOGETHER_API_KEY

If section 1 errors with "requires a dedicated endpoint" / not found / not served,
serverless is NOT available for this adapter as-is — stop and paste the error.
"""

import os
import statistics
import sys
import time

try:
    from openai import OpenAI
except ImportError:
    sys.exit("pip install openai  first")

API_KEY = os.environ.get("TOGETHER_API_KEY")
if not API_KEY:
    sys.exit("ERROR: set TOGETHER_API_KEY")

BASE_URL = "https://api.together.xyz/v1"
# confirm this is the v2 output model id (checklist 4.2d)
MODEL = "jeremiedyely_0f8b/Meta-Llama-3.1-8B-Instruct-Reference-vaine-v2-fbcf7718"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# production-shaped directive (latency is driven by tokens generated, not exact wording)
SYSTEM = (
    "You are Vaine. Do NOT answer the topic. Rephrase it and break it down into a "
    "strong, structured prompt, bound strictly to the user's input."
)
TOPICS = [
    "help me write a cold email to investors",
    "plan a 3 day trip to japan on a budget",
    "explain compound interest for a beginner",
    "draft a product spec for a mobile expense tracker",
    "summarize the risks of switching to serverless inference",
]


def call(topic):
    t0 = time.perf_counter()
    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": topic},
        ],
        max_tokens=400,
        temperature=0.7,
        stop=["<|eot_id|>"],
    )
    dt = time.perf_counter() - t0
    return dt, r.usage.prompt_tokens, r.usage.completion_tokens, r.choices[0].message.content


def main():
    print("=== 1. SERVERLESS COMPATIBILITY + COLD START ===")
    try:
        dt, pin, pout, text = call(TOPICS[0])
        print(f"OK  serverless call succeeded.  cold-ish latency={dt:.2f}s  in={pin} out={pout} tok")
        print("sample:", " ".join(text.split())[:200])
    except Exception as e:
        print("FAILED:", repr(e))
        print(
            ">>> If this says the model needs a DEDICATED endpoint / not found / not served, "
            "serverless is NOT available for this adapter. Stop here and paste this error."
        )
        sys.exit(1)

    print("\n=== 2. WARM LATENCY (15 calls) ===")
    lat, outtok = [], []
    for i in range(15):
        try:
            dt, _, pout, _ = call(TOPICS[i % len(TOPICS)])
            lat.append(dt)
            outtok.append(pout)
            print(f"  {i + 1:2d}: {dt:5.2f}s  out={pout}")
        except Exception as e:
            print(f"  {i + 1:2d}: ERROR {e!r}")
        time.sleep(0.4)

    if lat:
        s = sorted(lat)
        p50 = statistics.median(s)
        p95 = s[min(len(s) - 1, int(round(0.95 * len(s))) - 1)]
        print(f"\nRESULT  n={len(lat)}  p50={p50:.2f}s  p95={p95:.2f}s  min={min(s):.2f}s  max={max(s):.2f}s")
        print(f"avg out tok={statistics.mean(outtok):.0f}   30s-SLA(p95): {'PASS' if p95 < 30 else 'FAIL'}")


if __name__ == "__main__":
    main()
