import json, subprocess, sys
from pathlib import Path


def test_seed_builds_messy_crm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "scripts" / "seed_crm.py")],
                       capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(root / "src")})
    assert r.returncode == 0, r.stderr
    from leadflow.crm import SQLiteCRM
    crm = SQLiteCRM(tmp_path / "data" / "crm.db")
    s = crm.stats()
    assert s["contacts"] >= 400 and s["deals"] == 150
    truth = json.loads((tmp_path / "data" / "seed_truth.json").read_text())
    assert len(truth) == 75
    nulls = crm.conn.execute("SELECT COUNT(*) c FROM contacts WHERE email IS NULL").fetchone()["c"]
    assert nulls > 0
