# End-to-End AI Discovery Simulation

An openly documented eDiscovery benchmark: a complete review workflow — dedup, email
threading, multi-custodian tagging, tiered LLM responsiveness review, privilege review,
redaction, Bates-stamped production, privilege log, and case narrative — run end-to-end on
**EDRM Enron v2** (the primary corpus, which also carries the privilege phase), with the
**Jeb Bush corpus via the HiCAL public sample** as a validation / 2023-comparison layer.
Both have published human relevance judgments, so the project reports real recall/precision
numbers against them.

**Status: Phase 0 (scaffold).** No corpus data or metrics yet. See `docs/PLAN.md` for
the full implementation plan and phase gates, and `CLAUDE.md` for working rules.

## Corpora and ground truth

| Corpus | Documents | Ground truth | Role |
|---|---|---|---|
| EDRM Enron v2 (PII-cleaned) | 3 custodians: Skilling, Lay, Kaminski | TREC 2010 Legal Track Learning task, topic 201 "Prepay transactions" (sampled judgments) | **Primary** — end-to-end showcase + privilege dimension; the corpus the industry knows |
| Jeb Bush emails (HiCAL public sample, athome4 topics 401–409) | 50,000 sampled docs | TREC 2016 Total Recall graded judgments | Validation + 2023-comparison vs. the Tredennick/Webber GPT-3.5 study, on the overlapping sample topics |

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

The Bush corpus has a documented PII-leak history — the 2015 self-publication exposed
ordinary citizens' SSNs, addresses, and medical information before it was pulled and
replaced with a redacted version. Because this project is aimed at privacy-literate
practitioners, provenance is handled visibly: we use only the **HiCAL public sample** (the
redacted/sampled lineage, github.com/hical/sample-dataset), never the unvetted full-corpus
copies floating elsewhere, and never screenshot or quote Bush email content.
