"""Enron judged-sample universe: text-tarball ingest + review/validate wiring (synthetic, phase5)."""

import io
import json
import tarfile
from argparse import Namespace

import pyarrow as pa
import pytest

from pipeline.ingest.trec_text import build_row, member_docid, split_headers
from pipeline.qrels.enron import is_part
from pipeline.review import decisions as dec_mod
from pipeline.store import QRELS_RAW, read_table, write_table

pytestmark = pytest.mark.phase5

EMAIL = (
    "From: sender@example.com\n"
    "To: a@example.com, b@example.com\n"
    "Subject: synthetic subject\n"
    " folded tail\n"
    "Date: Mon, 1 Jan 2001 00:00:00 -0600\n"
    "\n"
    "synthetic body line\n"
)


def test_split_headers_parses_block_and_folds():
    headers, body = split_headers(EMAIL)
    assert headers["from"] == "sender@example.com"
    assert headers["subject"] == "synthetic subject folded tail"
    assert body == "synthetic body line\n"


@pytest.mark.parametrize("text", [
    "Note: not a header block\n\nrest of attachment\n",   # no anchor key
    "From: x@example.com\nSubject: never terminated",      # no blank line
    "plain attachment text\nFrom: x@example.com\n\nmore",  # first line not a header
])
def test_split_headers_keeps_full_text_when_not_a_header_block(text):
    assert split_headers(text) == ({}, text)


def test_member_docid_normalizes_and_rejects():
    assert member_docid("./edrm-enron-v2_jones-t_xml.zip/text_000/3.1.AAA.2.txt") == (
        "edrm-enron-v2_jones-t_xml.zip/text_000/3.1.AAA.2.txt", "jones-t", "3.1.AAA.2")
    assert member_docid("edrm-enron-v2_jones-t_xml.zip/text_000/") is None
    with pytest.raises(ValueError):
        member_docid("edrm-enron-v2_jones-t_xml.zip/deep/er/3.1.AAA.txt")


def test_build_row_marks_parts_and_truncates():
    row = build_row(b"x" * 50, source_file="t/p.txt", custodian="jones-t",
                    docid="3.1.AAA.2", max_body_chars=10)
    assert row["body_text"] == "x" * 10
    assert "truncated_body" in row["parse_warnings"]
    assert "no_header_block" in row["parse_warnings"]
    meta = json.loads(row["headers_json"])
    assert meta == {"is_part": True, "parent_trec_doc_id": "3.1.AAA",
                    "trec_doc_id": "3.1.AAA.2"}
    assert row["doc_id"].startswith("enron-")


# -- end-to-end: ingest -> review dry-run/submit -> validate ------------------

JUDGED = [  # topic 201: two strata, one part; weights 1.0 and 3.0
    ("3.1.AAA", "100", 1, 1.0),
    ("3.2.BBB", "100", 0, 1.0),
    ("3.3.CCC.1", "1000", 1, 3.0),
    ("3.4.DDD", "1000", 0, 3.0),
]


