"""Metrics validated against hand-computed confusion matrices."""

import pandas as pd
import pytest

from pipeline.validate.metrics import evaluate

pytestmark = pytest.mark.phase2


def _qrels(rows):
    return pd.DataFrame(
        [{"topic": "t1", "sampling_weight": 1.0, **r} for r in rows]
    )


def _decisions(pairs):
    return pd.DataFrame([{"doc_id": d, "decision": v} for d, v in pairs])


def test_hand_computed_unweighted():
    # 20 judged docs: 8 relevant, 12 not.
    # Model produces 7: 6 of the relevant (TP=6, FN=2), 1 irrelevant (FP=1, TN=11).
    # recall = 6/8 = 0.75; precision = 6/7 = 0.8571; F1 = 2*.75*.8571/1.6071 = 0.8
    qrels = _qrels(
        [{"doc_id": f"r{i}", "relevance": 1} for i in range(8)]
        + [{"doc_id": f"n{i}", "relevance": 0} for i in range(12)]
    )
    produced = [(f"r{i}", "responsive") for i in range(6)] + [("n0", "responsive")]
    held = [(f"r{i}", "not_responsive") for i in range(6, 8)]
    held += [(f"n{i}", "not_responsive") for i in range(1, 12)]
    result = evaluate(_decisions(produced + held), qrels, topic="t1")
    assert (result.tp, result.fp, result.fn, result.tn) == (6, 1, 2, 11)
    assert result.recall == pytest.approx(0.75)
    assert result.precision == pytest.approx(0.8571, abs=1e-4)
    assert result.f1 == pytest.approx(0.8, abs=1e-4)
    assert result.n_judged == 20 and result.n_evaluated == 20


def test_weighted_strata():
    # Two strata: weight 1 (fully judged) and weight 10 (1-in-10 sample).
    # TP: 2 docs at w=1, 1 doc at w=10 -> 12. FN: 1 doc at w=10 -> 10.
    # recall = 12/22; precision: FP = 1 doc at w=1 -> 12/13.
    qrels = pd.DataFrame(
        [
            {"topic": "t1", "doc_id": "a", "relevance": 1, "sampling_weight": 1.0},
            {"topic": "t1", "doc_id": "b", "relevance": 1, "sampling_weight": 1.0},
            {"topic": "t1", "doc_id": "c", "relevance": 1, "sampling_weight": 10.0},
            {"topic": "t1", "doc_id": "d", "relevance": 1, "sampling_weight": 10.0},
            {"topic": "t1", "doc_id": "e", "relevance": 0, "sampling_weight": 1.0},
        ]
    )
    decisions = _decisions(
        [("a", "responsive"), ("b", "responsive"), ("c", "responsive"),
         ("d", "not_responsive"), ("e", "responsive")]
    )
    result = evaluate(decisions, qrels, topic="t1", weighted=True)
    assert result.tp == pytest.approx(12.0)
    assert result.fn == pytest.approx(10.0)
    assert result.recall == pytest.approx(12 / 22, abs=1e-4)
    assert result.precision == pytest.approx(12 / 13, abs=1e-4)


def test_borderline_counts_as_not_produced():
    qrels = _qrels([{"doc_id": "a", "relevance": 1}, {"doc_id": "b", "relevance": 1}])
    decisions = _decisions([("a", "responsive"), ("b", "borderline")])
    result = evaluate(decisions, qrels, topic="t1")
    assert result.recall == pytest.approx(0.5)  # borderline b -> FN, conservative
    assert result.notes["borderline_in_eval"] == 1


def test_judged_without_decision_excluded_but_reported():
    qrels = _qrels([{"doc_id": "a", "relevance": 1}, {"doc_id": "z", "relevance": 0}])
    result = evaluate(_decisions([("a", "responsive")]), qrels, topic="t1")
    assert result.n_evaluated == 1
    assert result.notes["judged_without_decision"] == 1


def test_unknown_topic_raises():
    with pytest.raises(ValueError, match="no qrels"):
        evaluate(_decisions([("a", "responsive")]), _qrels([{"doc_id": "a", "relevance": 1}]),
                 topic="other")
