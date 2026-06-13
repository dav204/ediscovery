"""HiCAL-sample qrels parsing from synthetic files (IDs only, no doc content)."""

import pytest

from pipeline.qrels.parse import _read_sample_topics, parse_bush
from pipeline.store import QRELS_RAW


def make_bush_raw(tmp_path, topics=("401", "402"), qrel_rows=None):
    """Lay out synthetic HiCAL athome4.topics.sample + athome4.qrel.sample."""
    bush = tmp_path / "bush"
    bush.mkdir(parents=True)
    topic_lines = [f"{t} 1  Topic {t} -- A synthetic description for {t}." for t in topics]
    (bush / "athome4.topics.sample").write_text("\n".join(topic_lines) + "\n")
    # TREC qrel format: `topic 0 docno rel`.
    qrel_lines = [f"{topic} 0 {docno} {rel}" for topic, docno, rel in qrel_rows or []]
    (bush / "athome4.qrel.sample").write_text("\n".join(qrel_lines) + "\n")
    return bush


def test_parse_bush_emits_qrels_for_sample_topics(tmp_path):
    make_bush_raw(
        tmp_path,
        topics=("401", "402"),
        qrel_rows=[("401", "000010", 0), ("401", "000011", 2), ("402", "000012", 1)],
    )
    table = parse_bush(tmp_path)
    table.cast(QRELS_RAW)  # raises if the schema drifts from store.QRELS_RAW
    rows = {(r["topic"], r["trec_doc_id"]): r for r in table.to_pylist()}
    assert set(rows) == {("401", "000010"), ("401", "000011"), ("402", "000012")}
    assert rows[("401", "000011")]["relevance"] == 2
    assert all(r["corpus"] == "bush" for r in rows.values())
    assert all(r["stratum"] is None and r["sampling_weight"] is None for r in rows.values())


def test_parse_bush_restricts_to_sample_topics(tmp_path):
    # qrel.sample ships all 34 athome4 topics; only the topics.sample set is kept.
    make_bush_raw(
        tmp_path,
        topics=("401",),
        qrel_rows=[("401", "000010", 1), ("427", "000099", 2), ("434", "000098", 1)],
    )
    table = parse_bush(tmp_path)
    topics = {r["topic"] for r in table.to_pylist()}
    assert topics == {"401"}


def test_parse_bush_dedups_by_max_relevance(tmp_path):
    make_bush_raw(
        tmp_path,
        topics=("401",),
        qrel_rows=[("401", "000010", 0), ("401", "000010", 2), ("401", "000010", 1)],
    )
    table = parse_bush(tmp_path)
    rows = table.to_pylist()
    assert len(rows) == 1
    assert rows[0]["relevance"] == 2


def test_parse_bush_raises_when_no_judgments_for_sample_topics(tmp_path):
    make_bush_raw(tmp_path, topics=("401",), qrel_rows=[("427", "000099", 2)])
    with pytest.raises(ValueError, match="no judgments"):
        parse_bush(tmp_path)


def test_read_sample_topics_takes_first_field(tmp_path):
    bush = make_bush_raw(tmp_path, topics=("401", "402", "409"))
    assert _read_sample_topics(bush / "athome4.topics.sample") == ["401", "402", "409"]


def test_read_sample_topics_rejects_empty(tmp_path):
    path = tmp_path / "empty.topics"
    path.write_text("\n")
    with pytest.raises(ValueError, match="no topics"):
        _read_sample_topics(path)
