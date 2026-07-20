"""Bush athome4-sample ingest on synthetic documents (no real corpus content)."""

import io
import tarfile

import pyarrow as pa
import pytest

from pipeline.ingest.athome import (
    build_idmap,
    coverage_report,
    ingest_tarball,
    parse_doc,
    parse_from,
    parse_sent,
    rows_to_messages_table,
    split_headers,
)
from pipeline.ingest.normalize import normalize_body, normalize_subject
from pipeline.store import DOC_ID_MAP

pytestmark = pytest.mark.phase1

SYNTH_DOC = (
    "From:\tExample, Alice <alice@example.gov>\n"
    "Sent:\tMonday, January 07, 2002 9:05 AM\n"
    "To:\tBob Example; Carol Example\n"
    "Cc:\tdave@example.gov\n"
    "Subject:\tRE: quarterly widget totals\n"
    "Attachments:\twidgets.xls; totals.doc\n"
    "\n"
    "Please see the attached totals.\n"
    "\n"
    "> earlier quoted synthetic text\n"
)


def make_tgz(path, docs):
    """docs: {member_name: text}. Written in dict order (i.e. unsorted on purpose)."""
    with tarfile.open(path, "w:gz") as tar:
        dir_info = tarfile.TarInfo("athome4_test/")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        for name, text in docs.items():
            data = text.encode("utf-8") if isinstance(text, str) else text
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_normalize_subject_strips_stacked_prefixes():
    assert normalize_subject("Re: FW:  fwd: Widget   Totals ") == "widget totals"
    assert normalize_subject(None) is None


def test_normalize_body_unifies_endings_and_trailing_ws():
    assert normalize_body("a \r\nb\t\r\n\r\n") == "a\nb"


def test_split_headers_standard_block():
    headers, body, warnings = split_headers(SYNTH_DOC)
    assert headers["From"].startswith("Example, Alice")
    assert headers["Subject"] == "RE: quarterly widget totals"
    assert body.startswith("Please see the attached totals.")
    assert warnings == []


def test_split_headers_continuation_line_folds():
    text = "To:\tAlice Example;\n\tBob Example\nSubject:\tx\n\nbody\n"
    headers, body, _ = split_headers(text)
    assert headers["To"] == "Alice Example; Bob Example"
    assert body == "body\n"


def test_split_headers_no_block_is_all_body():
    text = "This synthetic document has no header block.\nJust text.\n"
    headers, body, warnings = split_headers(text)
    assert headers == {}
    assert body == text
    assert warnings == ["no_header_block"]


def test_split_headers_unterminated_block_warns():
    text = "From:\talice@example.gov\nnot a header and not blank\n"
    headers, body, warnings = split_headers(text)
    assert headers == {"From": "alice@example.gov"}
    assert body.startswith("not a header")
    assert "header_block_unterminated" in warnings


def test_parse_from_variants():
    assert parse_from("Example, Alice <alice@example.gov>") == ("Example, Alice", "alice@example.gov")
    assert parse_from("alice@example.gov") == (None, "alice@example.gov")
    assert parse_from("Alice Example") == ("Alice Example", None)


def test_parse_sent_formats():
    assert parse_sent("Monday, January 07, 2002 9:05 AM").year == 2002
    assert parse_sent("Monday, January 07, 2002 9:05:30 AM").second == 30
    assert parse_sent("1/7/2002 9:05 AM").month == 1
    assert parse_sent("not a date") is None


def test_parse_doc_full_fields():
    row = parse_doc("athome4_test/000123", SYNTH_DOC.encode("utf-8"))
    assert row["doc_id"].startswith("bush-") and len(row["doc_id"]) == 21
    assert row["source_file"] == "athome4_sample.tgz/athome4_test/000123"
    assert row["from_addr"] == "alice@example.gov"
    assert row["to"] == ["Bob Example", "Carol Example"]
    assert row["subject_norm"] == "quarterly widget totals"
    assert row["date_utc"] is not None and row["date_raw"].startswith("Monday")
    assert row["has_attachments"] and row["attachment_count"] == 2
    assert row["parse_warnings"] == []
    assert row["token_estimate"] > 0


def test_parse_doc_latin1_fallback_warns():
    row = parse_doc("athome4_test/000124", b"Caf\xe9 synthetic body\n")
    assert "decode_fallback_latin1" in row["parse_warnings"]
    assert "Café" in row["body_text"]


def test_ingest_tarball_sorts_and_counts(tmp_path):
    tgz = make_tgz(
        tmp_path / "sample.tgz",
        {
            "athome4_test/000300": "unsorted first\n",
            "athome4_test/000100": SYNTH_DOC,
            "athome4_test/000200": "middle\n",
        },
    )
    rows, docnos = ingest_tarball(tgz, expected_count=3)
    assert docnos == ["000100", "000200", "000300"]
    assert [r["source_file"].rsplit("/", 1)[-1] for r in rows] == docnos
    rows_to_messages_table(rows)  # raises if rows drift from store.MESSAGES

    with pytest.raises(ValueError, match="expected 4"):
        ingest_tarball(tgz, expected_count=4)


def test_ingest_tarball_rejects_non_numeric_member(tmp_path):
    tgz = make_tgz(tmp_path / "bad.tgz", {"athome4_test/README": "not a doc\n"})
    with pytest.raises(ValueError, match="not a numeric doc id"):
        ingest_tarball(tgz, expected_count=None)


def test_ingest_deterministic(tmp_path):
    tgz = make_tgz(
        tmp_path / "sample.tgz",
        {"athome4_test/000002": SYNTH_DOC, "athome4_test/000001": "short\n"},
    )
    first = rows_to_messages_table(ingest_tarball(tgz, 2)[0])
    second = rows_to_messages_table(ingest_tarball(tgz, 2)[0])
    assert first.equals(second)


def test_build_idmap_and_coverage(tmp_path):
    tgz = make_tgz(
        tmp_path / "sample.tgz",
        {"athome4_test/000001": "a\n", "athome4_test/000002": "b\n"},
    )
    rows, docnos = ingest_tarball(tgz, 2)
    idmap = build_idmap(rows, docnos)
    idmap.cast(DOC_ID_MAP)
    assert idmap.column("match_method").to_pylist() == ["filename", "filename"]

    qrels = pa.table(
        {
            "topic": ["401", "401", "402"],
            "trec_doc_id": ["000001", "000002", "999999"],
        }
    )
    report = coverage_report(qrels, set(docnos))
    assert report["topics"]["401"] == {"judged": 2, "mapped": 2, "coverage": 1.0}
    assert report["topics"]["402"]["coverage"] == 0.0
    assert report["overall"] == {"judged": 3, "mapped": 2, "coverage": round(2 / 3, 6)}
