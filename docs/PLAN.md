# Implementation Plan: End-to-End AI Discovery Simulation

## Context

Dan's brief: run a complete, openly documented eDiscovery workflow (dedup, threading, custodian tagging, tiered LLM responsiveness review, privilege review, redaction, Bates-stamped production, privilege log, case narrative, review UI) on two public corpora with human ground truth, and publish recall/precision against the 2023 EDRM GPT-3.5 baseline — answering Craig Ball's "retire Enron" question with data. Repo `dav204/ediscovery` is greenfield (README only). Branch: `claude/ediscovery-ai-benchmark-lv2ikj`.

## Decisions made (planning session, 2026-06-12)

- **Bush topics:** replicate the 2023 Tredennick/Webber study topics (extract their exact topic IDs + per-topic recall/precision in Phase 0; pick 2–4 of their topics).
- **Opioid coda:** cut.
- **Acquisition:** Dan extends this environment's network allowlist; pipeline downloads + checksums. Acquisition is fully separable (`--verify-only` mode accepts manually dropped files as fallback).
- **Enron custodians:** Skilling, Lay, Kaminski to start; adjust after Phase 5 idmap coverage check.

## Research findings (confirmed via web search; article full-texts blocked by container egress policy)

- **2023 baseline:** "Will ChatGPT Replace Ediscovery Review Teams?" — John Tredennick & Dr. William Webber (Merlin Search Technologies), Law.com Feb 2023 + EDRM Mar 2023 (edrm.net/2023/03/will-chatgpt-replace-ediscovery-review-teams/). GPT-3.5-era, Jeb Bush corpus; "reluctant to classify relevant"; hallucinations on 2 topics. Follow-ups: EDRM Jul 2023 "From Search Hits to Discovery Answers", Tredennick Medium series, Jan 2026 book. **Per-topic tables = Phase 0 extraction task.**
- **Bush corpus:** TREC Total Recall — athome1 (2015, 290,099 redacted Bush emails, 10 topics athome100–109) and athome4 (2016, same emails, 34 topics). Data: trec.nist.gov/data/total-recall/, NIST PDR mds2-3126, data.commerce.gov listing; sample at github.com/hical/sample-dataset. Historically gated by a signed "TREC Total Recall Usage Agreement" → **verify current terms in Phase 0**.
- **Enron:** EDRM Enron v2 (PII-cleaned, 153 custodian PSTs, ~8.6 GB): archive.org + enrondata.readthedocs.io; UMD helpers at trec-legal.umiacs.umd.edu/corpora/trec/legal10/. Ground truth: TREC 2010 Legal Track **Learning task** used EDRM Enron v2, topics 200–207; **topic 201 = "Prepay transactions"** (207 = football). Qrels at trec.nist.gov/data/legal10.html — **sampled/stratified**, so Enron metrics need strata-aware estimators.
- **DOJ ~186 trial-exhibit mapping:** no public mapping found → optional stretch, drop unless found in Phase 0.
- **Craig Ball hook confirmed:** "Still on Dial-Up…", Aug 15 2025, craigball.net + EDRM.
- **Tooling:** libratom/pypff (+ readpst fallback) for PSTs; jwzthreading + custom subject/quoted-text fallback (Enron lacks In-Reply-To headers); **no OSS inclusive-email detector → build custom**; datasketch MinHash/LSH; WeasyPrint render → PyMuPDF true redactions (`apply_redactions`) → pikepdf/ReportLab Bates; Concordance DAT (þ/¶) + Opticon OPT. Anthropic Batch API: 50% off, **10K requests/batch max**, 24h window. Batch pricing per MTok in/out: Haiku 4.5 $0.50/$2.50, Sonnet 4.6 $1.50/$7.50, Fable 5 $5/$25.

## Prerequisite (Dan, outside the repo)

Extend the environment network allowlist with: `trec.nist.gov`, `data.nist.gov`, `archive.org` (+ `*.us.archive.org` download nodes), `trec-legal.umiacs.umd.edu`, `plg.uwaterloo.ca`, `cormack.uwaterloo.ca`, `edrm.net`, `pypi.org`/`files.pythonhosted.org` (if not already). Until then, Phases 0 (scaffold) and all synthetic-fixture work proceed; corpus-dependent gates wait.

## Repo skeleton

