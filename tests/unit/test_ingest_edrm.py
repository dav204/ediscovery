"""Enron EDRM-XML ingest on synthetic zips (no real corpus content)."""

import json
import zipfile

import pytest

from pipeline.ingest.edrm_xml import (
    build_idmap,
    ingest_zip,
    manifest_messages,
    parse_eml,
    zip_custodian,
)
from pipeline.store import DOC_ID_MAP

pytestmark = pytest.mark.phase4

SYNTH_EML = (
    b"Message-ID: <100.synthetic@example>\r\n"
    b"In-Reply-To: <99.synthetic@example>\r\n"
    b"References: <98.synthetic@example> <99.synthetic@example>\r\n"
    b"Date: Mon, 7 Jan 2002 09:05:00 -0800 (PST)\r\n"
    b"From: Alice Example <alice@example.com>\r\n"
    b"To: bob@example.com, Carol Example <carol@example.com>\r\n"
    b"Subject: RE: synthetic widget totals\r\n"
    b"X-SDOC: 123456\r\n"
    b"X-Folder: \\Example\\Inbox\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=us-ascii\r\n"
    b"\r\n"
    b"Synthetic body about widget totals.\r\n"
)

# The real-data norm: no References/In-Reply-To, no X-headers beyond the export set.
SYNTH_EML_BARE = (
    b"Message-ID: <200.synthetic@example>\r\n"
    b"Date: Mon, 7 Jan 2002 09:05:00 -0800\r\n"
    b"From: alice@example.com\r\n"
    b"Subject: bare synthetic\r\n"
    b"Content-Type: text/plain; charset=us-ascii\r\n"
    b"\r\n"
    b"Bare synthetic body.\r\n"
)

MANIFEST_REC = {"has_attachments": False, "attachment_count": 0, "attachment_names": []}


def doc_xml(docid, native_dir, native_name,
            custodian="Alice Example<alice@example.com>",
            attachments=("widgets.xls",), include_attachment_tags=True):
    custodian = custodian.replace("<", "&lt;").replace(">", "&gt;")
    attachment_tags = ""
    if include_attachment_tags:
        attachment_tags = f"""
        <Tag TagName="#HasAttachments" TagValue="{'true' if attachments else 'false'}" TagDataType="Boolean"/>
        <Tag TagName="#AttachmentCount" TagValue="{len(attachments)}" TagDataType="LongInteger"/>
        <Tag TagName="#AttachmentNames" TagValue="{';'.join(attachments)}" TagDataType="Text"/>"""
    return f"""
    <Document DocID="{docid}" DocType="Message" MimeType="message/rfc822">
      <Tags>
        <Tag TagName="#Subject" TagValue="RE: synthetic widget totals" TagDataType="Text"/>{attachment_tags}
      </Tags>
      <Files>
        <File FileType="Native">
          <ExternalFile FilePath="{native_dir}" FileName="{native_name}" FileSize="1" Hash="aa"/>
        </File>
        <File FileType="Text">
          <ExternalFile FilePath="text_000" FileName="{docid}.txt" FileSize="1" Hash="bb"/>
        </File>
      </Files>
      <Locations><Location>
        <Custodian>{custodian}</Custodian><LocationURI>synthetic.pst</LocationURI>
      </Location></Locations>
    </Document>"""


def attachment_xml(docid):
    return f"""
    <Document DocID="{docid}" DocType="File" MimeType="application/vnd.ms-excel">
      <Tags><Tag TagName="#FileName" TagValue="widgets.xls" TagDataType="Text"/></Tags>
      <Files><File FileType="Native">
        <ExternalFile FilePath="native_000" FileName="{docid}.xls" FileSize="1" Hash="cc"/>
      </File></Files>
      <Locations><Location><Custodian>Alice Example&lt;alice@example.com&gt;</Custodian></Location></Locations>
    </Document>"""


def make_zip(path, loadfiles, natives):
    """loadfiles: {xml_name: documents_xml} — real zips hold 3-4 load files."""
    with zipfile.ZipFile(path, "w") as zf:
        for xml_name, documents_xml in loadfiles.items():
            zf.writestr(
                xml_name,
                f"<Root><Batch><Documents>{documents_xml}</Documents>"
                f"<Relationships/></Batch></Root>",
            )
        for name, data in natives.items():
            zf.writestr(name, data)
    return path


def one_loadfile(documents_xml):
    return {"zl_skilling-j_650_PSAD_000.xml": documents_xml}


