"""Canonical document ID derivation. The ONLY place doc_ids are minted.

doc_id = "{corpus}-{sha256(source_file + ':' + source_index)[:16]}"

Stable across re-ingest because it depends only on where the message physically
sits in the acquired corpus (relative path + ordinal), never on parsed content
or RFC Message-ID headers (missing/duplicated throughout the Enron PSTs).
TREC document IDs are foreign keys that live exclusively in the doc_id_map
table — never use them as primary keys.
"""

import hashlib

VALID_CORPORA = ("enron", "bush")


def doc_id(corpus: str, source_file: str, source_index: int) -> str:
    if corpus not in VALID_CORPORA:
        raise ValueError(f"unknown corpus {corpus!r}; expected one of {VALID_CORPORA}")
    if not source_file:
        raise ValueError("source_file must be a non-empty relative path")
    if source_index < 0:
        raise ValueError("source_index must be >= 0")
    digest = hashlib.sha256(f"{source_file}:{source_index}".encode("utf-8")).hexdigest()
    return f"{corpus}-{digest[:16]}"
