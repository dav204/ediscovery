import pytest

from pipeline.review import decisions as dec

pytestmark = pytest.mark.phase2


def _decision(doc_id="bush-0000000000000001", decision="responsive", tier=1,
              supersedes=None, prompt_version="fixture_topic.v1"):
    return dec.new_decision(
        doc_id=doc_id, corpus="bush", topic="athome102", phase="responsiveness",
        tier=tier, model="claude-haiku-4-5", prompt_version=prompt_version,
        prompt_hash="abc123def456", batch_id="batch_001", decision=decision,
        confidence=0.8, rationale="synthetic", input_tokens=100, output_tokens=20,
        cost_usd=0.0001, supersedes=supersedes,
    )


def test_custom_id_roundtrip():
    cid = dec.make_custom_id("bush-0000000000000001", "athome102", "responsiveness", 1,
                             "athome102.v1")
    assert len(cid) <= 64
    parsed = dec.parse_custom_id(cid)
    assert parsed == {
        "doc_id": "bush-0000000000000001",
        "topic": "athome102",
        "phase": "responsiveness",
        "tier": 1,
        "prompt_version": "athome102.v1",
    }


def test_custom_id_length_guard():
    with pytest.raises(ValueError, match="64-char"):
        dec.make_custom_id("bush-0000000000000001", "athome102" * 5, "responsiveness", 1,
                           "athome102.v1")


def test_invalid_decision_rejected():
    with pytest.raises(ValueError, match="invalid for phase"):
        _decision(decision="privileged")
    with pytest.raises(ValueError, match="confidence"):
        dec.new_decision(
            doc_id="d", corpus="bush", topic="t", phase="responsiveness", tier=1,
            model="m", prompt_version="p.v1", prompt_hash="h", batch_id="b",
            decision="responsive", confidence=1.5, rationale="r",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
        )


def test_append_load_roundtrip(tmp_path):
    records = [_decision(), _decision(doc_id="bush-0000000000000002")]
    dec.append(tmp_path, "bush", records)
    df = dec.load(tmp_path, "bush")
    assert len(df) == 2
    assert set(df["doc_id"]) == {"bush-0000000000000001", "bush-0000000000000002"}


def test_load_empty(tmp_path):
    assert dec.load(tmp_path, "bush").empty


def test_supersedes_chain(tmp_path):
    tier1 = _decision(decision="borderline")
    tier2 = _decision(decision="responsive", tier=2, supersedes=tier1["decision_id"])
    dec.append(tmp_path, "bush", [tier1, tier2])
    cur = dec.current(dec.load(tmp_path, "bush"))
    assert len(cur) == 1
    assert cur.iloc[0]["tier"] == 2
    assert cur.iloc[0]["decision"] == "responsive"


def test_scored_doc_ids_idempotency_key(tmp_path):
    dec.append(tmp_path, "bush", [_decision()])
    df = dec.load(tmp_path, "bush")
    common = {"topic": "athome102", "phase": "responsiveness", "tier": 1}
    assert dec.scored_doc_ids(df, prompt_version="fixture_topic.v1", **common) == {
        "bush-0000000000000001"
    }
    # A protocol version bump means the doc must be re-scored.
    assert dec.scored_doc_ids(df, prompt_version="fixture_topic.v2", **common) == set()
