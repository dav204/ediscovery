import pandas as pd
import pytest

from pipeline.review import tiering

pytestmark = pytest.mark.phase2


def _df(rows):
    base = {
        "decision_id": None, "topic": "athome102", "tier": 1,
        "prompt_version": "p.v1", "supersedes": None,
    }
    out = []
    for i, row in enumerate(rows):
        r = dict(base)
        r["decision_id"] = f"id{i}"
        r["doc_id"] = f"doc{i}"
        r.update(row)
        out.append(r)
    return pd.DataFrame(out)


def test_borderline_routing():
    df = _df([
        {"decision": "responsive"},
        {"decision": "borderline"},
        {"decision": "borderline", "topic": "other"},
        {"decision": "borderline", "tier": 2},
        {"decision": "borderline", "prompt_version": "p.v2"},
    ])
    assert tiering.borderline_doc_ids(df, topic="athome102", prompt_version="p.v1") == {"doc1"}


def test_qc_sample_deterministic_and_stratified():
    rows = [{"decision": "responsive"} for _ in range(40)]
    rows += [{"decision": "not_responsive"} for _ in range(60)]
    df = _df(rows)
    s1 = tiering.qc_sample_doc_ids(df, topic="athome102", prompt_version="p.v1",
                                   fraction=0.10, seed=7)
    s2 = tiering.qc_sample_doc_ids(df, topic="athome102", prompt_version="p.v1",
                                   fraction=0.10, seed=7)
    assert s1 == s2  # deterministic for a given seed
    responsive_ids = {f"doc{i}" for i in range(40)}
    assert len(s1 & responsive_ids) == 4  # 10% of each stratum
    assert len(s1 - responsive_ids) == 6
    s3 = tiering.qc_sample_doc_ids(df, topic="athome102", prompt_version="p.v1",
                                   fraction=0.10, seed=8)
    assert s1 != s3  # seed actually matters


def test_empty_frames():
    empty = pd.DataFrame()
    assert tiering.borderline_doc_ids(empty, topic="t", prompt_version="p") == set()
    assert tiering.qc_sample_doc_ids(empty, topic="t", prompt_version="p",
                                     fraction=0.05, seed=1) == set()
