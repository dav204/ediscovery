# Bush protocol v2 (breadth guidance) — APPROVED by Dan 2026-10-23

Status: drafted 2026-10-23 by Claude in the simulated case-counsel role (CLAUDE.md rule 3).
Not in `protocols/`; nothing has been scored with it. On sign-off, each topic gets
`protocols/bush/athome4NN.v2.md` = this template with the official topic text pasted in
verbatim, exactly as v1 did. v1 numbers stay reported alongside (headline comparison to
2023 is v1).

## Why a v2

The v1 dev-set baseline (9 topics, 4,500 judged docs, Haiku 4.5) shows a consistent,
aggregate-level pattern: highly relevant (grade 2) documents are mostly found, plainly
relevant (grade 1) documents are mostly rejected — confidently, not as borderline
(topic 401: 0/51 grade-1 found; 92 of 105 misses at ~0.97 confidence). v1 tells the model
to judge "against the production request exactly as written", which it reads narrowly,
while TREC assessors construed "concerning" broadly. The change below is standard
first-pass review instruction. It was written from that aggregate pattern and general
practice only; no per-document labels were read.

## What changes versus v1

1. "Concerning" is construed broadly: the document need only relate to the subject in some
   substantive way, not be primarily about it.
2. Any part counts: body, subject line, quoted earlier messages, forwarded material,
   named attachments.
3. The production standard favors inclusion: when unsure whether a document concerns the
   subject, code it responsive rather than borderline or not responsive.
4. `borderline` is reserved for documents where even a broad reading cannot decide.

Expected effect: recall up, precision down somewhat; borderline volume down (fewer tier-2
escalations, so cheaper). Whether the trade is good is exactly what the dev loop measures.

## Template (the `{request}` slot is filled verbatim from the official topic text)

```
# Jeb Bush email (TREC 2016 Total Recall athome4) topic {num}: {title}

You are a first-pass document reviewer in a civil litigation document review. Decide
whether the document below is responsive to this production request:

"{request}"

## How to decide

- Read "concerning" broadly. A document is responsive if any part of it relates to the
  subject of the request in a substantive way, even if that is not its main topic.
  Scheduling, logistics, press, constituent views, and internal discussion all count
  when they are about the subject.
- Any part of the document counts: the body, the subject line, quoted or forwarded
  earlier messages, and attachments listed by name.
- Not responsive: no connection to the subject, or a word in common with no
  substantive connection.
- The production standard favors inclusion. If you are unsure whether the document
  concerns the subject, answer "responsive".
- Answer "borderline" only if even this broad reading genuinely cannot decide.
- Give a confidence between 0 and 1 for your decision.

## Output

Reply with a single JSON object and nothing else:
{"decision": "responsive" | "not_responsive" | "borderline", "confidence": <number from 0 to 1>, "rationale": "<at most two sentences in your own words; never quote the document>"}
```
