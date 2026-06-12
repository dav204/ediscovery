"""Token/cost estimation, budget cap enforcement, and the spend ledger.

Every batch submission must pass `check()` first; actual usage is recorded
with `record()` from batch results. The ledger (data/spend/spend.jsonl) is
append-only and is the source of truth for `python -m pipeline spend`.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import BudgetConfig


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class CostEstimate:
    n_requests: int
    input_tokens: int
    output_tokens: int
    usd: float


def estimate(n_requests: int, doc_tokens: int, prompt_tokens: int,
             max_output_tokens: int, model: str, budget: BudgetConfig) -> CostEstimate:
    """Worst-case estimate: every request pays full prompt + doc + max output."""
    if model not in budget.prices:
        raise KeyError(f"no price configured for model {model!r}")
    price = budget.prices[model]
    input_tokens = doc_tokens + n_requests * prompt_tokens
    output_tokens = n_requests * max_output_tokens
    usd = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    return CostEstimate(n_requests, input_tokens, output_tokens, round(usd, 4))


def total_spent(spend_path: Path) -> tuple[float, dict[str, float]]:
    total = 0.0
    per_phase: dict[str, float] = {}
    if spend_path.exists():
        with open(spend_path) as f:
            for line in f:
                rec = json.loads(line)
                total += rec["cost_usd"]
                per_phase[rec["phase"]] = per_phase.get(rec["phase"], 0.0) + rec["cost_usd"]
    return total, per_phase


def check(budget_phase: str, est: CostEstimate, budget: BudgetConfig, spend_path: Path) -> None:
    """Raise BudgetExceeded if the projected spend breaks the phase cap or total stop."""
    if budget_phase not in budget.caps:
        raise KeyError(f"unknown budget phase {budget_phase!r}; configured: {sorted(budget.caps)}")
    total, per_phase = total_spent(spend_path)
    cap = budget.caps[budget_phase]
    phase_spent = per_phase.get(budget_phase, 0.0)
    if cap <= 0:
        raise BudgetExceeded(
            f"budget phase {budget_phase!r} is locked (cap 0); raise it in config/budget.yaml"
        )
    if phase_spent + est.usd > cap:
        raise BudgetExceeded(
            f"{budget_phase}: spent ${phase_spent:.2f} + projected ${est.usd:.2f} "
            f"exceeds cap ${cap:.2f}"
        )
    if total + est.usd > budget.total_stop_usd:
        raise BudgetExceeded(
            f"total: spent ${total:.2f} + projected ${est.usd:.2f} "
            f"exceeds total stop ${budget.total_stop_usd:.2f}"
        )


def record(spend_path: Path, *, batch_id: str, phase: str, corpus: str, topic: str,
           model: str, n_requests: int, input_tokens: int, output_tokens: int,
           budget: BudgetConfig) -> dict:
    price = budget.prices[model]
    cost_usd = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
    prior_total, _ = total_spent(spend_path)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch_id,
        "phase": phase,
        "corpus": corpus,
        "topic": topic,
        "model": model,
        "n_requests": n_requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
        "cumulative_usd": round(prior_total + cost_usd, 6),
    }
    spend_path.parent.mkdir(parents=True, exist_ok=True)
    with open(spend_path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec
