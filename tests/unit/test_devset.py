"""Dev-set sampling: determinism, stratification quotas, shortfall backfill."""

import json

import pyarrow as pa
import pytest

from pipeline.qrels.devset import build_devset


def qrels_table(rows):
    return pa.table(
        {
            "corpus": [r[0] for r in rows],
            "topic": [r[1] for r in rows],
            "trec_doc_id": [r[2] for r in rows],
            "relevance": [r[3] for r in rows],
            "stratum": [None] * len(rows),
            "sampling_weight": [None] * len(rows),
        }
    )


def synthetic(n0=300, n1=120, n2=90, topic="401"):
    rows = []
    rows += [("bush", topic, f"N{i:05d}", 0) for i in range(n0)]
    rows += [("bush", topic, f"R{i:05d}", 1) for i in range(n1)]
    rows += [("bush", topic, f"H{i:05d}", 2) for i in range(n2)]
    return qrels_table(rows)


def test_devset_is_deterministic():
    qrels = synthetic()
    a = build_devset("bush", "401", qrels, seed=42, per_topic_max=200, relevant_target=100)
    b = build_devset("bush", "401", qrels, seed=42, per_topic_max=200, relevant_target=100)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = build_devset("bush", "401", qrels, seed=43, per_topic_max=200, relevant_target=100)
    assert a["doc_ids"] != c["doc_ids"]


def test_devset_quotas_split_target_across_grades():
    devset = build_devset("bush", "401", synthetic(), seed=42, per_topic_max=200,
                          relevant_target=100)
    assert devset["counts"]["rel2"]["sampled"] == 50
    assert devset["counts"]["rel1"]["sampled"] == 50
    assert devset["counts"]["rel0"]["sampled"] == 100
    ids = devset["doc_ids"]
    assert all(i.startswith("H") for i in ids["rel2"])
    assert all(i.startswith("R") for i in ids["rel1"])
    assert all(i.startswith("N") for i in ids["rel0"])
    assert ids["rel2"] == sorted(ids["rel2"])


def test_devset_backfills_short_grade_from_other():
    devset = build_devset("bush", "401", synthetic(n2=10), seed=42, per_topic_max=200,
                          relevant_target=100)
    assert devset["counts"]["rel2"]["sampled"] == 10
    assert devset["counts"]["rel1"]["sampled"] == 90
    assert devset["counts"]["rel0"]["sampled"] == 100


def test_devset_caps_at_available_judgments():
    devset = build_devset("bush", "401", synthetic(n0=30, n1=5, n2=5), seed=42,
                          per_topic_max=500, relevant_target=150)
    assert devset["counts"]["rel2"]["sampled"] == 5
    assert devset["counts"]["rel1"]["sampled"] == 5
    assert devset["counts"]["rel0"]["sampled"] == 30


def test_devset_binary_qrels_have_single_positive_grade():
    rows = [("bush", "409", f"D{i:05d}", 1) for i in range(40)]
    devset = build_devset("bush", "409", qrels_table(rows), seed=42,
                          per_topic_max=500, relevant_target=20)
    assert devset["counts"]["rel1"]["sampled"] == 20
    assert devset["counts"]["rel0"]["sampled"] == 0


def test_devset_unknown_topic_raises():
    with pytest.raises(ValueError, match="no qrels"):
        build_devset("bush", "999", synthetic(), seed=42, per_topic_max=200,
                     relevant_target=100)
