"""Enron ingest: EDRM v2 XML-edition zips -> messages + doc_id_map tables.

Source layout (data/raw/enron/xml/, sha256-pinned by acquire): one zip per
custodian (kaminski-v split in two), each holding EDRM XML load files
(`zl_<custodian>_*_PSAD_NNN.xml`), `native_NNN/` with one .eml per message
(mostly single-part text/plain, ~7% multipart/mixed; attachment natives live
as sibling files, not MIME parts), and `text_NNN/` renditions (unused — the
.eml IS the text of record).

The XML manifest is the authority for which natives are messages and carries
custodian, #AttachmentCount/#AttachmentNames/#HasAttachments, and the official
EDRM v2 DocID — the same namespace the TREC 2010 Legal qrels use, so the idmap
is a filename join exactly like the Bush side. Header metadata (Message-ID,
In-Reply-To/References, addresses, Date with real UTC offsets) comes from the
.eml via the stdlib email parser; X-SDOC/X-Folder/X-Filename/X-ZLID are kept in
headers_json for the P5 mapping helpers.

Failure semantics (P4 gate): a message that cannot be parsed produces a
failure item, not a crash; artifacts/enron_ingest_report.json itemizes every
failure and main() exits 1 if the rate is >= 1%. Failure items carry ONLY a
DocID, the zip name, and a static error code (never str(e)) — the report is a
committed artifact, and exception text can embed corpus content.

P5 contract note: this idmap is MESSAGE-level. The TREC 2010 qrels also judge
attachment parts as separate docids (`<parent>.N` — the DocType="File" entries
this ingest deliberately skips); for topic 201 those are ~46% of judged docs.
P5 must decide fold-to-parent vs exclude BEFORE reading its coverage gate;
see docs/PLAN.md P5.

Determinism: zips in manifest order, documents in sorted-DocID order within
each zip, all fields pure functions of file bytes; re-ingest is byte-identical.
"""

import email
import email.policy
import email.utils
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import timezone
from pathlib import Path

import pyarrow as pa

from ..config import load_corpus_config, load_pipeline_config
from ..ids import doc_id as make_doc_id
from ..store import MESSAGES, read_table, table_path, write_table
from .normalize import body_hash, estimate_tokens, normalize_body, normalize_subject

FAILURE_RATE_GATE = 0.01

_ZIP_CUSTODIAN_RE = re.compile(r"edrm-enron-v2_(?P<custodian>.+?)_xml(?:_\d+of\d+)?\.zip$")


