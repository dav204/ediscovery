"""Parse the HiCAL-sample relevance judgments into the qrels_raw table.

Bush corpus source (data/raw/bush/, from github.com/hical/sample-dataset):
  athome4.topics.sample  one line per sample topic: `<topic> <n> <title> -- <desc>`.
                         The sample designates 9 topics (401-409); this file is
                         the source of truth for WHICH topics the 50K-doc sample
                         supports.
  athome4.qrel.sample    standard TREC qrel `<topic> 0 <docno> <rel>` (col 2 is the
                         unused iteration field). docno is a 6-digit athome4 id;
                         rel in {0,1,2} (0=nonrel, 1=rel, 2=important). Ships the
                         full 34-topic athome4 judgments — we restrict to the 9
                         sample topics, the set the document sample is built around.

stratum / sampling_weight stay null for bush: the HiCAL qrels are direct graded
assessments with no inclusion probabilities (those columns exist for the TREC 2010
Enron sample, which IS a stratified statistical sample — P5).
"""

import sys
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

from ..config import load_pipeline_config
from ..store import write_table

TOPICS_FILE = "athome4.topics.sample"
QREL_FILE = "athome4.qrel.sample"


def _read_sample_topics(path: Path) -> list[str]:
    topics = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                topics.append(line.split()[0])
    if not topics:
        raise ValueError(f"{path}: no topics found")
    return topics


def _parse_hical_qrel(path: Path, keep_topics: set[str]) -> dict[tuple[str, str], int]:
    judgments: dict[tuple[str, str], int] = {}
    with open(path) as f:
        for line in f:
            fields = line.split()
            if not fields:
                continue
            topic, _iteration, docno, rel = fields
            if topic not in keep_topics:
                continue
            key = (topic, docno)
            judgments[key] = max(judgments.get(key, 0), int(rel))
    return judgments


def parse_bush(raw_root: Path) -> pa.Table:
    bush_root = raw_root / "bush"
    topics = _read_sample_topics(bush_root / TOPICS_FILE)
    judgments = _parse_hical_qrel(bush_root / QREL_FILE, set(topics))
    if not judgments:
        raise ValueError(f"no judgments for sample topics {topics} in {QREL_FILE}")
    rows = sorted(judgments.items())
    return pa.table(
        {
            "corpus": ["bush"] * len(rows),
            "topic": [topic for (topic, _), _ in rows],
            "trec_doc_id": [doc for (_, doc), _ in rows],
            "relevance": [rel for _, rel in rows],
            "stratum": [None] * len(rows),
            "sampling_weight": [None] * len(rows),
        }
    )


def main(args) -> int:
    cfg = load_pipeline_config()
    table = parse_bush(cfg.paths["raw"])
    path = write_table(cfg.paths["store"], args.corpus, "qrels_raw", table)

    grades: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in table.to_pylist():
        grades[row["topic"]][row["relevance"]] += 1
    topics = sorted(grades)
    print(f"qrels_raw: {table.num_rows} judgments, {len(topics)} topics -> {path}")
    for topic in topics:
        g = grades[topic]
        print(f"  {topic}: rel0={g.get(0, 0)} rel1={g.get(1, 0)} rel2={g.get(2, 0)}")
    return 0
