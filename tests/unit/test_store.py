from datetime import datetime, timezone

import pyarrow as pa
import pytest

from pipeline import store

pytestmark = pytest.mark.phase0


def _minimal_messages_table() -> pa.Table:
    now = datetime(2001, 5, 1, 12, 0, tzinfo=timezone.utc)
    row = {
        "doc_id": "enron-0000000000000000",
        "corpus": "enron",
        "custodian": "skilling-j",
        "source_file": "pst/skilling-j/a.pst",
        "source_index": 0,
        "message_id_hdr": None,
        "in_reply_to": None,
        "references": [],
        "from_addr": "sender@example.com",
        "from_name": "Sender",
        "to": ["rcpt@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "Re: test",
        "subject_norm": "test",
        "date_utc": now,
        "date_raw": "Tue, 1 May 2001 07:00:00 -0500",
        "body_text": "synthetic fixture body",
        "body_norm": "synthetic fixture body",
        "body_hash": "ab" * 32,
        "has_attachments": False,
        "attachment_count": 0,
        "attachment_names": [],
        "headers_json": "{}",
        "parse_warnings": [],
        "token_estimate": 7,
    }
    return pa.Table.from_pylist([row])


def test_messages_roundtrip(tmp_path):
    table = _minimal_messages_table()
    store.write_table(tmp_path, "enron", "messages", table)
    back = store.read_table(tmp_path, "enron", "messages")
    assert back.schema.equals(store.MESSAGES)
    assert back.num_rows == 1
    assert back.column("doc_id")[0].as_py() == "enron-0000000000000000"


def test_write_rejects_missing_columns(tmp_path):
    bad = pa.Table.from_pylist([{"doc_id": "x"}])
    with pytest.raises(ValueError):
        store.write_table(tmp_path, "enron", "messages", bad)


def test_unknown_table_rejected(tmp_path):
    with pytest.raises(KeyError):
        store.table_path(tmp_path, "enron", "nope")


def test_all_registered_schemas_have_doc_id_or_hash_key():
    # Every table must be joinable back to documents (or be a pure key table).
    for name, schema in store.TABLES.items():
        assert any(f in schema.names for f in ("doc_id", "trec_doc_id", "body_hash")), name
