<!-- generated: verbatim v1 from official topic text (pipeline protocol) -->
# Enron (TREC 2010 Legal Learning task) topic 201: Prepay transactions

You are a document reviewer in a civil litigation document review. Decide whether
the document below is responsive to this production request:

"All documents or communications that describe, discuss, refer to, report on, or relate to the Company’s engagement in structured commodity transactions known as “prepay transactions.”"

## How to decide

- Judge the document against the production request exactly as written.
- Review the email metadata and body. Attachments are listed by name only; do not
  infer their contents beyond what the name and the email text say.
- Answer "responsive" if the document falls within the request, "not_responsive" if
  it does not, and "borderline" only if you genuinely cannot decide from the request
  as written.
- Give a confidence between 0 and 1 for your decision.

## Output

Reply with a single JSON object and nothing else:
{"decision": "responsive" | "not_responsive" | "borderline", "confidence": <number from 0 to 1>, "rationale": "<at most two sentences in your own words; never quote the document>"}
