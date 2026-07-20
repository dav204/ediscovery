"""CLI glue for `python -m pipeline review` — candidate selection, dry-run
projection, and (only when explicitly not a dry run) batch submission.

Safety posture, per CLAUDE.md hard rules: every path through here is cost-gated
by review/batch.py + review/cost.py; --dry-run never constructs an API client;
submission requires ANTHROPIC_API_KEY and a human-edited protocol file.

Candidate contract (idempotent): in-scope docs minus docs that already have a
current decision for (topic, phase, tier, prompt_version).
"""

import json
import os
import re
import sys
from pathlib import Path

from ..config import load_budget_config, load_pipeline_config
from ..store import read_table
from . import batch as batch_mod
from . import cost as cost_mod
from . import decisions as dec_mod
from . import tiering
from .prompts import load_protocol

_PROTOCOL_VERSION_RE = re.compile(r"\.v(\d+)$")

DOC_COLUMNS = [
    "doc_id", "custodian", "from_addr", "to", "cc", "date_raw", "subject",
    "attachment_names", "body_text", "token_estimate",
]


def resolve_protocol(protocols_root: Path, corpus: str, topic: str) -> Path:
    """Highest-versioned protocols/<corpus>/*<topic>.vN.md; protocols are
    human-edited only (protocols/README.md), so absence is a hard stop."""
    stem = f"athome{topic}" if corpus == "bush" else f"topic{topic}"
    candidates = sorted(
        (int(m.group(1)), p)
        for p in (protocols_root / corpus).glob(f"{stem}.v*.md")
        if (m := _PROTOCOL_VERSION_RE.search(p.stem))
    )
    if not candidates:
        raise FileNotFoundError(
            f"no protocol {stem}.vN.md under {protocols_root / corpus}; protocols are "
            "human-edited only — see protocols/README.md"
        )
    return candidates[-1][1]


def devset_trec_ids(artifacts: Path, corpus: str, topic: str) -> list[str]:
    payload = json.loads((artifacts / f"devset_{corpus}_{topic}.json").read_text())
    ids: list[str] = []
    for grade_ids in payload["doc_ids"].values():
        ids.extend(grade_ids)
    return sorted(set(ids))


def trec_to_doc_ids(store_root: Path, corpus: str, trec_ids: list[str]) -> dict[str, str]:
    idmap = read_table(store_root, corpus, "doc_id_map").to_pylist()
    mapping = {r["trec_doc_id"]: r["doc_id"] for r in idmap}
    missing = [t for t in trec_ids if t not in mapping]
    if missing:
        raise ValueError(f"{len(missing)} trec ids have no doc_id mapping, e.g. {missing[:3]}")
    return {t: mapping[t] for t in trec_ids}


def load_doc_rows(store_root: Path, corpus: str, doc_ids: set[str]) -> list[dict]:
    table = read_table(store_root, corpus, "messages").select(DOC_COLUMNS)
    rows = [r for r in table.to_pylist() if r["doc_id"] in doc_ids]
    missing = doc_ids - {r["doc_id"] for r in rows}
    if missing:
        raise ValueError(f"{len(missing)} doc_ids not in messages table")
    return sorted(rows, key=lambda r: r["doc_id"])


def tier1_scope_doc_ids(cfg, corpus: str, topic: str, dev_set: bool) -> set[str]:
    if dev_set:
        trec_ids = devset_trec_ids(cfg.paths["artifacts"], corpus, topic)
        return set(trec_to_doc_ids(cfg.paths["store"], corpus, trec_ids).values())
    table = read_table(cfg.paths["store"], corpus, "messages").select(["doc_id"])
    return set(table.column("doc_id").to_pylist())


def tier2_scope_doc_ids(decisions_df, cfg, topic: str, phase: str, prompt_version: str) -> set[str]:
    if phase == "qc":
        return tiering.qc_sample_doc_ids(
            decisions_df, topic=topic, prompt_version=prompt_version,
            fraction=cfg.sampling["qc_fraction"], seed=cfg.seeds["qc_sample"],
        )
    return tiering.borderline_doc_ids(decisions_df, topic=topic, prompt_version=prompt_version)


def tier1_decision_ids(decisions_df, *, topic: str, prompt_version: str) -> dict[str, str]:
    """doc_id -> current tier-1 decision_id, for tier-2 supersedes wiring."""
    cur = dec_mod.current(decisions_df)
    if cur.empty:
        return {}
    mask = (
        (cur["topic"].astype(str) == str(topic))
        & (cur["tier"] == 1)
        & (cur["prompt_version"] == prompt_version)
    )
    return dict(zip(cur[mask]["doc_id"], cur[mask]["decision_id"]))


