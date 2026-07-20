# Implementation Plan: End-to-End AI Discovery Simulation

## Context

Dan's brief: run a complete, openly documented eDiscovery workflow (dedup, threading, custodian tagging, tiered LLM responsiveness review, privilege review, redaction, Bates-stamped production, privilege log, case narrative, review UI) with human ground truth, and publish recall/precision against the 2023 EDRM GPT-3.5 baseline — answering Craig Ball's "retire Enron" question with data. **EDRM Enron v2 is the primary corpus and end-to-end showcase** (it carries the privilege dimension); **the Jeb Bush corpus, via the HiCAL public sample, is a validation / 2023-comparison layer**, not a co-equal leg. Repo `dav204/ediscovery` is greenfield (README only). Branch: `claude/ediscovery-ai-benchmark-lv2ikj`.

## Decisions made (planning sessions, 2026-06-12 / 2026-06-13)

- **Bush = HiCAL sample, validation only (2026-06-13 sourcing correction):** the full 290K
  TREC corpus is access-gated and carries a PII-leak history (see Acquisition findings); we
  use the HiCAL public sample (50K docs, redacted/sampled lineage, clean provenance) and
  Bush drops to a validation/comparison role. Enron is the sole primary corpus.
- **Bush topics:** all 9 HiCAL sample topics (athome4 401–409); 401 Summer Olympics is the
  2023-study worked-example anchor. (Supersedes the earlier "pick 2–4 of 34" plan — moot
  now that the sample bounds us to 9.)
- **Opioid coda:** cut.
- **Acquisition:** Bush downloads + checksums from the HiCAL GitHub raw endpoint (no
  allowlist or agreement needed). Enron acquisition still needs the allowlist extension.
  Acquisition is fully separable (`--verify-only` accepts manually dropped files as fallback).
- **Enron custodians:** Skilling, Lay, Kaminski to start; adjust after Phase 5 idmap coverage check.

## Research findings (confirmed via web search; article full-texts blocked by container egress policy)

- **2023 baseline:** "Will ChatGPT Replace Ediscovery Review Teams?" — John Tredennick & Dr. William Webber (Merlin Search Technologies), Law.com Feb 2023 + EDRM Mar 2023 (edrm.net/2023/03/will-chatgpt-replace-ediscovery-review-teams/). GPT-3.5-era, Jeb Bush corpus; "reluctant to classify relevant"; hallucinations on 2 topics. Follow-ups: EDRM Jul 2023 "From Search Hits to Discovery Answers", Tredennick Medium series, Jan 2026 book. **Per-topic tables = Phase 0 extraction task.**
- **Bush corpus (validation layer):** sourced from the **HiCAL public sample** (github.com/hical/sample-dataset) — 50,000 documents drawn from TREC 2016 Total Recall athome4 (full corpus: 290,099 redacted Bush emails, 34 topics), with the sample's 9 designated topics (401–409) and the athome4 graded judgments. Full-corpus access (gated by the TREC Total Recall Usage Agreements + server protocol) is **not pursued**. See Acquisition findings.
- **Enron:** EDRM Enron v2 (PII-cleaned, 153 custodian PSTs, ~8.6 GB): archive.org + enrondata.readthedocs.io; UMD helpers at trec-legal.umiacs.umd.edu/corpora/trec/legal10/. Ground truth: TREC 2010 Legal Track **Learning task** used EDRM Enron v2, topics 200–207; **topic 201 = "Prepay transactions"** (207 = football). Qrels at trec.nist.gov/data/legal10.html — **sampled/stratified**, so Enron metrics need strata-aware estimators.
- **DOJ ~186 trial-exhibit mapping:** no public mapping found → optional stretch, drop unless found in Phase 0.
- **Craig Ball hook confirmed:** "Still on Dial-Up…", Aug 15 2025, craigball.net + EDRM.
- **Tooling:** libratom/pypff (+ readpst fallback) for PSTs; jwzthreading + custom subject/quoted-text fallback (Enron lacks In-Reply-To headers); **no OSS inclusive-email detector → build custom**; datasketch MinHash/LSH; WeasyPrint render → PyMuPDF true redactions (`apply_redactions`) → pikepdf/ReportLab Bates; Concordance DAT (þ/¶) + Opticon OPT. Anthropic Batch API: 50% off, **10K requests/batch max**, 24h window. Batch pricing per MTok in/out: Haiku 4.5 $0.50/$2.50, Sonnet 4.6 $1.50/$7.50, Fable 5 $5/$25.

## Acquisition findings (2026-06-12 / 2026-06-13, local-Mac session — resolves the P0 external tasks)