```
config/            pipeline.yaml, budget.yaml, corpora/{enron,bush}.yaml, counsel/enron_counsel.yaml
protocols/         HUMAN-EDITED review protocols, version in filename (enron/topic201.v1.md, bush/…, privilege/…)
pipeline/          __main__.py (argparse dispatch), config.py, store.py (all parquet schemas), ids.py,
                   acquire.py, ingest/{pst,readpst_fallback,mbox,normalize}.py,
                   preprocess/{dedup,threads,inclusive,custodians}.py,
                   qrels/{parse,idmap,devset}.py,
                   review/{prompts,batch,tiering,decisions,cost}.py,
                   privilege/{screen,review}.py,
                   produce/{render,redact,bates,loadfiles}.py,
                   validate/{metrics,elusion}.py, ui/{build.py,template.html}, narrative.py
tests/             unit/, golden/, fixtures/ (synthetic mbox, fake qrels, mock batch responses), conftest.py (mock Anthropic client; NO network in tests)
data/              gitignored: raw/, store/, batches/, decisions/, spend/, productions/
artifacts/         committed: metrics.json, idmap coverage reports, dev-set ID lists, runs.jsonl (no doc content)
CLAUDE.md          phase checklist (cross-session memory), commands, hard rules (below)
```

CLAUDE.md hard rules: never commit `data/`; never quote bystander email content anywhere (code, fixtures, logs, rationales, commits); `protocols/` are human-edited only; all LLM submission via cost-gated `review/batch.py`; tests green before any batch submission; `--dry-run` before any review run; doc_id derivation only in `ids.py`; schemas only in `store.py`; seeds in config. Non-goals v1: no attachment-content review, no OCR, no CAL, no agentic multi-pass, no Bush privilege phase, no UI backend.

## Data model (Parquet via duckdb, partitioned by corpus)

- **messages**: doc_id, corpus, custodian, source_file, source_index, message_id_hdr?, in_reply_to?, references[], from_addr/from_name, to/cc/bcc[], subject, subject_norm, date_utc?, date_raw, body_text, body_norm, body_hash, has_attachments, attachment_count, attachment_names[], headers_json, parse_warnings[], token_estimate.
- **doc_id rule** (`ids.py`): `{corpus}-{sha256(source_file+':'+source_index)[:16]}` — stable across re-ingest; TREC IDs live only in the mapping table.
- **dedup_exact / dedup_near**: body_hash / MinHash cluster_id → canonical_doc_id (deterministic: earliest date, lowest doc_id).
- **threads**: doc_id, thread_id, parent_doc_id?, depth, method (jwz|subject_fallback|containment|singleton), is_inclusive, inclusive_reason.
- **qrels_raw**: corpus, topic, trec_doc_id, relevance, stratum?, sampling_weight? (TREC 2010 = sampled; athome ≈ complete).
- **doc_id_map** (risk-management table): doc_id ↔ trec_doc_id, match_method (filename|message_id|hash|tuple_fuzzy), confidence, ambiguous. Matcher cascade in `qrels/idmap.py`; every run emits `artifacts/{corpus}_idmap_coverage.json`.
- **decisions** (append-only JSONL → parquet; the benchmark artifact): decision_id, doc_id, corpus, topic, phase (responsiveness|privilege|qc), tier, model, prompt_version (+content hash), batch_id, custom_id = `{doc_id}|{topic}|{phase}|t{tier}|{prompt_version}`, decision, confidence, rationale (≤2 sentences, no verbatim quotes), input/output tokens, cost_usd, created_at, supersedes?.
- **overrides** (UI export) → human-delta metric. **spend.jsonl**: per-batch usage + cumulative_usd.

## CLI (all idempotent; resume default, `--force` to redo; every run appends to artifacts/runs.jsonl)

```
python -m pipeline acquire|ingest|preprocess|qrels|devset|review|privilege|produce|validate|ui|spend|status
e.g. review --corpus bush --topic athome102 --tier 1 [--dev-set] [--dry-run]
```
Review idempotency contract: candidates = in-scope docs minus docs with a current decision for (topic, phase, tier, prompt_version). `--dry-run` prints candidate count, token estimate, projected cost, remaining budget.

## Cost guardrails

`config/budget.yaml`: per-model batch prices (updatable), per-phase USD caps (dev_loop $25 default, others set with Dan before full runs), `total_stop_usd`. `review/cost.py`: estimate → cap check (raises BudgetExceeded pre-submission) → record actuals from batch results. Mid-run: stop submitting chunks when cap crossed, drain submitted batches, write stop-report.

## Phases & gates (ordered: Bush dev-set loop first = cheapest path to first recall number)

