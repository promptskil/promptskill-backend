r"""4.2c - launch the Vaine LoRA fine-tune. RUN ONCE (cost starts at create).

If it errors and you must retry, first run client.fine_tuning.list() to confirm
no job already exists, or you will double-charge.

Run (host, needs TOGETHER_API_KEY in .env):
    python scripts/launch_finetune.py
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from together import Together  # noqa: E402

from app.config import settings  # noqa: E402

client = Together(api_key=settings.TOGETHER_API_KEY)

resp = client.fine_tuning.create(
    training_file="file-758bc1ce-ab99-4d7d-9b95-deedb3a5a24a",
    validation_file="file-50c39762-dde2-45eb-b036-1a9a01ffbe87",
    model="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    lora=True,
    n_epochs=3,
    n_evals=10,
    learning_rate=1e-5,
    warmup_ratio=0,
    train_on_inputs="auto",
    suffix="vaine-v2",
)
print("JOB_ID =", resp.id)
