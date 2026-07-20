"""Review/validate CLI glue: candidate selection, protocol resolution, scoring
inputs — pure functions against synthetic stores (no config, no network)."""

import json

import pandas as pd
import pyarrow as pa
import pytest

from pipeline.review import decisions as dec_mod
from pipeline.review.run import (
    devset_trec_ids,
    load_doc_rows,
    resolve_protocol,
    tier1_decision_ids,
    trec_to_doc_ids,
)
from pipeline.store import DOC_ID_MAP, MESSAGES, write_table
from pipeline.validate.run import current_responsiveness, mapped_qrels

pytestmark = pytest.mark.phase2


def make_decision(doc_id, decision, tier=1, topic="401", version="athome401.v1", supersedes=None):
    return dec_mod.new_decision(
        doc_id=doc_id, corpus="bush", topic=topic, phase="responsiveness", tier=tier,
        model="m", prompt_version=version, prompt_hash="abc", batch_id="b1",
        decision=decision, confidence=0.9, rationale="synthetic", input_tokens=1,
        output_tokens=1, cost_usd=0.0, supersedes=supersedes,
    )


def write_store(tmp_path, doc_ids, trec_ids):
    idmap = pa.table(
        {
            "corpus": ["bush"] * len(doc_ids),
            "doc_id": doc_ids,
            "trec_doc_id": trec_ids,
            "match_method": ["filename"] * len(doc_ids),
            "confidence": [1.0] * len(doc_ids),
            "ambiguous": [False] * len(doc_ids),
        }
    ).cast(DOC_ID_MAP)
    write_table(tmp_path, "bush", "doc_id_map", idmap)
    rows = [
        {c.name: None for c in MESSAGES} | {
            "doc_id": d, "corpus": "bush", "source_file": f"s/{t}", "source_index": 0,
            "references": [], "to": [], "cc": [], "bcc": [], "attachment_names": [],
            "parse_warnings": [], "body_text": "synthetic", "body_norm": "synthetic",
            "body_hash": "h", "has_attachments": False, "attachment_count": 0,
            "token_estimate": 10,
        }
        for d, t in zip(doc_ids, trec_ids)
    ]
    write_table(tmp_path, "bush", "messages", pa.Table.from_pylist(rows, schema=MESSAGES))
    return tmp_path


def test_resolve_protocol_picks_highest_version_numerically(tmp_path):
    (tmp_path / "bush").mkdir()
    (tmp_path / "bush" / "athome401.v9.md").write_text("v9")
    (tmp_path / "bush" / "athome401.v10.md").write_text("v10")  # lexicographic trap
    (tmp_path / "bush" / "athome401.vdraft.md").write_text("not a version")
    (tmp_path / "bush" / "athome402.v99.md").write_text("other topic")
    assert resolve_protocol(tmp_path, "bush", "401").name == "athome401.v10.md"


def test_resolve_protocol_missing_is_hard_stop(tmp_path):
    (tmp_path / "bush").mkdir()
    with pytest.raises(FileNotFoundError, match="human-edited"):
        resolve_protocol(tmp_path, "bush", "401")


def test_devset_trec_ids_unions_grades(tmp_path):
    (tmp_path / "devset_bush_401.json").write_text(
        json.dumps({"doc_ids": {"rel2": ["000002"], "rel1": ["000003"], "rel0": ["000001", "000003"]}})
    )
    assert devset_trec_ids(tmp_path, "bush", "401") == ["000001", "000002", "000003"]


def test_trec_to_doc_ids_and_doc_rows(tmp_path):
    store = write_store(tmp_path, ["bush-aaaa", "bush-bbbb"], ["000001", "000002"])
    mapping = trec_to_doc_ids(store, "bush", ["000002", "000001"])
    assert mapping == {"000002": "bush-bbbb", "000001": "bush-aaaa"}
    with pytest.raises(ValueError, match="no doc_id mapping"):
        trec_to_doc_ids(store, "bush", ["999999"])

    rows = load_doc_rows(store, "bush", {"bush-bbbb", "bush-aaaa"})
    assert [r["doc_id"] for r in rows] == ["bush-aaaa", "bush-bbbb"]
    with pytest.raises(ValueError, match="not in messages"):
        load_doc_rows(store, "bush", {"bush-missing"})


