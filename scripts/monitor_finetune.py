r"""4.2d - monitor the Vaine fine-tune. Prints status + recent events.

Run (host, needs TOGETHER_API_KEY in .env):
    python scripts/monitor_finetune.py            # uses the default job id
    python scripts/monitor_finetune.py <job-id>   # or pass one explicitly
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from together import Together  # noqa: E402

from app.config import settings  # noqa: E402

JOB = sys.argv[1] if len(sys.argv) > 1 else "ft-912bfdc6-d339"
client = Together(api_key=settings.TOGETHER_API_KEY)

r = client.fine_tuning.retrieve(JOB)
print("status:", r.status)
print("output model:", getattr(r, "x_model_output_name", None) or "(not ready yet)")
print("--- recent events ---")
for ev in client.fine_tuning.list_events(id=JOB).data[-15:]:
    print(ev.message)