def make_env(tmp_path, monkeypatch, *, tarball_docids=None):
    from pipeline.config import BudgetConfig, CorpusConfig, PipelineConfig
    import pipeline.config as config_mod
    import pipeline.ingest.trec_text as trec_text
    import pipeline.review.run as review_run
    import pipeline.validate.run as validate_run

    paths = {k: tmp_path / k for k in
             ("raw", "store", "batches", "decisions", "spend", "productions", "artifacts")}
    paths["artifacts"].mkdir(parents=True)
    write_table(paths["store"], "enron", "qrels_raw", pa.table({
        "corpus": ["enron"] * len(JUDGED), "topic": ["201"] * len(JUDGED),
        "trec_doc_id": [d for d, *_ in JUDGED], "relevance": [r for _, _, r, _ in JUDGED],
        "stratum": [s for _, s, *_ in JUDGED], "sampling_weight": [w for *_, w in JUDGED],
    }).cast(QRELS_RAW))

    tarball = paths["raw"] / "enron" / "trec" / "edrmv2txt-v2.tar.bz2"
    tarball.parent.mkdir(parents=True)
    docids = tarball_docids if tarball_docids is not None else [d for d, *_ in JUDGED]
    with tarfile.open(tarball, "w:bz2") as tar:
        for docid in docids + ["3.9.UNJUDGED"]:
            data = ("attachment text\n" if is_part(docid) else EMAIL).encode()
            info = tarfile.TarInfo(f"edrm-enron-v2_jones-t_xml.zip/text_000/{docid}.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    (tmp_path / "protocols" / "enron").mkdir(parents=True)
    (tmp_path / "protocols" / "enron" / "topic201.v1.md").write_text(
        "Synthetic protocol: decide responsiveness, output JSON.")
    (paths["artifacts"] / "devset_enron_201.json").write_text(
        json.dumps({"doc_ids": {"rel1": ["3.1.AAA"], "rel0": ["3.2.BBB"]}}))

    cfg = PipelineConfig(
        paths=paths,
        models={"tier1": "model-a", "tier2": "model-b", "narrative": "model-b"},
        batch={"max_requests_per_batch": 10000, "poll_interval_seconds": 0,
               "max_output_tokens": 64},
        seeds={"devset": 42, "qc_sample": 1, "elusion": 7},
        sampling={"devset_per_topic_max": 500, "devset_relevant_target": 150,
                  "qc_fraction": 0.5, "elusion_sample_size": 5},
        normalization_version="v1",
        ingest={"judged_text_max_body_chars": 1000},
    )
    budget = BudgetConfig(
        prices={"model-a": {"input": 1.0, "output": 1.0},
                "model-b": {"input": 5.0, "output": 5.0}},
        caps={"dev_loop": 25.0, "bush_sample": 0.0, "enron_review": 10.0},
        total_stop_usd=100.0,
    )
    corpus_cfg = CorpusConfig(corpus="enron", format="edrm-xml-v2", files=[],
                              chosen_topics=["201"])
    for mod in (trec_text, review_run, validate_run):
        monkeypatch.setattr(mod, "load_pipeline_config", lambda: cfg)
    monkeypatch.setattr(trec_text, "load_corpus_config", lambda _c: corpus_cfg)
    monkeypatch.setattr(review_run, "load_budget_config", lambda: budget)
    monkeypatch.setattr(validate_run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(config_mod, "REPO_ROOT", tmp_path)
    return cfg


def test_ingest_judged_text_loads_only_judged_docs(tmp_path, monkeypatch):
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch)
    assert trec_text.main(Namespace(force=False)) == 0
    idmap = read_table(cfg.paths["store"], "enron", "judged_doc_id_map").to_pylist()
    assert sorted(r["trec_doc_id"] for r in idmap) == sorted(d for d, *_ in JUDGED)
    report = json.loads((cfg.paths["artifacts"] / "enron_judged_text_report.json").read_text())
    assert report["missing"] == 0 and report["topics"]["201"]["parts"] == 1
    rows = read_table(cfg.paths["store"], "enron", "judged_messages").to_pylist()
    email_row = next(r for r in rows if "3.1.AAA" in r["source_file"])
    assert email_row["subject"].startswith("synthetic subject")
    # resume reads the persisted gate
    assert trec_text.main(Namespace(force=False)) == 0


def test_ingest_judged_text_coverage_gate_fails_on_missing(tmp_path, monkeypatch):
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch, tarball_docids=["3.1.AAA", "3.2.BBB"])
    assert trec_text.main(Namespace(force=False)) == 1
    report = json.loads((cfg.paths["artifacts"] / "enron_judged_text_report.json").read_text())
    assert report["missing_docids"] == ["3.3.CCC.1", "3.4.DDD"]
    assert trec_text.main(Namespace(force=False)) == 1  # resume keeps the gate red


def review_args(**kw):
    base = dict(corpus="enron", topic="201", tier=1, phase="responsiveness",
                dev_set=False, dry_run=True)
    return Namespace(**(base | kw))


