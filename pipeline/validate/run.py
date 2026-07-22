"""CLI glue for `python -m pipeline validate` — score current decisions
against qrels and write the metrics artifact.

Bush/HiCAL judgments are direct graded assessments -> unweighted estimator.
The Enron path (P5) will pass weighted=True with the TREC 2010 strata weights.
Only responsiveness-phase decisions enter the production set; qc decisions are
an audit trail, not a scoring input.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ..config import REPO_ROOT, load_pipeline_config
from ..review import decisions as dec_mod
from ..review.prompts import load_protocol
from ..review.run import devset_trec_ids, resolve_protocol
from ..store import read_table
from .metrics import evaluate


def mapped_qrels(store_root: Path, corpus: str) -> pd.DataFrame:
    """qrels_raw joined to pipeline doc_ids; unmapped judged docs are dropped
    here but reported by the caller (coverage lives in the idmap artifact).
    Ambiguous idmap rows are excluded and a fan-out join is a hard error —
    a doubled row doubles that doc's sampling_weight in the matrix."""
    qrels = read_table(store_root, corpus, "qrels_raw").to_pandas()
    idmap = read_table(store_root, corpus, "doc_id_map").to_pandas()
    idmap = idmap[~idmap["ambiguous"]]
    merged = qrels.merge(
        idmap[["trec_doc_id", "doc_id"]], on="trec_doc_id", how="left",
        validate="m:1",
    )
    merged["sampling_weight"] = merged["sampling_weight"].fillna(1.0)
    return merged


def current_responsiveness(decisions_df: pd.DataFrame, topic: str,
                           prompt_version: str) -> pd.DataFrame:
    """Current responsiveness decisions for ONE protocol version. The engine
    keeps old-version decisions current on purpose (decisions.current), so
    scoring without a version filter would join each doc once per version and
    double-count it in the confusion matrix."""
    if decisions_df.empty:
        return pd.DataFrame(columns=["doc_id", "decision"])
    cur = dec_mod.current(decisions_df)
    mask = (
        (cur["topic"].astype(str) == str(topic))
        & (cur["phase"] == "responsiveness")
        & (cur["prompt_version"] == prompt_version)
    )
    return cur[mask][["doc_id", "decision"]]


def main(args) -> int:
    if args.corpus != "bush":
        print("validate: only --corpus bush is implemented (enron is P5)", file=sys.stderr)
        return 2
    cfg = load_pipeline_config()
    try:
        protocol = load_protocol(
            resolve_protocol(REPO_ROOT / "protocols", args.corpus, args.topic)
        )
    except FileNotFoundError as e:
        print(f"validate: {e}", file=sys.stderr)
        return 2
    decisions_df = dec_mod.load(cfg.paths["decisions"], args.corpus)
    decisions = current_responsiveness(decisions_df, args.topic, protocol.version)
    if decisions.empty:
        print(f"validate: no responsiveness decisions for topic {args.topic} under "
              f"protocol {protocol.version}; run review first", file=sys.stderr)
        return 2

    qrels = mapped_qrels(cfg.paths["store"], args.corpus)
    qrels = qrels[qrels["topic"].astype(str) == str(args.topic)]
    scope = "full"
    if args.dev_set:
        dev_ids = set(devset_trec_ids(cfg.paths["artifacts"], args.corpus, args.topic))
        qrels = qrels[qrels["trec_doc_id"].isin(dev_ids)]
        scope = "dev"
    unmapped = int(qrels["doc_id"].isna().sum())
    qrels = qrels[qrels["doc_id"].notna()]

    result = evaluate(decisions, qrels, topic=args.topic, weighted=False)
    payload = asdict(result)
    payload["scope"] = scope
    payload["prompt_version"] = protocol.version
    payload["unmapped_judged_docs"] = unmapped

    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    suffix = "_dev" if args.dev_set else ""
    out = artifacts / f"metrics_{args.corpus}_{args.topic}{suffix}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"validate {args.corpus}/{args.topic} [{scope}]: "
          f"recall={result.recall:.4f} precision={result.precision:.4f} f1={result.f1:.4f} "
          f"(evaluated {result.n_evaluated}/{result.n_judged} judged) -> {out}")
    return 0