- **P0 Scaffold + acquisition spec** — skeleton, pyproject, configs, ids/store, CLI stubs, CLAUDE.md, test harness, corpus manifests (URL+sha256), `acquire --verify-only` mode. External: extract 2023 study topics/tables; verify Total Recall usage terms. *Gate:* pytest green; `status` runs; manifests committed.
- **P1 Bush ingest + qrels + dev set** — mbox parser → messages parquet; qrels parse; filename-join idmap; seeded stratified dev set (~300–500 judged docs/topic). *Gate:* count ≈ 290,099; idmap ≥ 99% on chosen topics; dev-set IDs committed; re-run byte-identical.
- **P2 Review engine + first metrics** — protocol v1 (human-edited), batch runner (build → dry-run → ≤10K chunks → poll → parse), cost caps, decision log, tiering, metrics. Tier 1 Haiku on dev set → recall/precision/F1; iterate protocol (version bumps); tier-2 borderline routing. *Gate:* dev run under cap; metrics artifact + hand-computed-confusion-matrix test green; re-run submits 0 requests; mock-client batch tests green.
- **P3 Bush full runs** — all chosen topics: tier 1 → tier 2 borderline + stratified QC sample → elusion sample. *Gate:* zero decision gaps; spend reconciles with console; **2023-vs-2026 comparison table in artifacts/** (publishable milestone).
- **P4 Enron ingest + preprocessing** — PST parse (per-PST subprocess isolation), dedup, threading, inclusive detection, custodian tagging. *Gate:* threading/dedup/inclusive unit+golden tests green; parse failure rate <1%, itemized; fallback path tested.
- **P5 Enron qrels + topic 201 review** — idmap cascade vs TREC 2010 qrels (coverage gate ≥ ~90% of judged docs for selected custodians **before any token spend**; else swap custodians — idmap per-custodian is cheap); strata-aware metrics; dev set → full run, reusing P2 engine unchanged.
- **P6 Privilege (Enron)** — deterministic counsel screen (in-house: Derrick, Haedicke, Mintz, Mordaunt, Rogers…; V&E: Dilg, Hendrick, Astin… — verify list against Powers Report in-phase) → Fable 5 privilege pass → EDRM-style privilege log. *Gate:* same decision schema; log golden test; name/domain matcher unit tests.
- **P7 Production** — render → redact → Bates (`ENRON-{seq:08d}`, persisted allocator) → DAT/OPT. *Gate:* Bates continuity; **redaction verification: extract text from every redacted PDF, assert redacted strings absent**; DAT/OPT golden-byte tests.
- **P8 UI + narrative + publication** — static HTML per topic (embedded JSON, approve/override, export → human-delta), case narrative from responsive set only, final README, writeup support. *Gate:* file:// open with full topic data; override roundtrip test; ethics grep pass over all committed artifacts.

## Testing strategy

Unit (synthetic fixtures, zero network): threading edge cases, inclusive truth tables, dedup determinism, ids stability, both qrels formats, idmap ambiguity, Bates allocator, tiering, cost caps. Golden: DAT/OPT bytes, privilege log CSV, redacted-PDF extracted text, rendered prompt (catches prompt drift). Metrics vs hand-computed fixtures (incl. weighted sampled-qrels estimator). Mock Anthropic batch lifecycle incl. errored/expired requests → retry-via-idempotency. 30-message integration smoke through full pipeline with mock LLM. Phase gates = pytest markers + artifact checks; CLAUDE.md checklist updated only when green.

## Top risks

1. **Enron qrels↔PST ID mapping** — tiered matcher + hard coverage gate before token spend; honest coverage reporting; custodian swap fallback.
2. **Acquisition terms/access** (Total Recall usage agreement; allowlist) — separable acquire step; verify terms in P0; everything downstream keys off checksummed `data/raw/`.
3. **2023 topic extraction blocked** — pipeline is topic-parameterized; choice lands as config edit any time before P3.
4. **Budget** — caps + dry-run discipline + Haiku-first tiering; if dev-set borderline rate >25%, fix protocol before scaling.
5. **PST parse / pypff instability** — readpst fallback, per-PST isolation, <1% failure gate.

## Verification (end-to-end)

`pytest` green at every gate; integration smoke runs the full pipeline on synthetic fixtures in CI-seconds; first real validation = P2 dev-set metrics vs athome qrels; `python -m pipeline status` re-orients any fresh session. Commit + push to `claude/ediscovery-ai-benchmark-lv2ikj` at each gate.
