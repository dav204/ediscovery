import pytest

from pipeline.ids import doc_id

pytestmark = pytest.mark.phase0


def test_stable_across_calls():
    assert doc_id("bush", "athome1/123.txt", 0) == doc_id("bush", "athome1/123.txt", 0)


def test_known_value_pinned():
    # Pinned so any change to the derivation rule fails loudly: existing decision
    # logs and qrels mappings would all be orphaned by a silent change.
    assert doc_id("bush", "athome1/123.txt", 0) == "bush-d4643464a6aa6375"


def test_format():
    d = doc_id("enron", "pst/skilling-j/a.pst", 42)
    prefix, digest = d.rsplit("-", 1)
    assert prefix == "enron"
    assert len(digest) == 16
    assert int(digest, 16) >= 0


def test_distinct_inputs_distinct_ids():
    ids = {
        doc_id("enron", "pst/a.pst", 1),
        doc_id("enron", "pst/a.pst", 2),
        doc_id("enron", "pst/b.pst", 1),
        doc_id("bush", "pst/a.pst", 1),
    }
    assert len(ids) == 4


def test_rejects_bad_inputs():
    with pytest.raises(ValueError):
        doc_id("opioid", "x", 0)
    with pytest.raises(ValueError):
        doc_id("bush", "", 0)
    with pytest.raises(ValueError):
        doc_id("bush", "x", -1)
