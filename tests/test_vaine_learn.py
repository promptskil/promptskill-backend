from unittest.mock import MagicMock

from app.tasks import learn_task


def _patch_count(monkeypatch, n):
    conn = MagicMock()
    conn.execute.return_value.scalar_one.return_value = n
    cm = MagicMock()
    cm.__enter__.return_value = conn
    monkeypatch.setattr(learn_task.sync_engine, "connect", lambda: cm)


def test_vaine_learn_ready(monkeypatch):
    _patch_count(monkeypatch, 250)
    r = learn_task.vaine_learn_task()
    assert r["feedback_count"] == 250 and r["retrain_ready"] is True


def test_vaine_learn_not_ready(monkeypatch):
    _patch_count(monkeypatch, 10)
    assert learn_task.vaine_learn_task()["retrain_ready"] is False