def main(args) -> int:
    if args.corpus != "bush":
        print("review: only --corpus bush is implemented (enron review is P5)", file=sys.stderr)
        return 2

    cfg = load_pipeline_config()
    budget = load_budget_config()
    tier, phase = args.tier, args.phase
    if tier == 1 and phase == "qc":
        print("review: qc runs are tier 2 (senior model) by design", file=sys.stderr)
        return 2

    from ..config import REPO_ROOT
    try:
        protocol_path = resolve_protocol(REPO_ROOT / "protocols", args.corpus, args.topic)
    except FileNotFoundError as e:
        print(f"review: {e}", file=sys.stderr)
        return 2
    protocol = load_protocol(protocol_path)

    decisions_df = dec_mod.load(cfg.paths["decisions"], args.corpus)
    if tier == 1:
        scope = tier1_scope_doc_ids(cfg, args.corpus, args.topic, args.dev_set)
        # Any-tier exclusion: a tier-1 decision superseded by tier 2 must not
        # make the doc a candidate again.
        scored = dec_mod.current_doc_ids(
            decisions_df, topic=args.topic, phase=phase, prompt_version=protocol.version
        )
    else:
        scope = tier2_scope_doc_ids(decisions_df, cfg, args.topic, phase, protocol.version)
        if args.dev_set:
            scope &= tier1_scope_doc_ids(cfg, args.corpus, args.topic, dev_set=True)
        scored = dec_mod.scored_doc_ids(
            decisions_df, topic=args.topic, phase=phase, tier=tier,
            prompt_version=protocol.version,
        )
    candidate_ids = scope - scored
    if not candidate_ids:
        print(f"review: 0 candidates (scope {len(scope)}, already scored {len(scored)}) — "
              "nothing to submit")
        return 0
    candidates = load_doc_rows(cfg.paths["store"], args.corpus, candidate_ids)

    model = cfg.models[f"tier{tier}"]
    budget_phase = "dev_loop" if args.dev_set else "bush_sample"
    run = batch_mod.RunConfig(
        corpus=args.corpus,
        topic=args.topic,
        phase=phase,
        tier=tier,
        model=model,
        budget_phase=budget_phase,
        max_output_tokens=cfg.batch["max_output_tokens"],
        max_requests_per_batch=cfg.batch["max_requests_per_batch"],
        poll_interval_seconds=cfg.batch["poll_interval_seconds"],
    )
    est = batch_mod.estimate_run(candidates, protocol, run, budget)
    total_spent, per_phase = cost_mod.total_spent(cfg.paths["spend"] / "spend.jsonl")
    cap = budget.caps[budget_phase]
    print(f"review {args.corpus}/{args.topic} phase={phase} tier={tier} model={model} "
          f"protocol={protocol.version} ({protocol.content_hash})")
    print(f"  candidates: {len(candidates)} (scope {len(scope)}, already scored {len(scored)})")
    print(f"  est input tokens: {est.input_tokens:,}  max output: {est.output_tokens:,}")
    print(f"  projected cost: ${est.usd:.2f}  budget {budget_phase}: "
          f"${per_phase.get(budget_phase, 0.0):.2f} spent / "
          f"{'LOCKED' if cap <= 0 else f'${cap:.2f} cap'}  (total ${total_spent:.2f})")

    if args.dry_run:
        print("  dry run: nothing submitted")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("review: ANTHROPIC_API_KEY not set; put it in ~/.config/ediscovery.env "
              "and source it before submitting", file=sys.stderr)
        return 2

    result = batch_mod.run_batches(
        candidates=candidates,
        protocol=protocol,
        run=run,
        client=batch_mod.AnthropicBatchClient(),
        budget=budget,
        spend_path=cfg.paths["spend"] / "spend.jsonl",
        batches_dir=cfg.paths["batches"],
    )
    if tier == 2 and phase == "responsiveness":
        supersedes = tier1_decision_ids(
            decisions_df, topic=args.topic, prompt_version=protocol.version
        )
        for rec in result["decisions"]:
            rec["supersedes"] = supersedes.get(rec["doc_id"])
    dec_mod.append(cfg.paths["decisions"], args.corpus, result["decisions"])

    print(f"  decisions: {len(result['decisions'])}  failures: {len(result['failures'])}  "
          f"batches: {len(result['batch_ids'])}")
    if result["stopped_early"]:
        print(f"  STOPPED EARLY: {result.get('stop_reason')}", file=sys.stderr)
        return 1
    if result["failures"]:
        print("  failed requests will be retried as candidates on the next run",
              file=sys.stderr)
    return 0
