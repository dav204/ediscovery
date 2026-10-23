# Obsolescence ledger

The question this project answers (Dan, 2026-10-23): can one small operation now run the
whole matter — processing, AI-assisted review, production — without the archaic
e-discovery vendor workflow, and how much of the 2012–2017 hosting-analyst /
hosted-solutions-PM job (CDS / RVM / Lighthouse) is obsolete?

Each row is a task from that job, the pipeline stage that does it here, and what it
measurably took. "Pending" rows fill in as runs land; nothing here is estimated where a
measurement can replace it. The "then" column is Dan's to correct from experience.

| Old-job task | Then (typical) | Pipeline stage | Now (measured) |
|---|---|---|---|
| Processing: PST/container ingest, exception handling | Processing queue in vendor tool, exception reports, re-runs | `ingest` (P1/P4) | 101,860 Enron messages from 4 custodian zips in ~2.2 min, 0 exceptions; 50,000 Bush docs in ~3 s |
| Global/custodial dedup, near-dup, email threading | Vendor tool settings + QC of suppression counts | `preprocess` (P4) | 101,860 → 24,778 unique docs, 1,894 near-dup clusters, 20,318 threads, inclusive flags — ~22 s, byte-reproducible |
| Search term report (STR), term negotiation, hit counts | Run terms, build STR with/without families, iterate with counsel | — (no stage yet) | Pending: candidate next layer |
| Family expansion | Pull families onto hits before batching | Partial: judged sample reviews parts as own docs | Pending |
| Review batching + reviewer management ("batches of 500 for linear review") | Batch sets, reviewer assignments, pace tracking, overturns | `review` tier 1 (Haiku, Batch API) | Projected: 500-doc dev batches ≈ $0.83–1.02 each worst case; topic 201 judged sample (2,720 docs, 1,244 attachment parts) ≈ $7.48 worst case. Pending real runs |
| Second-level / QC review | Senior reviewers on a sample + borderlines | `review` tier 2 (borderline + stratified QC sample) | Pending |
| Recall/precision validation (elusion, sampling) | Rare, manual, statistician-dependent | `validate` (TREC qrels; HT-weighted for the stratified sample) | Pending real runs; estimator verified to reproduce NIST's official numbers exactly |
| Privilege review + privilege log | Attorney review, log built by hand from coding | `privilege` (P6) | Pending |
| Production: imaging, redaction, Bates, DAT/OPT load files | Imaging queue, redaction QC, load-file QC, re-productions | `produce` (P7) | Pending |
| Status reporting to case team | Daily/weekly emails, spreadsheets | `status` / `spend` dashboards | Live: every command logged; spend vs caps on demand |

Reading this table honestly will need the "then" column populated with real throughput
and cost figures from those years, which only Dan can supply.
