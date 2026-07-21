"""CLI glue for `python -m pipeline preprocess` — dedup_exact, dedup_near,
threads (with inclusive flags) over the Enron messages table, plus the
committed summary artifact. Deterministic: pure functions of the messages
table + config seeds/params; re-runs are byte-identical.
"""

import json
import sys
from collections import Counter

import pyarrow.parquet as pq

from ..config import load_pipeline_config
from ..store import read_table, table_path, write_table
from .dedup import exact_clusters, near_clusters
from .threads import build_threads, threads_table

COLUMNS = ["doc_id", "subject_norm", "body_norm", "date_utc", "attachment_names"]

REQUIRED_PARAMS = (
    "minhash_num_perm", "near_dup_threshold", "shingle_words",
    "near_dup_min_body_words", "boilerplate_body_min_repeats",
    "thread_max_gap_days",
)


def main(args) -> int:
    if args.corpus != "enron":
        print("preprocess: only --corpus enron is implemented (bush is whole-doc "
              "validation, no threading/dedup by design)", file=sys.stderr)
        return 2
    cfg = load_pipeline_config()
    missing = [k for k in REQUIRED_PARAMS if k not in cfg.preprocess]
    if missing:
        print(f"preprocess: config/pipeline.yaml preprocess block missing {missing}",
              file=sys.stderr)
        return 2
    store_root = cfg.paths["store"]
    artifacts = cfg.paths["artifacts"]
    report_path = artifacts / "enron_preprocess_report.json"

    if not table_path(store_root, "enron", "messages").exists():
        print("preprocess: messages table missing; run "
              "`python -m pipeline ingest --corpus enron` first", file=sys.stderr)
        return 2
    n_messages = pq.ParquetFile(
        table_path(store_root, "enron", "messages")
    ).metadata.num_rows

    params = {"seed": cfg.seeds["minhash"], **cfg.preprocess}
    tables = ["dedup_exact", "dedup_near", "threads"]
    if (all(table_path(store_root, "enron", t).exists() for t in tables)
            and report_path.exists() and not args.force):
        report = json.loads(report_path.read_text())
        # Resume only when the persisted run matches the current config AND
        # the current messages table — otherwise the outputs are stale.
        if report.get("params") == params and report.get("messages") == n_messages:
            print(f"preprocess: already done ({report['canonical_docs']} canonical "
                  f"docs, {report['threads']} threads); --force to redo")
            return 0
        print("preprocess: existing outputs don't match current config/messages; "
              "recomputing")

    # A crash between here and the report write must not leave a stale report
    # pairing with new tables — resume treats missing-report as not-done.
    report_path.unlink(missing_ok=True)

    rows = read_table(store_root, "enron", "messages").select(COLUMNS).to_pylist()

    exact = exact_clusters(rows)
    write_table(store_root, "enron", "dedup_exact", exact)
    canonical_ids = set(
        exact.column("canonical_doc_id").to_pylist()
    )
    canonical_rows = [r for r in rows if r["doc_id"] in canonical_ids]

    p = cfg.preprocess
    body_freq = Counter(r["body_norm"] for r in rows)
    near = near_clusters(
        canonical_rows,
        seed=cfg.seeds["minhash"],
        num_perm=p["minhash_num_perm"],
        threshold=p["near_dup_threshold"],
        shingle_words=p["shingle_words"],
        min_body_words=p["near_dup_min_body_words"],
        boilerplate_min_repeats=p["boilerplate_body_min_repeats"],
        body_freq=body_freq,
    )
    write_table(store_root, "enron", "dedup_near", near)

    thread_rows = build_threads(canonical_rows, max_gap_days=p["thread_max_gap_days"])
    threads = threads_table(thread_rows)
    write_table(store_root, "enron", "threads", threads)

    inclusive = Counter(
        t["inclusive_reason"] for t in thread_rows if t["is_inclusive"]
    )
    boilerplate_bodies = sum(
        1 for body, n in body_freq.items()
        if n >= p["boilerplate_body_min_repeats"]
    )
    report = {
        "corpus": "enron",
        "messages": len(rows),
        "canonical_docs": len(canonical_ids),
        "exact_dup_docs": len(rows) - len(canonical_ids),
        "boilerplate_bodies": boilerplate_bodies,
        "near_dup_docs": near.num_rows,
        "near_clusters": len(set(near.column("cluster_id").to_pylist())),
        "threads": len(set(threads.column("thread_id").to_pylist())),
        "singleton_threads": sum(1 for t in thread_rows if t["method"] == "singleton"),
        "inclusive": dict(sorted(inclusive.items())),
        "inclusive_docs": sum(1 for t in thread_rows if t["is_inclusive"]),
        "params": params,
    }
    artifacts.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"preprocess: {report['canonical_docs']} canonical of {report['messages']} docs "
          f"({report['exact_dup_docs']} exact dups), {report['near_dup_docs']} near-dup docs "
          f"in {report['near_clusters']} clusters")
    print(f"  threads: {report['threads']} ({report['singleton_threads']} singletons), "
          f"inclusive {report['inclusive_docs']}: {report['inclusive']} -> {report_path}")
    return 0
