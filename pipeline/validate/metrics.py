"""Recall / precision / F1 against TREC qrels.

Two estimators:
  unweighted — for (near-)complete judgments (Bush athome collections)
  weighted   — Horvitz-Thompson style, using qrels sampling_weight, for the
               stratified TREC 2010 Legal judgments (Enron)

Evaluation universe = judged docs that are mapped to a pipeline doc_id AND have
a current decision. Docs the model called `borderline` that never got a tier-2
decision count as not-produced (conservative: hurts recall if they were
relevant). Coverage numbers are reported alongside so the denominator is
always explicit.
"""

from dataclasses import dataclass, field

import pandas as pd

PRODUCED = {"responsive"}  # decisions that mean "this doc goes in the production"


@dataclass
class MetricsResult:
    topic: str
    n_judged: int
    n_evaluated: int  # judged docs with a decision
    tp: float
    fp: float
    fn: float
    tn: float
    recall: float
    precision: float
    f1: float
    weighted: bool
    notes: dict = field(default_factory=dict)


def _prf(tp: float, fp: float, fn: float) -> tuple[float, float, float]:
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return recall, precision, f1


def evaluate(decisions: pd.DataFrame, qrels: pd.DataFrame, *, topic: str,
             weighted: bool = False) -> MetricsResult:
    """`decisions`: current decisions with columns doc_id, decision.
    `qrels`: columns doc_id (already mapped from trec_doc_id), relevance,
             sampling_weight (1.0 where judgments are complete).
    """
    q = qrels[qrels["topic"].astype(str) == str(topic)].copy()
    if q.empty:
        raise ValueError(f"no qrels for topic {topic!r}")
    q["relevant"] = q["relevance"] > 0
    if not weighted:
        q["sampling_weight"] = 1.0
    if (q["sampling_weight"] <= 0).any():
        raise ValueError("non-positive sampling_weight in qrels")

    # m:1 guard: a doc with two current decisions (e.g. an unfiltered protocol
    # version mix) would silently fan out and double-count in the matrix.
    merged = q.merge(
        decisions[["doc_id", "decision"]], on="doc_id", how="left", validate="m:1"
    )
    evaluated = merged[merged["decision"].notna()]
    produced = evaluated["decision"].isin(PRODUCED)
    w = evaluated["sampling_weight"]

    tp = float(w[produced & evaluated["relevant"]].sum())
    fp = float(w[produced & ~evaluated["relevant"]].sum())
    fn = float(w[~produced & evaluated["relevant"]].sum())
    tn = float(w[~produced & ~evaluated["relevant"]].sum())
    recall, precision, f1 = _prf(tp, fp, fn)

    return MetricsResult(
        topic=str(topic),
        n_judged=len(q),
        n_evaluated=len(evaluated),
        tp=tp, fp=fp, fn=fn, tn=tn,
        recall=round(recall, 4),
        precision=round(precision, 4),
        f1=round(f1, 4),
        weighted=weighted,
        notes={
            "judged_without_decision": int(len(merged) - len(evaluated)),
            "borderline_in_eval": int((evaluated["decision"] == "borderline").sum()),
        },
    )
