# End-to-End AI Discovery Simulation

An openly documented eDiscovery benchmark: a complete review workflow — dedup, email
threading, multi-custodian tagging, tiered LLM responsiveness review, privilege review,
redaction, Bates-stamped production, privilege log, and case narrative — run on two
classic public corpora with published human relevance judgments, reporting real
recall/precision numbers against them.

**Status: Phase 0 (scaffold).** No corpus data or metrics yet. See `docs/PLAN.md` for
the full implementation plan and phase gates, and `CLAUDE.md` for working rules.

## Corpora and ground truth

| Corpus | Documents | Ground truth | Role |
|---|---|---|---|
| EDRM Enron v2 (PII-cleaned) | 3 custodians: Skilling, Lay, Kaminski | TREC 2010 Legal Track Learning task, topic 201 "Prepay transactions" (sampled judgments) | Privilege dimension + the corpus the industry knows |
| Jeb Bush emails (TREC Total Recall athome1/athome4) | 290,099 emails | NIST/CAL relevance judgments, near-complete | Headline comparison vs. the 2023 Tredennick/Webber GPT-3.5 study |

Provenance, licenses, and download terms are documented per-corpus in
`config/corpora/*.yaml` and verified by `python -m pipeline acquire`.

## Quick start

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e .[dev]
.venv/bin/python -m pytest
.venv/bin/python -m pipeline status
```

## Ethics

This is a simulation/benchmark, not legal advice; no output here would be defensible in
a real matter without attorney supervision. The Enron corpus contains personal email of
bystanders never accused of anything: this project uses only the EDRM v2 PII-cleaned
edition, never quotes bystander content, and names only public principals.
