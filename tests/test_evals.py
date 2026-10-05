import json, os, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("ev")
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_crm.py")], cwd=d, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    from evals.build_dataset import build
    ds = build(d / "data" / "crm.db", d / "data" / "seed_truth.json", d / "dataset.json")
    return d, ds


def test_dataset_shape(built):
    _, ds = built
    cases = ds["cases"]
    assert len(cases) == 50 and len({c["id"] for c in cases}) == 50
    tags = [t for c in cases for t in c["tags"]]
    for t, n in {"injection": 5, "spam": 5, "crm_exact": 7, "crm_fuzzy": 3, "clean": 7}.items():
        assert tags.count(t) == n


def test_all_labels_consistent(built):
    from evals.taxonomy import check_labels
    for c in built[1]["cases"]:
        assert check_labels(c["labels"]) == [], c["id"]


def test_label_checker_catches_mistakes():
    from evals.taxonomy import check_labels
    from evals.cases_static import STATIC_CASES
    import copy
    bad = copy.deepcopy(STATIC_CASES[0]["labels"])
    bad["route"] = "discard"            # hot lead cannot be discarded
    assert check_labels(bad)
    bad2 = copy.deepcopy(STATIC_CASES[0]["labels"])
    bad2["dedupe"] = {"decision": "existing", "match_ids": []}
    assert check_labels(bad2)


def test_static_new_leads_do_not_collide_with_crm(built):
    from leadflow.crm import SQLiteCRM
    d, ds = built
    crm = SQLiteCRM(d / "data" / "crm.db")
    for c in ds["cases"]:
        if "crm_" not in "".join(c["tags"]):
            e = c["labels"]["extraction"]["email"]
            if e:
                assert crm.find_by_email(e) == [], c["id"]
    crm.close()


def test_linked_labels_match_crm(built):
    from leadflow.crm import SQLiteCRM
    d, ds = built
    crm = SQLiteCRM(d / "data" / "crm.db")
    for c in ds["cases"]:
        if "crm_exact" in c["tags"]:
            ids = [x["id"] for x in crm.find_by_email(c["labels"]["extraction"]["email"])]
            assert ids and sorted(ids) == c["labels"]["dedupe"]["match_ids"]
        if "crm_namecollision" in c["tags"]:
            assert crm.find_by_email(c["labels"]["extraction"]["email"]) == []
    crm.close()


def test_oracle_scores_perfect(built):
    from evals.run_eval import run
    from evals.baselines import make_oracle
    d, _ = built
    rep = run(make_oracle(str(d / "dataset.json")), str(d / "dataset.json"), str(d / "data" / "crm.db"))
    assert rep["n_scored"] == 50
    assert rep["extraction"]["accuracy"] == 1.0
    assert rep["dedupe"]["decision_accuracy"] == 1.0 and rep["dedupe"]["duplicate_detection"] == 1.0
    assert rep["tier"]["accuracy"] == 1.0 and rep["route"]["accuracy"] == 1.0
    assert all(v["met"] for v in rep["targets"].values())


def test_regex_baseline_is_a_real_floor(built):
    from evals.run_eval import run
    from evals.baselines import regex_baseline
    d, _ = built
    rep = run(regex_baseline, str(d / "dataset.json"), str(d / "data" / "crm.db"))
    assert 0.5 < rep["extraction"]["accuracy"] < 0.95        # decent, but beatable
    assert rep["dedupe"]["false_merges"] == []
    assert not rep["crashed_cases"]


def test_pipeline_never_sees_labels(built):
    from evals.run_eval import run
    d, _ = built
    seen = []

    def spy(lead, crm):
        seen.append(set(lead))
        return {}
    run(spy, str(d / "dataset.json"), str(d / "data" / "crm.db"), limit=3)
    assert seen and all(s == {"id", "channel", "raw"} for s in seen)


def test_eval_cannot_modify_real_crm(built):
    from evals.run_eval import run
    d, _ = built
    db = d / "data" / "crm.db"
    before = db.read_bytes()

    def writer(lead, crm):
        crm.create_contact({"email": "evil@x.com"})
        return {}
    run(writer, str(d / "dataset.json"), str(db), limit=2)
    assert db.read_bytes() == before


def test_crash_counts_as_failure_not_abort(built):
    from evals.run_eval import run
    d, _ = built

    def boom(lead, crm):
        raise RuntimeError("x")
    rep = run(boom, str(d / "dataset.json"), str(d / "data" / "crm.db"), limit=4, only=["route"])
    assert len(rep["crashed_cases"]) == 4 and rep["route"]["accuracy"] == 0.0


def test_fingerprint_mismatch_excludes_linked_cases(built, tmp_path):
    from evals.run_eval import run
    from evals.baselines import regex_baseline
    d, _ = built
    ds = json.loads((d / "dataset.json").read_text())
    ds["meta"]["crm_fingerprint"] = "deadbeef"
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(ds))
    rep = run(regex_baseline, str(p), str(d / "data" / "crm.db"))
    assert rep["n_scored"] == 35 and len(rep["meta"]["excluded"]) == 15


def test_normalizers():
    from evals.scoring import text_match
    assert text_match("company_name", "Rossi & Partners", "Rossi and Partners Ltd")
    assert text_match("company_name", "Tan & Co", "Tan & Co.")
    assert text_match("first_name", "Tomás", "tomas")
    assert text_match("email", "A@B.com", " a@b.com ")
    assert text_match("last_name", None, "N/A") and not text_match("last_name", None, "Smith")
    assert text_match("job_title", ["Directora Comercial", "Commercial Director"], "commercial director")
    assert not text_match("first_name", "Sam", None)


def test_failure_report_shows_label_vs_pred(built):
    from evals.run_eval import run
    from evals.scoring import format_failures
    from evals.baselines import make_oracle
    d, ds = built
    oracle = make_oracle(str(d / "dataset.json"))

    def sloppy(lead, crm):
        p = oracle(lead, crm)
        if lead["id"] == "L001":
            p["extraction"]["job_title"] = "Janitor"
            p["tier"] = "cold"
        return p

    rep = run(sloppy, str(d / "dataset.json"), str(d / "data" / "crm.db"))
    txt = format_failures(rep, {c["id"]: c for c in ds["cases"]}, show_raw=True)
    assert "1 case(s) with errors" in txt and "Janitor" in txt and "tier" in txt and "| Name:" in txt
    assert format_failures(run(oracle, str(d / "dataset.json"), str(d / "data" / "crm.db"))) == "No errors."


def test_gen_config_builds():
    from leadflow import llm
    from leadflow.workflow.schemas import LLMExtraction
    assert llm._gen_config("sys", 100, LLMExtraction, "native") is not None
    assert llm._gen_config("sys", 100, LLMExtraction, "prompt") is not None