def test_zip_custodian_variants():
    assert zip_custodian("edrm-enron-v2_skilling-j_xml.zip") == "skilling-j"
    assert zip_custodian("edrm-enron-v2_kaminski-v_xml_1of2.zip") == "kaminski-v"
    with pytest.raises(ValueError, match="custodian"):
        zip_custodian("random.zip")


def test_manifest_messages_only_message_docs(tmp_path):
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        one_loadfile(
            doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml") + attachment_xml("3.100.AAA.1")
        ),
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    with zipfile.ZipFile(path) as zf:
        records, failures = manifest_messages(zf, path.name)
    assert failures == []
    assert [r["docid"] for r in records] == ["3.100.AAA"]
    rec = records[0]
    assert rec["native"] == "native_000/3.100.AAA.eml"
    assert rec["custodian"] == "Alice Example<alice@example.com>"
    assert rec["has_attachments"] and rec["attachment_count"] == 1
    assert rec["attachment_names"] == ["widgets.xls"]


def test_manifest_absent_attachment_tags_default_empty(tmp_path):
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        one_loadfile(
            doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml", include_attachment_tags=False)
        ),
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    with zipfile.ZipFile(path) as zf:
        records, failures = manifest_messages(zf, path.name)
    assert failures == []
    rec = records[0]
    assert not rec["has_attachments"]
    assert rec["attachment_count"] == 0 and rec["attachment_names"] == []


def test_manifest_malformed_entries_itemized_not_crashed(tmp_path):
    missing_docid = doc_xml("PLACEHOLDER", "native_000", "x.eml").replace(
        'DocID="PLACEHOLDER" ', ""
    )
    bad_count = doc_xml("3.200.BBB", "native_000", "3.200.BBB.eml").replace(
        'TagName="#AttachmentCount" TagValue="1"', 'TagName="#AttachmentCount" TagValue="one"'
    )
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        one_loadfile(
            doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml") + missing_docid + bad_count
        ),
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    with zipfile.ZipFile(path) as zf:
        records, failures = manifest_messages(zf, path.name)
    assert [r["docid"] for r in records] == ["3.100.AAA"]
    assert {f["error"] for f in failures} == {
        "manifest_missing_docid", "manifest_bad_attachment_count",
    }


def test_parse_eml_fields():
    rec = {"has_attachments": True, "attachment_count": 1, "attachment_names": ["widgets.xls"]}
    row = parse_eml(SYNTH_EML, source_file="z.zip/native_000/3.100.AAA.eml",
                    custodian="skilling-j", manifest=rec)
    assert row["doc_id"].startswith("enron-")
    assert row["message_id_hdr"] == "<100.synthetic@example>"
    assert row["references"] == ["<98.synthetic@example>", "<99.synthetic@example>"]
    assert row["from_addr"] == "alice@example.com" and row["from_name"] == "Alice Example"
    assert row["to"] == ["bob@example.com", "Carol Example <carol@example.com>"]
    assert (row["date_utc"].hour, row["date_utc"].minute) == (17, 5)  # -0800 -> UTC
    assert row["subject_norm"] == "synthetic widget totals"
    assert row["body_text"].startswith("Synthetic body")
    assert row["attachment_names"] == ["widgets.xls"]
    assert row["parse_warnings"] == []
    # metadata rides along in every rendered prompt -> counted in the estimate
    assert row["token_estimate"] > len("Synthetic body about widget totals.") // 4


def test_parse_eml_bare_message_norm():
    row = parse_eml(SYNTH_EML_BARE, source_file="z.zip/native_000/3.200.BBB.eml",
                    custodian="skilling-j", manifest=MANIFEST_REC)
    assert row["references"] == [] and row["in_reply_to"] is None
    assert row["headers_json"] == "{}"
    assert row["parse_warnings"] == []


def test_parse_eml_headers_json_exactly_export_xheaders():
    eml = SYNTH_EML.replace(
        b"X-SDOC: 123456\r\n",
        b"X-SDOC: 123456\r\nX-ZLID: zl-1\r\nX-Filename: a.pst\r\nX-Decoy: nope\r\n",
    )
    row = parse_eml(eml, source_file="z.zip/n/x.eml", custodian="skilling-j",
                    manifest=MANIFEST_REC)
    assert json.loads(row["headers_json"]) == {
        "X-SDOC": "123456", "X-ZLID": "zl-1", "X-Filename": "a.pst",
        "X-Folder": "\\Example\\Inbox",
    }