- **Bush sourcing correction (2026-06-13).** The full 290K corpus is gated behind the TREC
  Total Recall server protocol (submit doc IDs, server returns relevance) + signed usage
  agreements, and carries a **documented PII-leak history** (Bush's Feb 2015 self-publication
  exposed bystanders' SSNs/addresses/medical info; raw PSTs pulled within a day, replaced
  with a redacted version; floating copies e.g. on archive.org are unvetted). We therefore
  source Bush from the **HiCAL public sample** (github.com/hical/sample-dataset, branch
  `master`) — the redacted/sampled lineage with clean provenance — and **do not pursue full
  TREC access**. Three files, sha256-pinned in `config/corpora/bush.yaml`, downloaded from
  the GitHub raw endpoint (no allowlist/agreement needed): `athome4.topics.sample` (the 9
  designated topics 401–409), `athome4.qrel.sample`, `athome4_sample.tgz` (50K whole docs,
  48.9 MB gzip blob — verified not git-LFS; IDs are 6-digit athome4 numbers under an
  `athome4_test/` prefix).
- **qrel format & metrics.** `athome4.qrel.sample` is standard TREC `topic 0 docno rel`
  (col 2 = unused iteration field), graded rel 0/1/2, **no inclusion-probability column** →
  the Bush side needs **no strata-aware estimator** (unlike Enron's TREC 2010 sample).
  `qrels.parse` reads the topic IDs from `topics.sample` and restricts the judgments to
  those 9 (the file ships all 34 athome4 topics; only the 9 the doc-sample supports are
  kept) → 14,428 graded judgments. **Coverage verified: all 13,959 distinct judged docs for
  401–409 are present in the 50K sample (0 missing)** — recall/precision on the sample is
  fully computable. HiCAL's `process.py` (paragraph-segments docs into `{name}.{idx}` for
  their CAL setup) is **ignored**; we ingest whole docs keyed by athome4 doc ID — a single
  namespace, so the idmap is a trivial filename↔doc-ID join.
- **2023 baseline detail:** the study ran **all 34 athome4 topics (401–434)** on GPT-3.5,
  sampling per topic 20 docs from rel=2, 20 from rel=1, 40 from rel=0 (~3,000 total), prompt
  "yes/no/maybe + 25–40-word reasoning". **No per-topic table was ever published** — results
  appear only as a scatter plot; the citable aggregate is **11/34 topics at ≥75% recall and
  ≥60% precision** (19/34 passed recall alone). All 9 HiCAL topics fall in their 401–434
  range, so **overlap is complete (9/9)**; P3 reports per-topic Claude numbers vs that
  aggregate, plus a replication of their 20/20/40 cell (all 9 sample topics have ≥20 judged
  docs in each grade — verified). **427 Slot Machines** (their worked example) is **not** in
  the sample; **401 Summer Olympics** (their other named example) **is** → our anchor.
- **Chosen topics:** all 9 HiCAL sample topics — 401 Summer Olympics, 402 Space, 403 Bottled
  Water, 404 Eminent Domain, 405 Newt Gingrich, 406 Felon Disenfranchisement, 407 Faith-Based
  Initiatives, 408 Invasive Species, 409 Climate Change. Rationale in `config/corpora/bush.yaml`.

## Prerequisite (Dan, outside the repo)

