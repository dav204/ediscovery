"""Bush ingest: HiCAL athome4 sample tarball -> messages + doc_id_map tables.

Source: data/raw/bush/athome4_sample.tgz (sha256-pinned by acquire), 50,000
whole-document text files named by 6-digit athome4 doc number under a single
top-level directory. Documents are Outlook-style text exports: an optional
`Key:<tab>value` header block (From/Sent/To/Cc/Subject/Attachments), a blank
line, then the body. Quoted earlier messages stay in the body — whole-doc
review, no thread splitting (Bush is the validation layer; threading is P4).

Determinism: members are processed in sorted-name order, all derived fields are
pure functions of file bytes, and no timestamps are written — re-ingest is
byte-identical (P1 gate).

The idmap is trivial by construction (match_method=filename, confidence 1.0):
the athome4 doc number IS the file name, one file per document. Coverage of
judged docs is still computed and written to artifacts/bush_idmap_coverage.json
so the 100%-coverage gate is checked against data, not assumed.
"""

import json
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

from ..config import load_corpus_config, load_pipeline_config
from ..ids import doc_id as make_doc_id
from ..store import MESSAGES, read_table, table_path, write_table
from .normalize import (
    body_hash,
    estimate_tokens,
    normalize_body,
    normalize_subject,
)

TARBALL = "athome4_sample.tgz"

# Outlook text exports delimit header values with a TAB. Requiring it (or an
# empty value) is what keeps prose like "Comment: I oppose ..." at the top of a
# webform doc from being eaten as a header.
_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):(?:\t+(.*)|[ \t]*)$")
_ADDR_RE = re.compile(r"<([^<>]+)>")

# Naive Sent: timestamps are governor's-office local time; converted via
# America/New_York (DST-aware) to true UTC instants. date_raw keeps the original.
_LOCAL_TZ = ZoneInfo("America/New_York")

_DATE_FORMATS = (
    "%A, %B %d, %Y %I:%M %p",
    "%A, %B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %I:%M %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %I:%M %p",
)


def split_headers(text: str) -> tuple[dict[str, str], str, list[str]]:
    """Split a leading `Key:<tab>value` block from the body.

    Continuation lines (leading whitespace) fold into the previous value.
    A blank line ends the block; a non-header line before any header means the
    document has no header block at all (everything is body).
    """
    lines = text.split("\n")
    headers: dict[str, str] = {}
    warnings: list[str] = []
    last_key: str | None = None
    body_start = 0
    for i, line in enumerate(lines):
        if not line.strip():
            if headers:
                body_start = i + 1
                break
            body_start = i  # leading blank lines with no headers: all body
            break
        m = _HEADER_RE.match(line)
        if m:
            key, value = m.group(1), (m.group(2) or "").strip()
            if key in headers:
                headers[key] = f"{headers[key]}; {value}"
            else:
                headers[key] = value
            last_key = key
            body_start = i + 1
        elif line[0] in (" ", "\t") and last_key:
            headers[last_key] = f"{headers[last_key]} {line.strip()}"
            body_start = i + 1
        else:
            if headers:
                warnings.append("header_block_unterminated")
            body_start = i
            break
    if not headers:
        warnings.append("no_header_block")
        return {}, text, warnings
    return headers, "\n".join(lines[body_start:]), warnings


def parse_from(value: str) -> tuple[str | None, str | None]:
    """-> (from_name, from_addr) from `Name <addr>` / bare address / bare name."""
    m = _ADDR_RE.search(value)
    if m:
        name = _ADDR_RE.sub("", value).strip().strip('"').strip()
        return (name or None, m.group(1).strip())
    v = value.strip()
    if "@" in v and " " not in v:
        return None, v
    return (v or None), None


def parse_recipients(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def parse_sent(value: str) -> datetime | None:
    v = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    for fmt in _DATE_FORMATS:
        try:
            local = datetime.strptime(v, fmt).replace(tzinfo=_LOCAL_TZ)
            return local.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def decode_doc(raw: bytes) -> tuple[str, list[str]]:
    """utf-8, then cp1252 (Outlook-era smart quotes/dashes), then latin-1
    (never fails; maps cp1252's five undefined bytes to C1 controls)."""
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), ["decode_fallback_cp1252"]
    except UnicodeDecodeError:
        return raw.decode("latin-1"), ["decode_fallback_latin1"]


def parse_doc(member_name: str, raw: bytes) -> dict:
    """One tar member -> one messages-table row (dict keyed by schema column)."""
    text, warnings = decode_doc(raw)
    headers, body, header_warnings = split_headers(text)
    warnings += header_warnings

    from_name = from_addr = None
    if "From" in headers:
        from_name, from_addr = parse_from(headers["From"])

    date_raw = headers.get("Sent") or headers.get("Date")
    date_utc = None
    if date_raw:
        date_utc = parse_sent(date_raw)
        if date_utc is None:
            warnings.append("date_unparsed")

    subject = headers.get("Subject")
    attachment_names = parse_recipients(headers.get("Attachments", ""))
    body_text = body
    norm = normalize_body(body_text)

    source_file = f"{TARBALL}/{member_name}"
    return {
        "doc_id": make_doc_id("bush", source_file, 0),
        "corpus": "bush",
        "custodian": None,
        "source_file": source_file,
        "source_index": 0,
        "message_id_hdr": None,
        "in_reply_to": None,
        "references": [],
        "from_addr": from_addr,
        "from_name": from_name,
        "to": parse_recipients(headers.get("To", "")),
        "cc": parse_recipients(headers.get("Cc", "")),
        "bcc": parse_recipients(headers.get("Bcc", "")),
        "subject": subject,
        "subject_norm": normalize_subject(subject),
        "date_utc": date_utc,
        "date_raw": date_raw,
        "body_text": body_text,
        "body_norm": norm,
        "body_hash": body_hash(norm),
        "has_attachments": bool(attachment_names),
        "attachment_count": len(attachment_names),
        "attachment_names": attachment_names,
        "headers_json": json.dumps(headers, sort_keys=True),
        "parse_warnings": warnings,
        "token_estimate": estimate_tokens(text),
    }