def test_parse_eml_date_warning_vocabulary():
    naive = SYNTH_EML_BARE.replace(
        b"Date: Mon, 7 Jan 2002 09:05:00 -0800\r\n",
        b"Date: Mon, 7 Jan 2002 09:05:00 -0000\r\n",
    )
    row = parse_eml(naive, source_file="z.zip/n/x.eml", custodian="c", manifest=MANIFEST_REC)
    assert row["parse_warnings"] == ["date_no_tz_assumed_utc"]
    assert (row["date_utc"].hour, row["date_utc"].tzname()) == (9, "UTC")

    garbage = SYNTH_EML_BARE.replace(
        b"Date: Mon, 7 Jan 2002 09:05:00 -0800\r\n", b"Date: banana\r\n"
    )
    row = parse_eml(garbage, source_file="z.zip/n/x.eml", custodian="c", manifest=MANIFEST_REC)
    assert row["parse_warnings"] == ["date_unparsed"] and row["date_utc"] is None


def test_parse_eml_multipart_and_body_fallbacks():
    multipart = (
        b"Message-ID: <300.synthetic@example>\r\n"
        b"Date: Mon, 7 Jan 2002 09:05:00 -0800\r\n"
        b"From: alice@example.com\r\n"
        b"Subject: multipart synthetic\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=BOUND\r\n"
        b"\r\n"
        b"--BOUND\r\n"
        b"Content-Type: text/plain; charset=us-ascii\r\n"
        b"\r\n"
        b"Synthetic multipart body.\r\n"
        b"--BOUND\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename=x.bin\r\n"
        b"\r\n"
        b"AAAA\r\n"
        b"--BOUND--\r\n"
    )
    row = parse_eml(multipart, source_file="z.zip/n/x.eml", custodian="c",
                    manifest=MANIFEST_REC)
    assert row["body_text"].strip() == "Synthetic multipart body."
    assert row["parse_warnings"] == []

    html_only = multipart.replace(
        b"Content-Type: text/plain; charset=us-ascii", b"Content-Type: text/html"
    )
    row = parse_eml(html_only, source_file="z.zip/n/x.eml", custodian="c",
                    manifest=MANIFEST_REC)
    assert row["body_text"] == "" and "no_text_body_part" in row["parse_warnings"]

    weird_charset = SYNTH_EML_BARE.replace(b"charset=us-ascii", b"charset=x-mystery")
    row = parse_eml(weird_charset, source_file="z.zip/n/x.eml", custodian="c",
                    manifest=MANIFEST_REC)
    assert "body_decode_fallback_latin1" in row["parse_warnings"]
    assert "Bare synthetic body." in row["body_text"]


def test_ingest_zip_merges_loadfiles_sorted_and_itemizes_failures(tmp_path):
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        {
            "zl_skilling-j_650_PSAD_000.xml":
                doc_xml("3.300.CCC", "native_000", "3.300.CCC.eml")     # missing native
                + doc_xml("3.200.BBB", "native_000", "3.200.BBB.eml"),
            "zl_skilling-j_650_PSAD_001.xml":
                doc_xml("3.100.AAA", "native_001", "3.100.AAA.eml")
                + doc_xml("3.400.DDD", "native_001", "3.400.DDD.eml",
                          custodian="Someone Else<else@example.com>"),
        },
        {
            "native_001/3.100.AAA.eml": SYNTH_EML,
            "native_000/3.200.BBB.eml": SYNTH_EML,
            "native_001/3.400.DDD.eml": SYNTH_EML,
        },
    )
    rows, docids, failures = ingest_zip(path)
    assert docids == ["3.100.AAA", "3.200.BBB"]  # cross-loadfile, sorted
    assert [r["custodian"] for r in rows] == ["skilling-j", "skilling-j"]
    errors = {f["docid"]: f["error"] for f in failures}
    assert errors == {"3.300.CCC": "KeyError", "3.400.DDD": "custodian_inconsistent"}

    idmap = build_idmap(rows, docids)
    idmap.cast(DOC_ID_MAP)
    assert idmap.column("trec_doc_id").to_pylist() == docids


def test_ingest_zip_rejects_duplicate_docids_within_zip(tmp_path):
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        {
            "zl_skilling-j_650_PSAD_000.xml": doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml"),
            "zl_skilling-j_650_PSAD_001.xml": doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml"),
        },
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    with pytest.raises(ValueError, match="duplicate DocIDs within"):
        ingest_zip(path)