For **Enron** (still primary, still allowlist-gated), extend the environment network allowlist with: `archive.org` (+ `*.us.archive.org` download nodes), `trec-legal.umiacs.umd.edu`, `trec.nist.gov`, `data.nist.gov`, `edrm.net`, `pypi.org`/`files.pythonhosted.org` (if not already). **Bush no longer needs this** — the HiCAL sample is a plain download from `raw.githubusercontent.com` (allowlist that host if running in the egress-gated container; local-Mac sessions already have it). Until the Enron hosts are open, Phase 0 (scaffold), the whole Bush/HiCAL leg, and all synthetic-fixture work proceed; Enron gates wait.

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
e.g. review --corpus bush --topic 401 --tier 1 [--dev-set] [--dry-run]
```
Review idempotency contract: candidates = in-scope docs minus docs with a current decision for (topic, phase, tier, prompt_version). `--dry-run` prints candidate count, token estimate, projected cost, remaining budget.

## Cost guardrails

`config/budget.yaml`: per-model batch prices (updatable), per-phase USD caps (dev_loop $25 default, others set with Dan before full runs), `total_stop_usd`. `review/cost.py`: estimate → cap check (raises BudgetExceeded pre-submission) → record actuals from batch results. Mid-run: stop submitting chunks when cap crossed, drain submitted batches, write stop-report.

## Phases & gates (ordered: Bush/HiCAL dev-set loop first = cheapest engine shakeout; Enron is the primary showcase that follows)

- **P0 Scaffold + acquisition spec** — skeleton, pyproject, configs, ids/store, CLI stubs, CLAUDE.md, test harness, corpus manifests (URL+sha256), `acquire --verify-only` mode. External: extract 2023 study topics/tables; verify Total Recall usage terms. *Gate:* pytest green; `status` runs; manifests committed.
- **P1 Bush ingest + qrels + dev set** — extract `athome4_sample.tgz` → whole-doc messages parquet; `qrels.parse` (HiCAL `topic 0 docno rel`, restricted to the 9 sample topics); trivial filename↔doc-ID idmap (single namespace); seeded stratified dev set (150 relevant + up to 350 judged negatives/topic). *Gate:* doc count == 50,000; idmap 100% on the 9 topics (verified: all judged docs are in the sample); dev-set IDs committed; re-run byte-identical. **[DONE — qrels + dev set 2026-06-13; ingest + idmap + coverage artifact 2026-07-20.]**
- **P2 Review engine + first metrics** — protocol v1 (human-edited), batch runner (build → dry-run → ≤10K chunks → poll → parse), cost caps, decision log, tiering, metrics. Tier 1 Haiku on dev set → recall/precision/F1; iterate protocol (version bumps); tier-2 borderline routing. *Gate:* dev run under cap; metrics artifact + hand-computed-confusion-matrix test green; re-run submits 0 requests; mock-client batch tests green.
- **P3 Bush/HiCAL validation runs** — all 9 sample topics (401–409): tier 1 → tier 2 borderline + stratified QC sample → elusion sample. *Gate:* zero decision gaps; spend reconciles with console (capped by `bush_sample`); **2023-vs-2026 comparison table in artifacts/** — per-topic Claude recall/precision on the 9 overlapping topics vs the 2023 study's aggregate (11/34 at ≥75%/≥60%), plus the 20/20/40 replication cell where sample grades allow. A modest but legitimate comparison; Enron remains the end-to-end showcase.
- **P4 Enron ingest + preprocessing** — PST parse (per-PST subprocess isolation), dedup, threading, inclusive detection, custodian tagging. *Gate:* threading/dedup/inclusive unit+golden tests green; parse failure rate <1%, itemized; fallback path tested.
- **P5 Enron qrels + topic 201 review** — idmap cascade vs TREC 2010 qrels (coverage gate ≥ ~90% of judged docs for selected custodians **before any token spend**; else swap custodians — idmap per-custodian is cheap); strata-aware metrics; dev set → full run, reusing P2 engine unchanged.
- **P6 Privilege (Enron)** — deterministic counsel screen (in-house: Derrick, Haedicke, Mintz, Mordaunt, Rogers…; V&E: Dilg, Hendrick, Astin… — verify list against Powers Report in-phase) → Fable 5 privilege pass → EDRM-style privilege log. *Gate:* same decision schema; log golden test; name/domain matcher unit tests.
- **P7 Production** — render → redact → Bates (`ENRON-{seq:08d}`, persisted allocator) → DAT/OPT. *Gate:* Bates continuity; **redaction verification: extract text from every redacted PDF, assert redacted strings absent**; DAT/OPT golden-byte tests.
- **P8 UI + narrative + publication** — static HTML per topic (embedded JSON, approve/override, export → human-delta), case narrative from responsive set only, final README, writeup support. *Gate:* file:// open with full topic data; override roundtrip test; ethics grep pass over all committed artifacts.

## Testing strategy

Unit (synthetic fixtures, zero network): threading edge cases, inclusive truth tables, dedup determinism, ids stability, qrels formats (HiCAL graded `topic 0 docno rel`; Enron sampled/weighted — P5), dev-set stratification + determinism, idmap ambiguity, Bates allocator, tiering, cost caps. Golden: DAT/OPT bytes, privilege log CSV, redacted-PDF extracted text, rendered prompt (catches prompt drift). Metrics vs hand-computed fixtures (incl. weighted sampled-qrels estimator). Mock Anthropic batch lifecycle incl. errored/expired requests → retry-via-idempotency. 30-message integration smoke through full pipeline with mock LLM. Phase gates = pytest markers + artifact checks; CLAUDE.md checklist updated only when green.

## Top risks

1. **Enron qrels↔PST ID mapping** — tiered matcher + hard coverage gate before token spend; honest coverage reporting; custodian swap fallback.
2. **Acquisition terms/access** — *retired for Bush* (HiCAL public sample, plain download, clean provenance; full TREC access not pursued). Remaining exposure is Enron only (EDRM v2 + allowlist); separable acquire step keeps everything downstream keyed off checksummed `data/raw/`.
3. **2023 comparison apples-to-apples** — *resolved:* all 9 HiCAL topics fall in the study's 401–434, so overlap is complete; comparison is per-topic Claude vs their published aggregate (no per-topic table exists), plus the 20/20/40 cell. Modest but legitimate claim.
4. **Budget** — caps + dry-run discipline + Haiku-first tiering; if dev-set borderline rate >25%, fix protocol before scaling.
5. **PST parse / pypff instability** — readpst fallback, per-PST isolation, <1% failure gate.

## Verification (end-to-end)

`pytest` green at every gate; integration smoke runs the full pipeline on synthetic fixtures in CI-seconds; first real validation = P2 dev-set metrics vs athome qrels; `python -m pipeline status` re-orients any fresh session. Commit + push to `claude/ediscovery-ai-benchmark-lv2ikj` at each gate.
