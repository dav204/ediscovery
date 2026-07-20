"""Batch runner: build requests, enforce budget, submit in <=10K chunks, poll,
parse results into decision records.

The Anthropic client is injected behind the small `BatchClient` protocol so the
entire lifecycle is testable offline (tests/conftest.py provides the mock). The
real adapter is constructed only by the CLI when a run is actually submitted.

Failure semantics: requests that come back `errored`/`expired` simply produce
no decision record — idempotent re-runs pick them up as candidates again.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol as TypingProtocol

from ..config import BudgetConfig
from . import cost as cost_mod
from . import decisions as dec_mod
from .prompts import Protocol, render_messages


class BatchClient(TypingProtocol):
    def create(self, requests: list[dict]) -> str:
        """Submit a batch; returns batch_id."""

    def status(self, batch_id: str) -> str:
        """Returns processing status; 'ended' when results are ready."""

    def results(self, batch_id: str) -> Iterable[dict]:
        """Yields per-request results once ended."""


@dataclass
class RunConfig:
    corpus: str
    topic: str
    phase: str  # responsiveness | privilege | qc
    tier: int
    model: str
    budget_phase: str  # key into budget caps, e.g. dev_loop
    max_output_tokens: int
    max_requests_per_batch: int = 10000
    poll_interval_seconds: float = 60.0


def build_requests(candidates: list[dict], protocol: Protocol, run: RunConfig) -> list[dict]:
    requests = []
    for doc in candidates:
        custom_id = dec_mod.make_custom_id(
            doc["doc_id"], run.topic, run.phase, run.tier, protocol.version
        )
        requests.append(
            {
                "custom_id": custom_id,
                "params": {
                    "model": run.model,
                    "max_tokens": run.max_output_tokens,
                    "messages": render_messages(protocol, doc),
                },
            }
        )
    return requests


def parse_model_output(text: str) -> dict:
    """Parse the protocol-mandated JSON object {decision, confidence, rationale}.

    Tolerates markdown code fences and leading/trailing prose; raises ValueError
    if no JSON object with the required keys is found.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    obj = json.loads(text[start : end + 1])
    missing = {"decision", "confidence", "rationale"} - set(obj)
    if missing:
        raise ValueError(f"model output missing keys: {sorted(missing)}")
    return obj


def chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _doc_tokens(candidates: list[dict]) -> int:
    return sum(int(d.get("token_estimate") or 0) for d in candidates)


def _prompt_tokens(protocol: Protocol) -> int:
    return max(1, len(protocol.content) // 4)


def estimate_run(candidates: list[dict], protocol: Protocol, run: RunConfig,
                 budget: BudgetConfig) -> cost_mod.CostEstimate:
    """The run-level projection shown at dry-run time. Derives from the same
    token accounting run_batches enforces per chunk, so the number a human
    approves is the number the cap check uses."""
    return cost_mod.estimate(
        n_requests=len(candidates),
        doc_tokens=_doc_tokens(candidates),
        prompt_tokens=_prompt_tokens(protocol),
        max_output_tokens=run.max_output_tokens,
        model=run.model,
        budget=budget,
    )


def run_batches(
    candidates: list[dict],
    protocol: Protocol,
    run: RunConfig,
    client: BatchClient,
    budget: BudgetConfig,
    spend_path: Path,
    batches_dir: Path,
    sleep=time.sleep,
) -> dict:
    """Full lifecycle. Returns {"decisions": [...], "failures": [...], "batch_ids": [...],
    "stopped_early": bool}.

    Budget is re-checked before each chunk; if the cap is crossed mid-run, no
    further chunks are submitted but already-submitted batches are drained.
    """
    requests = build_requests(candidates, protocol, run)
    doc_tokens = _doc_tokens(candidates)
    prompt_tokens = _prompt_tokens(protocol)

    out = {"decisions": [], "failures": [], "batch_ids": [], "stopped_early": False}
    batches_dir.mkdir(parents=True, exist_ok=True)

    for part in chunk(requests, run.max_requests_per_batch):
        est = cost_mod.estimate(
            n_requests=len(part),
            doc_tokens=doc_tokens * len(part) // max(1, len(requests)),
            prompt_tokens=prompt_tokens,
            max_output_tokens=run.max_output_tokens,
            model=run.model,
            budget=budget,
        )
        try:
            cost_mod.check(run.budget_phase, est, budget, spend_path)
        except cost_mod.BudgetExceeded as e:
            out["stopped_early"] = True
            out["stop_reason"] = str(e)
            break

        batch_id = client.create(part)
        out["batch_ids"].append(batch_id)
        (batches_dir / f"{batch_id}.requests.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in part)
        )

        while client.status(batch_id) != "ended":
            sleep(run.poll_interval_seconds)

        input_tokens = output_tokens = 0
        result_lines = []
        for result in client.results(batch_id):
            result_lines.append(json.dumps(result))
            custom_id = result["custom_id"]
            if result["type"] != "succeeded":
                out["failures"].append({"custom_id": custom_id, "type": result["type"]})
                continue
            usage = result["usage"]
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            price = budget.prices[run.model]
            req_cost = (
                usage["input_tokens"] * price["input"]
                + usage["output_tokens"] * price["output"]
            ) / 1_000_000
            parsed_id = dec_mod.parse_custom_id(custom_id)
            try:
                verdict = parse_model_output(result["text"])
                record = dec_mod.new_decision(
                    doc_id=parsed_id["doc_id"],
                    corpus=run.corpus,
                    topic=run.topic,
                    phase=run.phase,
                    tier=run.tier,
                    model=run.model,
                    prompt_version=protocol.version,
                    prompt_hash=protocol.content_hash,
                    batch_id=batch_id,
                    decision=verdict["decision"],
                    confidence=float(verdict["confidence"]),
                    rationale=str(verdict["rationale"]),
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    cost_usd=round(req_cost, 6),
                )
                out["decisions"].append(record)
            except (ValueError, json.JSONDecodeError) as e:
                out["failures"].append(
                    {"custom_id": custom_id, "type": "unparseable", "error": str(e)}
                )
        (batches_dir / f"{batch_id}.results.jsonl").write_text(
            "".join(line + "\n" for line in result_lines)
        )
        cost_mod.record(
            spend_path,
            batch_id=batch_id,
            phase=run.budget_phase,
            corpus=run.corpus,
            topic=run.topic,
            model=run.model,
            n_requests=len(part),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            budget=budget,
        )
    return out


class AnthropicBatchClient:
    """Thin adapter over the anthropic SDK's Message Batches API.

    Imported lazily so the SDK is only required when actually submitting
    (tests and offline work never touch it).
    """

    def __init__(self):
        import anthropic

        self._client = anthropic.Anthropic()

    def create(self, requests: list[dict]) -> str:
        batch = self._client.messages.batches.create(requests=requests)
        return batch.id

    def status(self, batch_id: str) -> str:
        return self._client.messages.batches.retrieve(batch_id).processing_status

    def results(self, batch_id: str) -> Iterable[dict]:
        for entry in self._client.messages.batches.results(batch_id):
            kind = entry.result.type
            rec = {"custom_id": entry.custom_id, "type": kind}
            if kind == "succeeded":
                message = entry.result.message
                rec["text"] = "".join(
                    block.text for block in message.content if block.type == "text"
                )
                rec["usage"] = {
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                }
            yield rec