def ingest_tarball(tgz_path: Path, expected_count: int | None) -> tuple[list[dict], list[str]]:
    """-> (message rows, athome doc numbers), both in sorted member-name order."""
    # Stream in archive order (random access into a .tgz re-decompresses from
    # the start each seek), then sort rows by member name for determinism.
    by_name: dict[str, dict] = {}
    with tarfile.open(tgz_path, "r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            docno = Path(member.name).name
            if not docno.isdigit():
                raise ValueError(
                    f"{tgz_path.name}: member {member.name!r} is not a numeric doc id"
                )
            raw = tar.extractfile(member).read()
            by_name[member.name] = parse_doc(member.name, raw)
    rows = [by_name[name] for name in sorted(by_name)]
    docnos = [Path(name).name for name in sorted(by_name)]
    dupes = {d for d in docnos if docnos.count(d) > 1} if len(set(docnos)) != len(docnos) else set()
    if dupes:
        raise ValueError(
            f"{tgz_path.name}: duplicate doc numbers across members: {sorted(dupes)[:3]}"
        )
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(
            f"{tgz_path.name}: {len(rows)} documents, expected {expected_count}"
        )
    return rows, docnos


def build_idmap(rows: list[dict], docnos: list[str]) -> pa.Table:
    return pa.table(
        {
            "corpus": ["bush"] * len(rows),
            "doc_id": [r["doc_id"] for r in rows],
            "trec_doc_id": docnos,
            "match_method": ["filename"] * len(rows),
            "confidence": [1.0] * len(rows),
            "ambiguous": [False] * len(rows),
        }
    )


def coverage_report(qrels: pa.Table, mapped_docnos: set[str]) -> dict:
    per_topic: dict[str, dict] = {}
    judged: dict[str, set[str]] = {}
    for row in qrels.to_pylist():
        judged.setdefault(row["topic"], set()).add(row["trec_doc_id"])
    total_judged = total_mapped = 0
    for topic in sorted(judged):
        docs = judged[topic]
        mapped = len(docs & mapped_docnos)
        per_topic[topic] = {
            "judged": len(docs),
            "mapped": mapped,
            "coverage": round(mapped / len(docs), 6),
        }
        total_judged += len(docs)
        total_mapped += mapped
    return {
        "corpus": "bush",
        "match_method": "filename",
        "topics": per_topic,
        "overall": {
            "judged": total_judged,
            "mapped": total_mapped,
            "coverage": round(total_mapped / total_judged, 6) if total_judged else 0.0,
        },
    }


def rows_to_messages_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=MESSAGES)


def main(args) -> int:
    if args.corpus != "bush":
        print("ingest: only --corpus bush is implemented (enron is P4)", file=sys.stderr)
        return 2
    cfg = load_pipeline_config()
    corpus_cfg = load_corpus_config(args.corpus)
    store_root = cfg.paths["store"]

    qrels_path = table_path(store_root, "bush", "qrels_raw")
    if not qrels_path.exists():
        print("ingest: qrels_raw missing; run `python -m pipeline qrels --corpus bush` "
              "first (coverage gate needs it)", file=sys.stderr)
        return 2

    messages_path = table_path(store_root, "bush", "messages")
    idmap_path = table_path(store_root, "bush", "doc_id_map")
    if messages_path.exists() and idmap_path.exists() and not args.force:
        messages = read_table(store_root, "bush", "messages")
        idmap = read_table(store_root, "bush", "doc_id_map")
        print(f"ingest: already done ({messages.num_rows} messages); --force to redo")
    else:
        tgz = cfg.paths["raw"] / "bush" / TARBALL
        if not tgz.exists():
            print(f"ingest: {tgz} missing; run `python -m pipeline acquire --corpus bush`",
                  file=sys.stderr)
            return 2
        rows, docnos = ingest_tarball(tgz, corpus_cfg.expected_message_count)
        messages = rows_to_messages_table(rows)
        idmap = build_idmap(rows, docnos)
        write_table(store_root, "bush", "messages", messages)
        write_table(store_root, "bush", "doc_id_map", idmap)
        n_warn = sum(1 for r in rows if r["parse_warnings"])
        print(f"ingest: {messages.num_rows} messages -> {messages_path}")
        print(f"  docs with parse warnings: {n_warn}")

    qrels = read_table(store_root, "bush", "qrels_raw")
    mapped = set(idmap.column("trec_doc_id").to_pylist())
    report = coverage_report(qrels, mapped)
    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    out = artifacts / "bush_idmap_coverage.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"idmap coverage: {report['overall']['coverage']:.1%} "
          f"({report['overall']['mapped']}/{report['overall']['judged']} judged docs) -> {out}")
    return 0 if report["overall"]["coverage"] == 1.0 else 1
