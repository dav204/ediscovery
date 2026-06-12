from pathlib import Path

import pytest

from pipeline.config import BudgetConfig
from pipeline.review import decisions as dec
from pipeline.review.batch import RunConfig, build_requests, parse_model_output, run_batches
from pipeline.review.prompts import load_protocol

pytestmark = pytest.mark.phase2

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def protocol():
    return load_protocol(FIXTURES / "fixture_topic.v1.md")


@pytest.fixture
def budget():
    return BudgetConfig(
        prices={"claude-haiku-4-5": {"input": 0.50, "output": 2.50}},
        caps={"dev_loop": 25.0},
        total_stop_usd=200.0,
    )


def _candidates(n):
    return [
        {
            "doc_id": f"bush-{i:016x}",
            "custodian": "bush-office",
            "from_addr": "x@example.com",
            "to": [], "cc": [],
            "date_raw": "Mon, 1 Jan 2001 00:00:00 +0000",
            "subject": f"synthetic {i}",
            "attachment_names": [],
            "body_text": "synthetic body",
            "token_estimate": 100,
        }
        for i in range(n)
    ]


def _run_cfg(**overrides):
    defaults = dict(
        corpus="bush", topic="athome102", phase="responsiveness", tier=1,
        model="claude-haiku-4-5", budget_phase="dev_loop", max_output_tokens=256,
        max_requests_per_batch=10000, poll_interval_seconds=0,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def test_parse_model_output_variants():
    assert parse_model_output('{"decision": "responsive", "confidence": 0.9, "rationale": "r"}')["decision"] == "responsive"
    fenced = '```json\n{"decision": "borderline", "confidence": 0.5, "rationale": "r"}\n```'
    assert parse_model_output(fenced)["decision"] == "borderline"
    with pytest.raises(ValueError):
        parse_model_output("no json here")
    with pytest.raises(ValueError, match="missing keys"):
        parse_model_output('{"decision": "responsive"}')


def test_build_requests_custom_ids(protocol):
    reqs = build_requests(_candidates(2), protocol, _run_cfg())
    assert len(reqs) == 2
    parsed = dec.parse_custom_id(reqs[0]["custom_id"])
    assert parsed["phase"] == "responsiveness"
    assert reqs[0]["params"]["model"] == "claude-haiku-4-5"


def test_chunking_at_batch_cap(protocol, budget, tmp_path, mock_client_factory):
    client = mock_client_factory()
    out = run_batches(
        _candidates(25), protocol, _run_cfg(max_requests_per_batch=10), client,
        budget, tmp_path / "spend.jsonl", tmp_path / "batches", sleep=lambda s: None,
    )
    assert [len(b) for b in client.created_batches] == [10, 10, 5]
    assert len(out["decisions"]) == 25
    assert not out["stopped_early"]


def test_lifecycle_with_failures(protocol, budget, tmp_path, mock_client_factory):
    candidates = _candidates(4)
    cid = lambda i: dec.make_custom_id(candidates[i]["doc_id"], "athome102",
                                       "responsiveness", 1, protocol.version)
    client = mock_client_factory(
        script={
            cid(0): {"decision": "responsive", "confidence": 0.95, "rationale": "r"},
            cid(1): "ERRORED",
            cid(2): "EXPIRED",
            cid(3): "model rambled with no json",
        },
        pending_polls=2,
    )
    out = run_batches(candidates, protocol, _run_cfg(), client, budget,
                      tmp_path / "spend.jsonl", tmp_path / "batches", sleep=lambda s: None)
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["decision"] == "responsive"
    assert out["decisions"][0]["prompt_hash"] == protocol.content_hash
    assert {f["type"] for f in out["failures"]} == {"errored", "expired", "unparseable"}
    # Request/result payloads persisted for the audit trail.
    assert (tmp_path / "batches" / "batch_001.requests.jsonl").exists()
    assert (tmp_path / "batches" / "batch_001.results.jsonl").exists()
    # Spend recorded from actual usage (2 succeeded * (100 in + 20 out)).
    import json
    rec = json.loads((tmp_path / "spend.jsonl").read_text().splitlines()[0])
    assert rec["input_tokens"] == 200
    assert rec["output_tokens"] == 40


def test_failed_docs_become_candidates_again(protocol, budget, tmp_path, mock_client_factory):
    """The idempotency loop: errored docs have no decision, so a re-run targets
    exactly them."""
    candidates = _candidates(3)
    cid = lambda i: dec.make_custom_id(candidates[i]["doc_id"], "athome102",
                                       "responsiveness", 1, protocol.version)
    client = mock_client_factory(script={cid(1): "ERRORED"})
    out = run_batches(candidates, protocol, _run_cfg(), client, budget,
                      tmp_path / "spend.jsonl", tmp_path / "batches", sleep=lambda s: None)
    dec.append(tmp_path, "bush", out["decisions"])
    scored = dec.scored_doc_ids(
        dec.load(tmp_path, "bush"), topic="athome102", phase="responsiveness",
        tier=1, prompt_version=protocol.version,
    )
    remaining = [c for c in candidates if c["doc_id"] not in scored]
    assert [c["doc_id"] for c in remaining] == [candidates[1]["doc_id"]]


def test_budget_stop_between_chunks(protocol, tmp_path, mock_client_factory):
    # Cap sized so chunk 1 fits but chunk 2's projection crosses it.
    tight = BudgetConfig(
        prices={"claude-haiku-4-5": {"input": 0.50, "output": 2.50}},
        caps={"dev_loop": 0.009}, total_stop_usd=200.0,
    )
    client = mock_client_factory(tokens=(4000, 200))
    out = run_batches(
        _candidates(20), protocol, _run_cfg(max_requests_per_batch=10), client,
        tight, tmp_path / "spend.jsonl", tmp_path / "batches", sleep=lambda s: None,
    )
    assert out["stopped_early"]
    assert len(client.created_batches) == 1  # second chunk never submitted
    assert len(out["decisions"]) == 10  # first chunk drained, not abandoned
    assert "stop_reason" in out
