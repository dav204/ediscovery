"""Tier routing: which docs go to the tier-2 (senior-attorney) model.

Two routes out of tier 1:
  borderline — every doc tier 1 marked borderline
  qc         — a seeded, stratified sample of confident tier-1 calls
               (equal-rate sample from responsive and not_responsive)
"""

import random

import pandas as pd

from .decisions import current


def borderline_doc_ids(decisions_df: pd.DataFrame, *, topic: str, prompt_version: str) -> set[str]:
    cur = current(decisions_df)
    if cur.empty:
        return set()
    mask = (
        (cur["topic"].astype(str) == str(topic))
        & (cur["tier"] == 1)
        & (cur["prompt_version"] == prompt_version)
        & (cur["decision"] == "borderline")
    )
    return set(cur[mask]["doc_id"])


def qc_sample_doc_ids(decisions_df: pd.DataFrame, *, topic: str, prompt_version: str,
                      fraction: float, seed: int) -> set[str]:
    cur = current(decisions_df)
    if cur.empty:
        return set()
    sampled: set[str] = set()
    for decision in ("responsive", "not_responsive"):
        mask = (
            (cur["topic"].astype(str) == str(topic))
            & (cur["tier"] == 1)
            & (cur["prompt_version"] == prompt_version)
            & (cur["decision"] == decision)
        )
        ids = sorted(cur[mask]["doc_id"])  # sorted -> deterministic for a given seed
        k = round(len(ids) * fraction)
        sampled.update(random.Random(f"{seed}:{decision}").sample(ids, k))
    return sampled