def test_tier1_decision_ids_maps_current_only():
    t1 = make_decision("bush-aaaa", "borderline")
    t1b = make_decision("bush-bbbb", "responsive")
    t2 = make_decision("bush-aaaa", "responsive", tier=2, supersedes=t1["decision_id"])
    df = pd.DataFrame([t1, t1b, t2])
    ids = tier1_decision_ids(df, topic="401", prompt_version="athome401.v1")
    assert ids == {"bush-bbbb": t1b["decision_id"]}  # t1 superseded by t2


def test_current_responsiveness_drops_superseded_other_topics_and_versions():
    t1 = make_decision("bush-aaaa", "borderline")
    t2 = make_decision("bush-aaaa", "responsive", tier=2, supersedes=t1["decision_id"])
    other = make_decision("bush-cccc", "responsive", topic="402")
    stale = make_decision("bush-aaaa", "not_responsive", version="athome401.v0")
    df = pd.DataFrame([t1, t2, other, stale])
    cur = current_responsiveness(df, "401", "athome401.v1")
    assert cur.to_dict("records") == [{"doc_id": "bush-aaaa", "decision": "responsive"}]
    assert current_responsiveness(pd.DataFrame(), "401", "athome401.v1").empty


def test_current_doc_ids_excludes_tier2_adjudicated_docs_from_tier1_reruns():
    t1 = make_decision("bush-aaaa", "borderline")
    t2 = make_decision("bush-aaaa", "responsive", tier=2, supersedes=t1["decision_id"])
    df = pd.DataFrame([t1, t2])
    # scored_doc_ids(tier=1) forgets the doc once tier 2 supersedes it...
    assert dec_mod.scored_doc_ids(df, topic="401", phase="responsiveness", tier=1,
                                  prompt_version="athome401.v1") == set()
    # ...current_doc_ids still remembers it, so tier-1 reruns skip it.
    assert dec_mod.current_doc_ids(df, topic="401", phase="responsiveness",
                                   prompt_version="athome401.v1") == {"bush-aaaa"}


def test_mapped_qrels_joins_doc_ids(tmp_path):
    store = write_store(tmp_path, ["bush-aaaa", "bush-bbbb"], ["000001", "000002"])
    qrels = pa.table(
        {
            "corpus": ["bush", "bush", "bush"],
            "topic": ["401", "401", "401"],
            "trec_doc_id": ["000001", "000002", "999999"],
            "relevance": [2, 0, 1],
            "stratum": [None, None, None],
            "sampling_weight": [None, None, None],
        }
    )
    write_table(store, "bush", "qrels_raw", qrels)
    merged = mapped_qrels(store, "bush")
    assert merged["sampling_weight"].tolist() == [1.0, 1.0, 1.0]
    assert merged[merged["trec_doc_id"] == "999999"]["doc_id"].isna().all()
    assert merged[merged["trec_doc_id"] == "000001"]["doc_id"].tolist() == ["bush-aaaa"]


# -- main() paths: dry-run safety, budget-phase mapping, idempotent resume ----

def make_cli_env(tmp_path, monkeypatch):
    """Full tmp environment for review/validate main(): store, devset artifact,
    protocol (tmp only — repo protocols/ stays human-edited), configs."""
    from pipeline.config import BudgetConfig, PipelineConfig
    import pipeline.review.run as review_run
    import pipeline.validate.run as validate_run

    store = tmp_path / "store"
    write_store(store, ["bush-aaaa", "bush-bbbb"], ["000001", "000002"])
    qrels = pa.table(
        {
            "corpus": ["bush"] * 2,
            "topic": ["401"] * 2,
            "trec_doc_id": ["000001", "000002"],
            "relevance": [1, 0],
            "stratum": [None, None],
            "sampling_weight": [None, None],
        }
    )
    write_table(store, "bush", "qrels_raw", qrels)

    paths = {
        "raw": tmp_path / "raw", "store": store, "batches": tmp_path / "batches",
        "decisions": tmp_path / "decisions", "spend": tmp_path / "spend",
        "productions": tmp_path / "productions", "artifacts": tmp_path / "artifacts",
    }
    paths["artifacts"].mkdir(parents=True)
    (paths["artifacts"] / "devset_bush_401.json").write_text(
        json.dumps({"doc_ids": {"rel1": ["000001"], "rel0": ["000002"]}})
    )
    (tmp_path / "protocols" / "bush").mkdir(parents=True)
    (tmp_path / "protocols" / "bush" / "athome401.v1.md").write_text(
        "Synthetic protocol: decide responsiveness, output JSON."
    )
    cfg = PipelineConfig(
        paths=paths,
        models={"tier1": "model-a", "tier2": "model-b", "narrative": "model-b"},
        batch={"max_requests_per_batch": 10000, "poll_interval_seconds": 0,
               "max_output_tokens": 64},
        seeds={"devset": 42, "qc_sample": 1, "elusion": 7},
        sampling={"devset_per_topic_max": 500, "devset_relevant_target": 150,
                  "qc_fraction": 0.5, "elusion_sample_size": 5},
        normalization_version="v1",
    )
    budget = BudgetConfig(
        prices={"model-a": {"input": 1.0, "output": 1.0},
                "model-b": {"input": 5.0, "output": 5.0}},
        caps={"dev_loop": 25.0, "bush_sample": 0.0},
        total_stop_usd=100.0,
    )
    monkeypatch.setattr(review_run, "load_pipeline_config", lambda: cfg)
    monkeypatch.setattr(review_run, "load_budget_config", lambda: budget)
    monkeypatch.setattr(validate_run, "load_pipeline_config", lambda: cfg)
    monkeypatch.setattr(validate_run, "REPO_ROOT", tmp_path)
    import pipeline.config as config_mod
    monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
    return cfg, budget