def test_ingest_zip_deterministic(tmp_path):
    path = make_zip(
        tmp_path / "edrm-enron-v2_skilling-j_xml.zip",
        one_loadfile(doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml")),
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    assert ingest_zip(path) == ingest_zip(path)


# -- main(): failure-rate gate, resume, duplicate DocIDs, dispatch ------------

def make_main_env(tmp_path, monkeypatch, zips):
    from argparse import Namespace

    import pipeline.ingest.edrm_xml as edrm_mod
    from pipeline.config import CorpusConfig, CorpusFile, PipelineConfig

    raw = tmp_path / "raw"
    (raw / "enron" / "xml").mkdir(parents=True)
    files = []
    for zip_name, spec in zips.items():
        if spec is not None:
            loadfiles, natives = spec
            make_zip(raw / "enron" / "xml" / zip_name, loadfiles, natives)
        files.append(CorpusFile(name=zip_name, url=None, sha256=None,
                                dest=f"xml/{zip_name}"))
    paths = {
        "raw": raw, "store": tmp_path / "store", "artifacts": tmp_path / "artifacts",
        "batches": tmp_path / "b", "decisions": tmp_path / "d",
        "spend": tmp_path / "s", "productions": tmp_path / "p",
    }
    cfg = PipelineConfig(paths=paths, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    corpus_cfg = CorpusConfig(corpus="enron", format="edrm-xml-v2", files=files,
                              chosen_topics=["201"])
    monkeypatch.setattr(edrm_mod, "load_pipeline_config", lambda: cfg)
    monkeypatch.setattr(edrm_mod, "load_corpus_config", lambda _c: corpus_cfg)
    return edrm_mod, Namespace(corpus="enron", force=False), paths


def test_main_green_and_resume(tmp_path, monkeypatch, capsys):
    edrm_mod, args, paths = make_main_env(
        tmp_path, monkeypatch,
        {"edrm-enron-v2_skilling-j_xml.zip": (
            one_loadfile(doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml")),
            {"native_000/3.100.AAA.eml": SYNTH_EML},
        )},
    )
    assert edrm_mod.main(args) == 0
    report = json.loads((paths["artifacts"] / "enron_ingest_report.json").read_text())
    assert report["messages"] == 1 and report["failures"] == 0
    assert report["per_custodian"] == {"skilling-j": 1}
    assert edrm_mod.main(args) == 0
    assert "already done" in capsys.readouterr().out


def test_main_failure_rate_gate_exit_1_and_sticky_on_resume(tmp_path, monkeypatch):
    edrm_mod, args, paths = make_main_env(
        tmp_path, monkeypatch,
        {"edrm-enron-v2_skilling-j_xml.zip": (
            one_loadfile(
                doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml")
                + doc_xml("3.300.CCC", "native_000", "3.300.CCC.eml")  # missing native
            ),
            {"native_000/3.100.AAA.eml": SYNTH_EML},
        )},
    )
    assert edrm_mod.main(args) == 1
    report = json.loads((paths["artifacts"] / "enron_ingest_report.json").read_text())
    assert report["failures"] == 1 and report["failure_rate"] == 0.5
    assert report["failure_items"][0] == {
        "docid": "3.300.CCC", "zip": "edrm-enron-v2_skilling-j_xml.zip", "error": "KeyError",
    }
    # resume must NOT flip a failed gate to green
    assert edrm_mod.main(args) == 1


def test_main_missing_zip_exit_2(tmp_path, monkeypatch):
    edrm_mod, args, _ = make_main_env(
        tmp_path, monkeypatch, {"edrm-enron-v2_lay-k_xml.zip": None}
    )
    assert edrm_mod.main(args) == 2


def test_main_duplicate_docids_across_zips_raises(tmp_path, monkeypatch):
    same = (
        one_loadfile(doc_xml("3.100.AAA", "native_000", "3.100.AAA.eml")),
        {"native_000/3.100.AAA.eml": SYNTH_EML},
    )
    edrm_mod, args, _ = make_main_env(
        tmp_path, monkeypatch,
        {"edrm-enron-v2_kaminski-v_xml_1of2.zip": same,
         "edrm-enron-v2_kaminski-v_xml_2of2.zip": same},
    )
    with pytest.raises(ValueError, match="duplicate DocIDs"):
        edrm_mod.main(args)


def test_cli_ingest_dispatch_routes_by_corpus(monkeypatch):
    from pipeline.__main__ import build_parser

    monkeypatch.setattr("pipeline.ingest.athome.main", lambda a: 41)
    monkeypatch.setattr("pipeline.ingest.edrm_xml.main", lambda a: 42)
    parser = build_parser()
    bush = parser.parse_args(["ingest", "--corpus", "bush"])
    enron = parser.parse_args(["ingest", "--corpus", "enron"])
    assert bush.func(bush) == 41
    assert enron.func(enron) == 42
