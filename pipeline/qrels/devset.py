"""Seeded stratified dev sets: the judged-doc samples used for protocol iteration.

Per topic, sample from qrels_raw stratified by relevance grade:
  relevant   up to sampling.devset_relevant_target docs, split evenly across the
             positive grades present (2=important, 1=relevant), backfilling one
             grade from the other when a stratum runs short;
  nonrel     judged rel=0 docs to fill up to sampling.devset_per_topic_max.

Output artifacts/devset_{corpus}_{topic}.json holds sorted ID lists keyed by
grade. Byte-identical across re-runs (P1 gate): the RNG is seeded from
seeds.devset + corpus + topic only, and the output carries no timestamp.
"""

import json
import random
import sys
from collections import defaultdict

from ..config import load_corpus_config, load_pipeline_config
from ..store import read_table


def build_devset(corpus: str, topic: str, qrels, seed: int, per_topic_max: int,
                 relevant_target: int) -> dict:
    pools: dict[int, list[str]] = defaultdict(list)
    for row in qrels.to_pylist():
        if row["corpus"] == corpus and row["topic"] == topic:
            pools[row["relevance"]].append(row["trec_doc_id"])
    if not pools:
        raise ValueError(f"no qrels for corpus={corpus!r} topic={topic!r}")

    def sample(grade: int, k: int) -> list[str]:
        pool = sorted(pools.get(grade, []))
        rng = random.Random(f"{seed}:{corpus}:{topic}:{grade}")
        return sorted(rng.sample(pool, min(k, len(pool))))

    positive_grades = sorted((g for g in pools if g > 0), reverse=True)
    per_grade = {g: len(pools[g]) for g in positive_grades}
    quota = {}
    remaining = relevant_target
    # Even split across positive grades; grades with small pools donate their
    # unused share to the next grade.
    for i, grade in enumerate(positive_grades):
        share = remaining // (len(positive_grades) - i)
        take = min(share, per_grade[grade])
        quota[grade] = take
        remaining -= take
    # Second pass: backfill leftover target from grades with spare capacity.
    for grade in positive_grades:
        if remaining <= 0:
            break
        extra = min(remaining, per_grade[grade] - quota[grade])
        quota[grade] += extra
        remaining -= extra

    selected = {grade: sample(grade, k) for grade, k in quota.items()}
    n_relevant = sum(len(ids) for ids in selected.values())
    selected[0] = sample(0, per_topic_max - n_relevant)

    return {
        "corpus": corpus,
        "topic": topic,
        "seed": seed,
        "counts": {
            f"rel{g}": {"judged": len(pools.get(g, [])), "sampled": len(ids)}
            for g, ids in sorted(selected.items(), reverse=True)
        },
        "doc_ids": {f"rel{g}": ids for g, ids in sorted(selected.items(), reverse=True)},
    }


def main(args) -> int:
    if args.corpus != "bush":
        print("devset: only --corpus bush is implemented (enron is P5)", file=sys.stderr)
        return 2
    cfg = load_pipeline_config()
    corpus_cfg = load_corpus_config(args.corpus)
    topics = [args.topic] if args.topic else corpus_cfg.chosen_topics
    if not topics:
        print("devset: no --topic given and chosen_topics is empty in the corpus config",
              file=sys.stderr)
        return 2
    qrels = read_table(cfg.paths["store"], args.corpus, "qrels_raw")
    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        devset = build_devset(
            args.corpus, topic, qrels,
            seed=cfg.seeds["devset"],
            per_topic_max=cfg.sampling["devset_per_topic_max"],
            relevant_target=cfg.sampling["devset_relevant_target"],
        )
        path = artifacts / f"devset_{args.corpus}_{topic}.json"
        path.write_text(json.dumps(devset, indent=2, sort_keys=True) + "\n")
        sampled = {k: v["sampled"] for k, v in devset["counts"].items()}
        print(f"devset {args.corpus}/{topic}: {sampled} -> {path}")
    return 0