def test_enron_full_run_scopes_all_judged_docs_and_bills_enron_review(
        tmp_path, monkeypatch, capsys, mock_client_factory):
    import pipeline.review.batch as batch_mod
    import pipeline.review.run as review_run
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch)
    assert trec_text.main(Namespace(force=False)) == 0
    assert review_run.main(review_args()) == 0
    out = capsys.readouterr().out
    assert "candidates: 4" in out and "enron_review" in out

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")
    monkeypatch.setattr(batch_mod, "AnthropicBatchClient", lambda: mock_client_factory())
    assert review_run.main(review_args(dry_run=False)) == 0
    spend = [json.loads(x) for x in
             (cfg.paths["spend"] / "spend.jsonl").read_text().splitlines()]
    assert {r["phase"] for r in spend} == {"enron_review"}


def test_enron_dev_set_dry_run_uses_devset(tmp_path, monkeypatch, capsys):
    import pipeline.review.run as review_run
    from pipeline.ingest import trec_text

    make_env(tmp_path, monkeypatch)
    assert trec_text.main(Namespace(force=False)) == 0
    assert review_run.main(review_args(dev_set=True)) == 0
    out = capsys.readouterr().out
    assert "candidates: 2" in out and "dev_loop" in out


def _seed_decisions(cfg, responsive_trec_ids):
    idmap = read_table(cfg.paths["store"], "enron", "judged_doc_id_map").to_pylist()
    recs = [
        dec_mod.new_decision(
            doc_id=r["doc_id"], corpus="enron", topic="201", phase="responsiveness",
            tier=1, model="m", prompt_version="topic201.v1", prompt_hash="abc",
            batch_id="b1",
            decision="responsive" if r["trec_doc_id"] in responsive_trec_ids
            else "not_responsive",
            confidence=0.9, rationale="synthetic", input_tokens=1, output_tokens=1,
            cost_usd=0.0,
        )
        for r in idmap
    ]
    dec_mod.append(cfg.paths["decisions"], "enron", recs)


def test_enron_validate_full_is_weighted(tmp_path, monkeypatch):
    import pipeline.validate.run as validate_run
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch)
    assert trec_text.main(Namespace(force=False)) == 0
    # finds the stratum-100 relevant doc, misses the weight-3 relevant part
    _seed_decisions(cfg, {"3.1.AAA"})
    assert validate_run.main(Namespace(corpus="enron", topic="201", dev_set=False)) == 0
    payload = json.loads((cfg.paths["artifacts"] / "metrics_enron_201.json").read_text())
    assert payload["weighted"] is True
    assert payload["estimator"] == "weighted_horvitz_thompson"
    assert payload["tp"] == 1.0 and payload["fn"] == 3.0
    assert payload["recall"] == 0.25 and payload["precision"] == 1.0


def test_enron_validate_dev_is_unweighted_diagnostic(tmp_path, monkeypatch):
    import pipeline.validate.run as validate_run
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch)
    assert trec_text.main(Namespace(force=False)) == 0
    _seed_decisions(cfg, {"3.1.AAA"})
    assert validate_run.main(Namespace(corpus="enron", topic="201", dev_set=True)) == 0
    payload = json.loads(
        (cfg.paths["artifacts"] / "metrics_enron_201_dev.json").read_text())
    assert payload["weighted"] is False
    assert payload["estimator"] == "unweighted_dev_diagnostic"


def test_enron_validate_full_refuses_unmapped_judged_docs(tmp_path, monkeypatch):
    import pipeline.validate.run as validate_run
    from pipeline.ingest import trec_text

    cfg = make_env(tmp_path, monkeypatch, tarball_docids=["3.1.AAA", "3.2.BBB"])
    assert trec_text.main(Namespace(force=False)) == 1  # gate red, tables still written
    _seed_decisions(cfg, {"3.1.AAA"})
    assert validate_run.main(Namespace(corpus="enron", topic="201", dev_set=False)) == 2
