# AI Discovery Simulation — working rules

End-to-end eDiscovery benchmark: tiered Claude review on EDRM Enron v2 (TREC 2010 Legal
topic 201 "Prepay transactions") and the TREC Total Recall Jeb Bush corpus
(athome1/athome4), validated against NIST relevance judgments and compared to the 2023
Tredennick/Webber GPT-3.5 baseline. Full plan: see `docs/PLAN.md`.

## Phase status

- [x] P0 Scaffold + acquisition spec
- [ ] P1 Bush ingest + qrels + dev set
- [ ] P2 Review engine + first dev-set metrics
- [ ] P3 Bush full runs + 2023 comparison table
- [ ] P4 Enron ingest + deterministic preprocessing
- [ ] P5 Enron qrels mapping + topic 201 review
- [ ] P6 Privilege review (Enron)
- [ ] P7 Production (PDF/redaction/Bates/load files)
- [ ] P8 Review UI + narrative + publication

Update a checkbox only when that phase's gate (pytest marker + artifact checks) is green.

## Commands

```bash
source .venv/bin/activate          # or use .venv/bin/python directly
pytest                             # must be green before any batch submission
python -m pipeline status          # phase/artifact dashboard — run first in a fresh session
python -m pipeline spend           # budget burn vs caps
python -m pipeline acquire --corpus bush [--verify-only]
python -m pipeline review --corpus bush --topic <topic> --tier 1 --dev-set --dry-run
```

## Hard rules

1. Never commit anything under `data/` (gitignored; keep it that way).
2. Never quote, paste, or paraphrase bystander email content anywhere — code, fixtures,
   logs, decision rationales, commit messages, artifacts. Name only public principals.
3. `protocols/` files are HUMAN-EDITED ONLY. Claude must never create or modify review
   protocols; prompt iteration happens by Dan editing a new `*.vN.md` file.
4. All LLM submission goes through `pipeline/review/batch.py` (cost-gated). No ad-hoc
   API calls to score documents.
5. Tests green before any batch submission; `--dry-run` before any review run; check
   `python -m pipeline spend` before approving a run.
6. Single sources of truth: doc_id derivation only in `pipeline/ids.py`; parquet schemas
   only in `pipeline/store.py`; random seeds only in `config/pipeline.yaml`.
7. No network in tests, ever (mock Anthropic client lives in `tests/conftest.py`).
8. Metrics files under `artifacts/` are machine-written; do not hand-edit.

## Non-goals (v1)

No attachment-content review, no OCR, no CAL/active learning, no agentic multi-pass
review, no privilege phase for the Bush corpus, no backend for the review UI.

## Environment notes

- venv at `.venv/` (created with uv); base deps installed, per-phase extras in
  `pyproject.toml` (`uv pip install -e .[ingest]` etc.).
- Remote container egress is allowlist-gated. Corpus hosts (trec.nist.gov, archive.org,
  trec-legal.umiacs.umd.edu, uwaterloo.ca) must be added to the environment network
  policy before `acquire` download mode works; `acquire --verify-only` accepts files
  manually placed in `data/raw/<corpus>/`.
