"""Decision log: append-only JSONL, the first-class benchmark artifact.

custom_id format (also used as the Batch API request id):
    {doc_id}|{topic}|{phase_code}|t{tier}|{prompt_version}
phase codes are kept short because the Batch API caps custom_id at 64 chars:
    resp = responsiveness, priv = privilege, qc = qc
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PHASE_CODES = {"responsiveness": "resp", "privilege": "priv", "qc": "qc"}
CODE_PHASES = {v: k for k, v in PHASE_CODES.items()}

RESPONSIVENESS_DECISIONS = ("responsive", "not_responsive", "borderline")
PRIVILEGE_DECISIONS = ("privileged", "not_privileged", "partial")


def make_custom_id(doc_id: str, topic: str, phase: str, tier: int, prompt_version: str) -> str:
    code = PHASE_CODES[phase]
    custom_id = f"{doc_id}|{topic}|{code}|t{tier}|{prompt_version}"
    if len(custom_id) > 64:
        raise ValueError(f"custom_id exceeds Batch API 64-char limit: {custom_id!r}")
    return custom_id


def parse_custom_id(custom_id: str) -> dict:
    doc_id, topic, code, tier, prompt_version = custom_id.split("|")
    return {
        "doc_id": doc_id,
        "topic": topic,
        "phase": CODE_PHASES[code],
        "tier": int(tier.removeprefix("t")),
        "prompt_version": prompt_version,
    }


def new_decision(*, doc_id: str, corpus: str, topic: str, phase: str, tier: int,
                 model: str, prompt_version: str, prompt_hash: str, batch_id: str,
                 decision: str, confidence: float, rationale: str,
                 input_tokens: int, output_tokens: int, cost_usd: float,
                 supersedes: str | None = None) -> dict:
    valid = RESPONSIVENESS_DECISIONS if phase in ("responsiveness", "qc") else PRIVILEGE_DECISIONS
    if decision not in valid:
        raise ValueError(f"decision {decision!r} invalid for phase {phase!r}; expected {valid}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence {confidence} out of [0, 1]")
    return {
        "decision_id": str(uuid.uuid4()),
        "doc_id": doc_id,
        "corpus": corpus,
        "topic": topic,
        "phase": phase,
        "tier": tier,
        "model": model,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "batch_id": batch_id,
        "custom_id": make_custom_id(doc_id, topic, phase, tier, prompt_version),
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "supersedes": supersedes,
    }


def log_path(decisions_dir: Path, corpus: str) -> Path:
    return Path(decisions_dir) / f"{corpus}_decisions.jsonl"


def append(decisions_dir: Path, corpus: str, records: list[dict]) -> Path:
    path = log_path(decisions_dir, corpus)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def load(decisions_dir: Path, corpus: str) -> pd.DataFrame:
    path = log_path(decisions_dir, corpus)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_json(path, lines=True, dtype={"supersedes": "string"})


def current(df: pd.DataFrame) -> pd.DataFrame:
    """Drop records that any later record supersedes.

    Tier-2 records supersede tier-1 via the `supersedes` field; protocol
    version changes don't supersede (candidate selection keys on
    prompt_version instead, so old-version decisions stay for the record).
    """
    if df.empty:
        return df
    superseded = set(df["supersedes"].dropna())
    return df[~df["decision_id"].isin(superseded)]


def scored_doc_ids(df: pd.DataFrame, *, topic: str, phase: str, tier: int,
                   prompt_version: str) -> set[str]:
    """Docs that already have a current decision for this exact run config —
    the idempotency contract: these are excluded from the next candidate set."""
    if df.empty:
        return set()
    cur = current(df)
    mask = (
        (cur["topic"].astype(str) == str(topic))
        & (cur["phase"] == phase)
        & (cur["tier"] == tier)
        & (cur["prompt_version"] == prompt_version)
    )
    return set(cur[mask]["doc_id"])


def current_doc_ids(df: pd.DataFrame, *, topic: str, phase: str,
                    prompt_version: str) -> set[str]:
    """Docs with a current decision at ANY tier. Tier-1 candidate selection
    must use this, not scored_doc_ids: a tier-1 decision superseded by tier-2
    is no longer current, and re-reviewing that doc at tier 1 would both pay
    again and mint a second co-current decision."""
    if df.empty:
        return set()
    cur = current(df)
    mask = (
        (cur["topic"].astype(str) == str(topic))
        & (cur["phase"] == phase)
        & (cur["prompt_version"] == prompt_version)
    )
    return set(cur[mask]["doc_id"])
