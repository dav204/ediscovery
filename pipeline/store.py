"""Parquet table schemas and I/O. The ONLY place schemas are defined.

Tables are partitioned by corpus on disk: data/store/{corpus}/{table}.parquet.
Reads go through duckdb for querying; writes go through pyarrow so the schema
is enforced at write time.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MESSAGES = pa.schema(
    [
        ("doc_id", pa.string()),
        ("corpus", pa.string()),
        ("custodian", pa.string()),
        ("source_file", pa.string()),
        ("source_index", pa.int32()),
        ("message_id_hdr", pa.string()),
        ("in_reply_to", pa.string()),
        ("references", pa.list_(pa.string())),
        ("from_addr", pa.string()),
        ("from_name", pa.string()),
        ("to", pa.list_(pa.string())),
        ("cc", pa.list_(pa.string())),
        ("bcc", pa.list_(pa.string())),
        ("subject", pa.string()),
        ("subject_norm", pa.string()),
        ("date_utc", pa.timestamp("us", tz="UTC")),
        ("date_raw", pa.string()),
        ("body_text", pa.string()),
        ("body_norm", pa.string()),
        ("body_hash", pa.string()),
        ("has_attachments", pa.bool_()),
        ("attachment_count", pa.int32()),
        ("attachment_names", pa.list_(pa.string())),
        ("headers_json", pa.string()),
        ("parse_warnings", pa.list_(pa.string())),
        ("token_estimate", pa.int32()),
    ]
)

DEDUP_EXACT = pa.schema(
    [
        # sha256(subject_norm + "\x00" + body_norm) — NOT body-only: 45% of the
        # Enron v2 export shares one placeholder body (calendar/notes items),
        # so body-only clustering would collapse distinct documents (2026-07-21).
        ("dedup_key", pa.string()),
        ("canonical_doc_id", pa.string()),
        ("doc_id", pa.string()),
        ("dup_rank", pa.int32()),
    ]
)

DEDUP_NEAR = pa.schema(
    [
        ("doc_id", pa.string()),
        ("cluster_id", pa.string()),
        ("canonical_doc_id", pa.string()),
        ("jaccard_to_canonical", pa.float64()),
        ("minhash_seed_version", pa.string()),
    ]
)

THREADS = pa.schema(
    [
        ("doc_id", pa.string()),
        ("thread_id", pa.string()),
        ("parent_doc_id", pa.string()),
        ("depth", pa.int32()),
        ("method", pa.string()),  # jwz | subject_fallback | containment | singleton
        ("is_inclusive", pa.bool_()),
        ("inclusive_reason", pa.string()),  # terminal_node | unique_content | attachment_unique
    ]
)

QRELS_RAW = pa.schema(
    [
        ("corpus", pa.string()),
        ("topic", pa.string()),
        ("trec_doc_id", pa.string()),
        ("relevance", pa.int32()),
        ("stratum", pa.string()),
        ("sampling_weight", pa.float64()),
    ]
)

DOC_ID_MAP = pa.schema(
    [
        ("corpus", pa.string()),
        ("doc_id", pa.string()),
        ("trec_doc_id", pa.string()),
        ("match_method", pa.string()),  # filename | message_id | hash | tuple_fuzzy
        ("confidence", pa.float64()),
        ("ambiguous", pa.bool_()),
    ]
)

DECISIONS = pa.schema(
    [
        ("decision_id", pa.string()),
        ("doc_id", pa.string()),
        ("corpus", pa.string()),
        ("topic", pa.string()),
        ("phase", pa.string()),  # responsiveness | privilege | qc
        ("tier", pa.int32()),
        ("model", pa.string()),
        ("prompt_version", pa.string()),
        ("prompt_hash", pa.string()),
        ("batch_id", pa.string()),
        ("custom_id", pa.string()),
        ("decision", pa.string()),
        ("confidence", pa.float64()),
        ("rationale", pa.string()),
        ("input_tokens", pa.int64()),
        ("output_tokens", pa.int64()),
        ("cost_usd", pa.float64()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("supersedes", pa.string()),
    ]
)

OVERRIDES = pa.schema(
    [
        ("doc_id", pa.string()),
        ("topic", pa.string()),
        ("original_decision_id", pa.string()),
        ("override_decision", pa.string()),
        ("note", pa.string()),
        ("override_at", pa.timestamp("us", tz="UTC")),
    ]
)

TABLES: dict[str, pa.Schema] = {
    "messages": MESSAGES,
    "dedup_exact": DEDUP_EXACT,
    "dedup_near": DEDUP_NEAR,
    "threads": THREADS,
    "qrels_raw": QRELS_RAW,
    "doc_id_map": DOC_ID_MAP,
    "decisions": DECISIONS,
    "overrides": OVERRIDES,
}


def table_path(store_root: Path, corpus: str, table: str) -> Path:
    if table not in TABLES:
        raise KeyError(f"unknown table {table!r}; registered: {sorted(TABLES)}")
    return Path(store_root) / corpus / f"{table}.parquet"


def write_table(store_root: Path, corpus: str, table: str, data: pa.Table) -> Path:
    schema = TABLES[table]
    data = data.cast(schema)  # raises if columns are missing or mistyped
    path = table_path(store_root, corpus, table)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(data, path)
    return path


def read_table(store_root: Path, corpus: str, table: str) -> pa.Table:
    return pq.read_table(table_path(store_root, corpus, table))
