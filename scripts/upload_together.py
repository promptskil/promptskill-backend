r"""4.2b - check_file + upload the Together JSONL. Prints the file IDs.

Run (host, needs TOGETHER_API_KEY + `pip install -U together`):
    python scripts/upload_together.py
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from together import Together  # noqa: E402

from app.config import settings  # noqa: E402

try:
    from together.utils import check_file  # optional client-side validator
except ImportError:
    check_file = None

BASE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "aisystem", "vaine-data"))
client = Together(api_key=settings.TOGETHER_API_KEY)  # key from .env via settings

for name in ("vaine-train.together.jsonl", "vaine-eval.together.jsonl"):
    path = os.path.join(BASE, name)
    if check_file is not None:
        rep = check_file(path)
        print(name, "->", rep["is_check_passed"], rep.get("message", ""))
        assert rep["is_check_passed"], rep
    else:
        print(name, "-> client-side check unavailable; server will validate on upload")

train = client.files.upload(
    os.path.join(BASE, "vaine-train.together.jsonl"), purpose="fine-tune", check=True
)
val = client.files.upload(
    os.path.join(BASE, "vaine-eval.together.jsonl"), purpose="fine-tune", check=True
)
print("TRAIN_FILE_ID =", train.id)
print("VAL_FILE_ID   =", val.id)