class IngestError(Exception):
    """Carries a static error code; failure items must never hold str(e)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _failure(docid: str | None, zip_name: str, exc: Exception) -> dict:
    return {
        "docid": docid,
        "zip": zip_name,
        "error": getattr(exc, "code", type(exc).__name__),
    }

# Kept out of headers_json noise: parsed into first-class columns already.
_XHEADERS = ("X-SDOC", "X-ZLID", "X-Folder", "X-Filename")


def zip_custodian(zip_name: str) -> str:
    m = _ZIP_CUSTODIAN_RE.search(zip_name)
    if not m:
        raise ValueError(f"cannot derive custodian from zip name {zip_name!r}")
    return m.group("custodian")


def manifest_messages(zf: zipfile.ZipFile, zip_name: str) -> tuple[list[dict], list[dict]]:
    """Parse every XML load file in the zip -> (one record per Message doc,
    itemized failures for malformed manifest entries — never a crash)."""
    records: list[dict] = []
    failures: list[dict] = []
    for xml_name in sorted(n for n in zf.namelist() if n.endswith(".xml") and "/" not in n):
        for _event, el in ET.iterparse(io.BytesIO(zf.read(xml_name)), events=("end",)):
            if el.tag != "Document":
                continue
            if el.get("DocType") == "Message":
                try:
                    docid = el.get("DocID")
                    if not docid:
                        raise IngestError("manifest_missing_docid")
                    tags = {t.get("TagName"): t.get("TagValue") for t in el.iter("Tag")}
                    native = None
                    for file_el in el.iter("File"):
                        if file_el.get("FileType") == "Native":
                            ext = file_el.find("ExternalFile")
                            if ext is not None:
                                native = f"{ext.get('FilePath')}/{ext.get('FileName')}"
                    cust_el = el.find(".//Custodian")
                    try:
                        count = int(tags.get("#AttachmentCount") or 0)
                    except ValueError:
                        raise IngestError("manifest_bad_attachment_count") from None
                    names = tags.get("#AttachmentNames") or ""
                    records.append(
                        {
                            "docid": docid,
                            "native": native,
                            "custodian": cust_el.text if cust_el is not None else None,
                            "attachment_count": count,
                            "attachment_names": [n for n in names.split(";") if n],
                            "has_attachments": (tags.get("#HasAttachments") == "true"),
                        }
                    )
                except Exception as e:
                    failures.append(_failure(el.get("DocID"), zip_name, e))
            el.clear()
    return records, failures


def _addr_list(msg, header: str) -> list[str]:
    values = msg.get_all(header, [])
    out = []
    for _name, addr in email.utils.getaddresses(values):
        formatted = email.utils.formataddr((_name or None, addr)) if addr else _name
        if formatted:
            out.append(formatted)
    return out


def _body_text(msg) -> tuple[str, list[str]]:
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    if part is None:
        return "", ["no_text_body_part"]
    try:
        return part.get_content(), []
    except (LookupError, UnicodeDecodeError, KeyError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("latin-1"), ["body_decode_fallback_latin1"]


def parse_eml(raw: bytes, *, source_file: str, custodian: str,
              manifest: dict) -> dict:
    """One native .eml + its manifest record -> one messages-table row."""
    warnings: list[str] = []
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    date_raw = msg.get("Date")
    date_utc = None
    if date_raw:
        try:
            parsed = email.utils.parsedate_to_datetime(date_raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
                warnings.append("date_no_tz_assumed_utc")
            date_utc = parsed.astimezone(timezone.utc)
        except (ValueError, TypeError):
            warnings.append("date_unparsed")

    from_name = from_addr = None
    if msg.get("From"):
        from_name, from_addr = email.utils.parseaddr(msg.get("From"))
        from_name, from_addr = (from_name or None), (from_addr or None)

    body, body_warnings = _body_text(msg)
    warnings += body_warnings
    subject = msg.get("Subject")
    norm = normalize_body(body)
    references = (msg.get("References") or "").split()

    extra = {k: msg[k] for k in _XHEADERS if msg[k]}
    # Token basis mirrors review/prompts.render_document: metadata fields ride
    # along with the body in every request, so the cost estimate counts them.
    to_list = _addr_list(msg, "To")
    cc_list = _addr_list(msg, "Cc")
    rendered_meta = " ".join(filter(None, [
        custodian, from_addr or "", "; ".join(to_list), "; ".join(cc_list),
        date_raw or "", subject or "", "; ".join(manifest["attachment_names"]),
    ]))
    return {
        "doc_id": make_doc_id("enron", source_file, 0),
        "corpus": "enron",
        "custodian": custodian,
        "source_file": source_file,
        "source_index": 0,
        "message_id_hdr": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "references": references,
        "from_addr": from_addr,
        "from_name": from_name,
        "to": to_list,
        "cc": cc_list,
        "bcc": _addr_list(msg, "Bcc"),
        "subject": subject,
        "subject_norm": normalize_subject(subject),
        "date_utc": date_utc,
        "date_raw": date_raw,
        "body_text": body,
        "body_norm": norm,
        "body_hash": body_hash(norm),
        "has_attachments": manifest["has_attachments"],
        "attachment_count": manifest["attachment_count"],
        "attachment_names": manifest["attachment_names"],
        "headers_json": json.dumps(extra, sort_keys=True),
        "parse_warnings": warnings,
        "token_estimate": estimate_tokens(f"{rendered_meta}\n{body}"),
    }


def ingest_zip(zip_path: Path) -> tuple[list[dict], list[str], list[dict]]:
    """-> (message rows, docids, failure items), in sorted-DocID order."""
    custodian = zip_custodian(zip_path.name)
    rows: list[dict] = []
    docnos: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        records, failures = manifest_messages(zf, zip_path.name)
        docids = [r["docid"] for r in records]
        if len(set(docids)) != len(docids):
            dupes = sorted({d for d in docids if docids.count(d) > 1})
            raise ValueError(f"duplicate DocIDs within {zip_path.name}: {dupes[:3]}")
        # The XML <Custodian> is a display string ("Jeff Skilling<jeff.…>"),
        # not the config id — the id comes from the zip name. The manifest
        # value is checked for consistency WITHIN the zip: one zip, one person.
        zip_display = records[0]["custodian"] if records else None
        for rec in sorted(records, key=lambda r: r["docid"]):
            try:
                if rec["native"] is None:
                    raise IngestError("manifest_no_native")
                if rec["custodian"] != zip_display:
                    raise IngestError("custodian_inconsistent")
                raw = zf.read(rec["native"])
                rows.append(
                    parse_eml(raw, source_file=f"{zip_path.name}/{rec['native']}",
                              custodian=custodian, manifest=rec)
                )
                docnos.append(rec["docid"])
            except Exception as e:  # itemized, gated on rate — never a crash
                failures.append(_failure(rec["docid"], zip_path.name, e))
    return rows, docnos, failures


def build_idmap(rows: list[dict], docids: list[str]) -> pa.Table:
    return pa.table(
        {
            "corpus": ["enron"] * len(rows),
            "doc_id": [r["doc_id"] for r in rows],
            "trec_doc_id": docids,
            "match_method": ["filename"] * len(rows),
            "confidence": [1.0] * len(rows),
            "ambiguous": [False] * len(rows),
        }
    )


def main(args) -> int:
    cfg = load_pipeline_config()
    corpus_cfg = load_corpus_config("enron")
    store_root = cfg.paths["store"]

    messages_path = table_path(store_root, "enron", "messages")
    idmap_path = table_path(store_root, "enron", "doc_id_map")
    report_path = cfg.paths["artifacts"] / "enron_ingest_report.json"
    if (messages_path.exists() and idmap_path.exists() and report_path.exists()
            and not args.force):
        # Resume must re-derive the gate from the persisted report — a run can
        # write tables AND fail the failure-rate gate, and a rerun must not
        # flip that to green.
        report = json.loads(report_path.read_text())
        ok = report["failure_rate"] < FAILURE_RATE_GATE
        print(f"ingest: already done ({report['messages']} messages, "
              f"failure rate {report['failure_rate']:.3%}, gate "
              f"{'green' if ok else 'FAILED'}); --force to redo")
        return 0 if ok else 1

    zip_entries = [f for f in corpus_cfg.files if f.dest.startswith("xml/")]
    tables: list[pa.Table] = []
    idmaps: list[pa.Table] = []
    failures: list[dict] = []
    per_custodian: dict[str, int] = {}
    seen_docids: set[str] = set()
    for entry in zip_entries:
        zip_path = cfg.paths["raw"] / "enron" / entry.dest
        if not zip_path.exists():
            print(f"ingest: {zip_path} missing; run `python -m pipeline acquire --corpus enron`",
                  file=sys.stderr)
            return 2
        rows, docids, zip_failures = ingest_zip(zip_path)
        dupes = seen_docids.intersection(docids)
        if dupes:
            raise ValueError(f"duplicate DocIDs across zips, e.g. {sorted(dupes)[:3]}")
        seen_docids.update(docids)
        failures += zip_failures
        if rows:
            tables.append(pa.Table.from_pylist(rows, schema=MESSAGES))
            idmaps.append(build_idmap(rows, docids))
            custodian = rows[0]["custodian"]
            per_custodian[custodian] = per_custodian.get(custodian, 0) + len(rows)
        print(f"  {zip_path.name}: {len(rows)} messages, {len(zip_failures)} failures")

    n_parsed = sum(t.num_rows for t in tables)
    n_total = n_parsed + len(failures)
    failure_rate = len(failures) / n_total if n_total else 1.0
    report = {
        "corpus": "enron",
        "messages": n_parsed,
        "failures": len(failures),
        "failure_rate": round(failure_rate, 6),
        "gate": FAILURE_RATE_GATE,
        "per_custodian": dict(sorted(per_custodian.items())),
        "failure_items": failures,
    }
    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    report_path = artifacts / "enron_ingest_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    if not tables:
        print("ingest: no messages parsed", file=sys.stderr)
        return 1
    write_table(store_root, "enron", "messages", pa.concat_tables(tables))
    write_table(store_root, "enron", "doc_id_map", pa.concat_tables(idmaps))
    print(f"ingest: {n_parsed} messages ({report['per_custodian']}) -> {messages_path}")
    print(f"  failures: {len(failures)} ({failure_rate:.3%}, gate <{FAILURE_RATE_GATE:.0%}) "
          f"-> {report_path}")
    return 0 if failure_rate < FAILURE_RATE_GATE else 1
