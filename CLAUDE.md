# AI Discovery Simulation — working rules

End-to-end eDiscovery benchmark. **Primary corpus + end-to-end showcase: EDRM Enron v2**
(TREC 2010 Legal topic 201 "Prepay transactions"; carries the privilege phase). **Validation
/ 2023-comparison layer: the Jeb Bush corpus via the HiCAL public sample** (50K docs,
athome4 topics 401–409), compared to the 2023 Tredennick/Webber GPT-3.5 baseline. Both
validated against TREC relevance judgments. Full plan: see `docs/PLAN.md`.

## Phase status

- [x] P0 Scaffold + acquisition spec
- [x] P1 Bush ingest + qrels + dev set
      (qrels parse + seeded dev sets 2026-06-13; ingest 2026-07-20 — 50,000
      messages + filename idmap from `athome4_sample.tgz`, coverage artifact
      100% of the 14,428 judged docs for 401–409, re-ingest byte-identical,
      phase1 tests green)
- [ ] P2 Review engine + first dev-set metrics
      (engine core built and offline-tested ahead of schedule — prompts, cost
      caps, decision log, batch runner, tiering, metrics; corpus data ready as
      of P1; gate still needs the real dev-set run, which waits on
      ANTHROPIC_API_KEY + human-edited `protocols/bush/athome4NN.v1.md`)
- [ ] P3 Bush/HiCAL validation runs + 2023 comparison table (9 topics)
- [ ] P4 Enron ingest + deterministic preprocessing
      (ingest done 2026-07-21 — 101,860 messages from the v2 XML zips, 0 parse
      failures, filename idmap on EDRM DocIDs, byte-identical re-runs;
      preprocessing — dedup/threading/inclusive — still pending. NOTE for P5:
      qrels judge attachment parts as separate docids, fold-vs-exclude decision
      recorded in PLAN.md P5, decide before reading the coverage gate)
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
python -m pipeline qrels --corpus bush
python -m pipeline devset --corpus bush [--topic 401]   # default: all chosen_topics
python -m pipeline review --corpus bush --topic 401 --tier 1 --dev-set --dry-run
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

## Code gotchas (dated; append when a mistake slips past existing rules)

- 2026-07-20: never randomly access members of a `.tgz` via `tarfile.getmembers()`
  + `extractfile` — each seek re-decompresses from the stream start (O(n²); the
  50K-doc sample took >300s). Stream with `tarfile.open(path, "r|gz")` in archive
  order and sort afterwards (~3s).

## Environment notes

- venv at `.venv/` (created with uv); base deps installed, per-phase extras in
  `pyproject.toml` (`uv pip install -e .[ingest]` etc.).
- Remote container egress is allowlist-gated. **Enron** corpus hosts (archive.org,
  trec-legal.umiacs.umd.edu, trec.nist.gov) must be added to the environment network
  policy before `acquire --corpus enron` download mode works; `acquire --verify-only`
  accepts files manually placed in `data/raw/<corpus>/`. **Bush/HiCAL** downloads from
  `raw.githubusercontent.com` (allowlist that host in-container; local Mac sessions have
  normal egress).
- (2026-06-13) **Bush provenance rule.** Source Bush ONLY from the HiCAL public sample
  (`github.com/hical/sample-dataset` — redacted/sampled lineage, clean provenance). This
  corpus has a documented PII-leak history (Bush's 2015 self-publication exposed bystander
  SSNs/addresses/medical info before redaction). Therefore: do NOT use floating full-corpus
  copies (e.g. archive.org); do NOT screenshot, quote, or paraphrase any Bush email content
  in artifacts/UI/writeups; full TREC access is NOT pursued. **Bush topics are 401–409
  only** — 427 Slot Machines is NOT in the sample, so do not reintroduce it (401 Summer
  Olympics is the worked-example anchor). The manifest + rationale live in
  `config/corpora/bush.yaml`.
