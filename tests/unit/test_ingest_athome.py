"""Bush athome4-sample ingest on synthetic documents (no real corpus content)."""

import io
import json
import tarfile

import pyarrow as pa
import pytest

from pipeline.ingest.athome import (
    build_idmap,
    coverage_report,
    decode_doc,
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


def test_parse_sent_converts_eastern_to_utc():
    est = parse_sent("Monday, January 07, 2002 9:05 AM")
    assert (est.year, est.hour, est.minute) == (2002, 14, 5)  # EST = UTC-5
    edt = parse_sent("Monday, July 08, 2002 9:05 AM")
    assert edt.hour == 13  # EDT = UTC-4
    assert parse_sent("Monday, January 07, 2002 9:05:30 AM").second == 30
    assert parse_sent("1/7/2002 9:05 AM").month == 1
    assert parse_sent("not a date") is None


def test_split_headers_requires_tab_so_prose_stays_body():
    text = "Comment: I support widget reform\n\nPlease consider it.\n"
    headers, body, warnings = split_headers(text)
    assert headers == {}
    assert body == text
    assert warnings == ["no_header_block"]


def test_split_headers_empty_value_does_not_break_block():
    text = "From:\talice@example.gov\nSubject:\nAttachments:\twidgets.xls\n\nbody\n"
    headers, body, warnings = split_headers(text)
    assert headers == {"From": "alice@example.gov", "Subject": "", "Attachments": "widgets.xls"}
    assert body == "body\n"
    assert warnings == []


def test_split_headers_accepts_digit_keys():
    headers, _, warnings = split_headers("X400-To:\tsynthetic route\n\nbody\n")
    assert headers == {"X400-To": "synthetic route"}
    assert warnings == []


def test_decode_cp1252_before_latin1():
    text, warnings = decode_doc(b"a \x93quoted\x94 word\n")
    assert warnings == ["decode_fallback_cp1252"]
    assert "“quoted”" in text


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


def test_parse_doc_cp1252_fallback_warns():
    row = parse_doc("athome4_test/000124", b"Caf\xe9 synthetic body\n")
    assert "decode_fallback_cp1252" in row["parse_warnings"]
    assert "Café" in row["body_text"]


def test_parse_doc_latin1_last_resort():
    # 0x8f is undefined in cp1252, so only latin-1 can decode it
    row = parse_doc("athome4_test/000125", b"synthetic \x8f body\n")
    assert "decode_fallback_latin1" in row["parse_warnings"]


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


def test_ingest_tarball_rejects_duplicate_docnos(tmp_path):
    tgz = make_tgz(
        tmp_path / "dup.tgz",
        {"a/000001": "first\n", "b/000001": "second\n"},
    )
    with pytest.raises(ValueError, match="duplicate doc numbers"):
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


# -- main() orchestration: the coverage-gate exit code IS the P1 gate --------

def make_main_env(tmp_path, monkeypatch, docs, qrel_rows, expected_count):
    from argparse import Namespace

    import pipeline.ingest.athome as athome_mod
    from pipeline.config import CorpusConfig, PipelineConfig
    from pipeline.store import QRELS_RAW, write_table

    raw = tmp_path / "raw"
    (raw / "bush").mkdir(parents=True)
    make_tgz(raw / "bush" / "athome4_sample.tgz", docs)
    paths = {
        "raw": raw, "store": tmp_path / "store", "artifacts": tmp_path / "artifacts",
        "batches": tmp_path / "b", "decisions": tmp_path / "d",
        "spend": tmp_path / "s", "productions": tmp_path / "p",
    }
    cfg = PipelineConfig(paths=paths, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    corpus_cfg = CorpusConfig(corpus="bush", format="trec-athome4-sample", files=[],
                              chosen_topics=["401"], expected_message_count=expected_count)
    monkeypatch.setattr(athome_mod, "load_pipeline_config", lambda: cfg)
    monkeypatch.setattr(athome_mod, "load_corpus_config", lambda _c: corpus_cfg)
    if qrel_rows is not None:
        table = pa.table(
            {
                "corpus": ["bush"] * len(qrel_rows),
                "topic": [t for t, _, _ in qrel_rows],
                "trec_doc_id": [d for _, d, _ in qrel_rows],
                "relevance": [r for _, _, r in qrel_rows],
                "stratum": [None] * len(qrel_rows),
                "sampling_weight": [None] * len(qrel_rows),
            }
        ).cast(QRELS_RAW)
        write_table(paths["store"], "bush", "qrels_raw", table)
    return athome_mod, Namespace(corpus="bush", force=False), paths


def test_main_green_gate_exit_0(tmp_path, monkeypatch, capsys):
    docs = {"athome4_test/000001": SYNTH_DOC, "athome4_test/000002": "plain\n"}
    athome_mod, args, paths = make_main_env(
        tmp_path, monkeypatch, docs, [("401", "000001", 1), ("401", "000002", 0)], 2
    )
    assert athome_mod.main(args) == 0
    report = json.loads((paths["artifacts"] / "bush_idmap_coverage.json").read_text())
    assert report["overall"]["coverage"] == 1.0
    # resume path: second run reports done and stays green
    assert athome_mod.main(args) == 0
    assert "already done" in capsys.readouterr().out


def test_main_incomplete_coverage_exit_1(tmp_path, monkeypatch):
    docs = {"athome4_test/000001": "a\n"}
    athome_mod, args, _ = make_main_env(
        tmp_path, monkeypatch, docs, [("401", "000001", 1), ("401", "999999", 1)], 1
    )
    assert athome_mod.main(args) == 1


def test_main_missing_qrels_exit_2(tmp_path, monkeypatch):
    athome_mod, args, _ = make_main_env(
        tmp_path, monkeypatch, {"athome4_test/000001": "a\n"}, None, 1
    )
    assert athome_mod.main(args) == 2
