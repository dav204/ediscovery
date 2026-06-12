# Review protocols — HUMAN-EDITED ONLY

Each file here is a versioned review protocol: the "production request" prompt a
review tier receives, derived from the official TREC topic language.

Rules (mirrored in CLAUDE.md):

- Files are written and edited by Dan only. Claude Code must never create or modify
  protocol files. Prompt iteration = Dan writes a new `<topic>.vN.md`; old versions
  stay in place so every decision-log entry's `prompt_version` resolves.
- File naming: `enron/topic201.v1.md`, `bush/athome102.v1.md`,
  `privilege/enron_priv.v1.md`. The pipeline records both the version (from the
  file name) and a content hash with every decision; changing a file's content
  without bumping its version will fail the prompt-drift golden test.
- A protocol contains: the production request (official topic text), responsiveness
  definitions, the output JSON contract (decision / confidence / rationale ≤ 2
  sentences, no verbatim quotes from documents), and borderline guidance.
