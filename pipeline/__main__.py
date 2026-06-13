"""CLI dispatcher: python -m pipeline <command> [...].

One command per phase, all idempotent (resume by default, --force to redo).
Every invocation appends a run record to artifacts/runs.jsonl so a fresh
session can re-orient with `python -m pipeline status`.
"""

import argparse
import json
import sys
from datetime import datetime, timezone

from . import acquire as acquire_mod
from .config import load_budget_config, load_pipeline_config
from .qrels import devset as devset_mod
from .qrels import parse as qrels_mod
from .store import TABLES, table_path

NOT_IMPLEMENTED = {
    "ingest": "P1 (bush) / P4 (enron)",
    "preprocess": "P4",
    "review": "P2",
    "privilege": "P6",
    "produce": "P7",
    "validate": "P2",
    "ui": "P8",
}


def _record_run(command: str, argv: list[str], exit_code: int) -> None:
    cfg = load_pipeline_config()
    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "argv": argv,
        "exit_code": exit_code,
    }
    with open(artifacts / "runs.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")


def cmd_status(_args) -> int:
    cfg = load_pipeline_config()
    print("== store tables ==")
    for corpus in ("bush", "enron"):
        present = [t for t in TABLES if table_path(cfg.paths["store"], corpus, t).exists()]
        print(f"  {corpus}: {', '.join(present) if present else '(none)'}")
    print("== artifacts ==")
    artifacts = cfg.paths["artifacts"]
    if artifacts.exists():
        for p in sorted(artifacts.iterdir()):
            print(f"  {p.name}")
    print("== spend ==")
    return cmd_spend(_args, header=False)


def cmd_spend(_args, header: bool = True) -> int:
    cfg = load_pipeline_config()
    budget = load_budget_config()
    spend_file = cfg.paths["spend"] / "spend.jsonl"
    total = 0.0
    per_phase: dict[str, float] = {}
    if spend_file.exists():
        with open(spend_file) as f:
            for line in f:
                rec = json.loads(line)
                total += rec["cost_usd"]
                per_phase[rec["phase"]] = per_phase.get(rec["phase"], 0.0) + rec["cost_usd"]
    if header:
        print("== spend ==")
    print(f"  total: ${total:.2f} / stop at ${budget.total_stop_usd:.2f}")
    for phase, cap in budget.caps.items():
        spent = per_phase.get(phase, 0.0)
        cap_str = f"${cap:.2f}" if cap > 0 else "LOCKED"
        print(f"  {phase}: ${spent:.2f} / {cap_str}")
    return 0


def cmd_acquire(args) -> int:
    return acquire_mod.main(args)


def _stub(command: str):
    def run(_args) -> int:
        print(f"`{command}` is not implemented yet (planned: {NOT_IMPLEMENTED[command]}).",
              file=sys.stderr)
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_acquire = sub.add_parser("acquire", help="download or verify corpus files")
    p_acquire.add_argument("--corpus", required=True, choices=["bush", "enron"])
    p_acquire.add_argument("--verify-only", action="store_true")
    p_acquire.set_defaults(func=cmd_acquire)

    p_qrels = sub.add_parser("qrels", help="parse relevance judgments into qrels_raw")
    p_qrels.add_argument("--corpus", required=True, choices=["bush", "enron"])
    p_qrels.set_defaults(func=qrels_mod.main)

    p_devset = sub.add_parser("devset", help="build seeded stratified dev sets")
    p_devset.add_argument("--corpus", required=True, choices=["bush", "enron"])
    p_devset.add_argument("--topic", help="default: all chosen_topics from the corpus config")
    p_devset.set_defaults(func=devset_mod.main)

    sub.add_parser("status", help="phase/artifact dashboard").set_defaults(func=cmd_status)
    sub.add_parser("spend", help="budget burn vs caps").set_defaults(func=cmd_spend)

    for command in NOT_IMPLEMENTED:
        p = sub.add_parser(command)
        p.add_argument("--corpus", choices=["bush", "enron"])
        p.add_argument("--topic")
        p.add_argument("--tier", type=int, choices=[1, 2])
        p.add_argument("--dev-set", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true")
        p.set_defaults(func=_stub(command))

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)
    exit_code = args.func(args)
    _record_run(args.command, argv, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