def review_args(**overrides):
    from argparse import Namespace
    base = dict(corpus="bush", topic="401", tier=1, phase="responsiveness",
                dev_set=True, dry_run=True)
    base.update(overrides)
    return Namespace(**base)


def test_review_dry_run_never_constructs_client(tmp_path, monkeypatch, capsys):
    import pipeline.review.batch as batch_mod
    import pipeline.review.run as review_run

    make_cli_env(tmp_path, monkeypatch)

    def bomb():
        raise AssertionError("dry run must not construct an API client")

    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", bomb)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert review_run.main(review_args()) == 0
    out = capsys.readouterr().out
    assert "candidates: 2" in out and "dry run: nothing submitted" in out


def test_review_submit_writes_decisions_and_bills_dev_loop(tmp_path, monkeypatch,
                                                           mock_client_factory):
    import pipeline.review.batch as batch_mod
    import pipeline.review.run as review_run

    cfg, _ = make_cli_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", lambda: mock_client_factory())

    assert review_run.main(review_args(dry_run=False)) == 0
    df = dec_mod.load(cfg.paths["decisions"], "bush")
    assert len(df) == 2 and set(df["doc_id"]) == {"bush-aaaa", "bush-bbbb"}
    spend = [json.loads(line) for line in
             (cfg.paths["spend"] / "spend.jsonl").read_text().splitlines()]
    assert [r["phase"] for r in spend] == ["dev_loop"]

    # idempotent resume: identical rerun finds 0 candidates, submits nothing
    def bomb():
        raise AssertionError("resume with 0 candidates must not submit")
    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", bomb)
    assert review_run.main(review_args(dry_run=False)) == 0
    assert len(dec_mod.load(cfg.paths["decisions"], "bush")) == 2


def test_review_full_run_bills_locked_bush_sample_and_stops(tmp_path, monkeypatch,
                                                            mock_client_factory):
    import pipeline.review.batch as batch_mod
    import pipeline.review.run as review_run

    make_cli_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    client = mock_client_factory()
    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", lambda: client)

    assert review_run.main(review_args(dev_set=False, dry_run=False)) == 1
    assert client.created_batches == []  # locked cap stopped submission pre-flight


def test_validate_main_scopes_dev_set_and_writes_artifact(tmp_path, monkeypatch,
                                                          mock_client_factory):
    from argparse import Namespace

    import pipeline.review.batch as batch_mod
    import pipeline.review.run as review_run
    import pipeline.validate.run as validate_run

    cfg, _ = make_cli_env(tmp_path, monkeypatch)
    v_args = Namespace(corpus="bush", topic="401", dev_set=True)
    assert validate_run.main(v_args) == 2  # no decisions yet -> hard stop

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", lambda: mock_client_factory())
    assert review_run.main(review_args(dry_run=False)) == 0

    assert validate_run.main(v_args) == 0
    payload = json.loads(
        (cfg.paths["artifacts"] / "metrics_bush_401_dev.json").read_text()
    )
    assert payload["scope"] == "dev" and payload["prompt_version"] == "athome401.v1"
    assert payload["n_judged"] == 2 and payload["n_evaluated"] == 2
