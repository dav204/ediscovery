"""Enron judged-sample ingest: official TREC 2010 Learning-task text -> judged_messages.

Scoring-universe decision (Dan, 2026-09-30; docs/PLAN.md P5): Enron topics are
scored over the FULL TREC judged sample, reviewed from the official
participant text distribution (`trec/edrmv2txt-v2.tar.bz2`, sha-pinned), not
over the custodian-scoped XML ingest. Attachment parts (`<parent>.N`) are
reviewed as their own documents, exactly as the Learning task defined them, so
no fold-to-parent policy enters scoring. The custodian store (messages /
doc_id_map) stays the P6/P7 showcase set.

Tarball layout (`<custodian zip>/text_NNN/<docid>.txt`, see
qrels/enron.py:full_custodian_map): one member per canonical document. The
stream is read once in archive order (never random access — see the CLAUDE.md
.tgz gotcha) and only members whose docid is judged for a chosen topic are
kept.

Rendition format: the text files are the participant renditions; a leading
RFC-822-style header block (From/To/Cc/Subject/Date) is parsed into metadata
when one is present, and otherwise the whole file is the body. Bodies longer
than ingest.judged_text_max_body_chars are truncated with a
`truncated_body` parse warning (attachment parts include large spreadsheets).

Output: judged_messages + judged_doc_id_map (same schemas as messages /
doc_id_map, separate tables so the two universes never share an idmap), and
artifacts/enron_judged_text_report.json (counts and missing DOCIDS only —
never content). Coverage gate: every judged docid for the chosen topics must
be found; main() exits 1 otherwise.
"""

import json
import re
import sys
import tarfile
from pathlib import Path

import pyarrow as pa

from ..config import load_corpus_config, load_pipeline_config
from ..ids import doc_id as make_doc_id
from ..qrels.enron import TEXT_TARBALL, is_part, parent_docid
from ..store import MESSAGES, read_table, table_path, write_table
from .normalize import body_hash, estimate_tokens, normalize_body, normalize_subject

DEFAULT_MAX_BODY_CHARS = 60000
_TAR_ZIP_RE = re.compile(r"edrm-enron-v2_(?P<custodian>.+?)_xml")
_HEADER_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*):[ \t]?(?P<value>.*)$")
_KNOWN_HEADERS = {"from", "to", "cc", "bcc", "subject", "date", "sent"}
_ANCHOR_HEADERS = {"from", "subject", "to"}


def member_docid(name: str) -> tuple[str, str, str] | None:
    """Tarball member path -> (normalized path, custodian, docid), or None for
    non-text members. Same path contract as qrels/enron.full_custodian_map."""
    if not name.endswith(".txt"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if len(parts) != 3:
        raise ValueError(f"unexpected tarball member path {name!r}")
    m = _TAR_ZIP_RE.search(parts[0])
    if not m:
        raise ValueError(f"unexpected tarball member path {name!r}")
    return "/".join(parts), m.group("custodian"), parts[2][: -len(".txt")]


def split_headers(text: str) -> tuple[dict[str, str], str]:
    """Leading header block -> ({lowercased key: value}, remaining body).

    A block counts as headers only when every line up to the first blank line
    is `Key: value` (or a folded continuation), at least one key is a
    From/To/Subject anchor, and a blank line terminates it — so an attachment
    part that merely opens with "Note: ..." keeps its full text as body."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headers: dict[str, str] = {}
    last_key = None
    for i, line in enumerate(lines):
        if line.strip() == "":
            if headers and _ANCHOR_HEADERS & set(headers):
                return headers, "\n".join(lines[i + 1:])
            return {}, text
        if line[:1] in (" ", "\t") and last_key:
            headers[last_key] += " " + line.strip()
            continue
        m = _HEADER_RE.match(line)
        if not m:
            return {}, text
        last_key = m.group("key").lower()
        headers.setdefault(last_key, m.group("value").strip())
    return {}, text  # never terminated: not a header block


def _addr_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [a.strip() for a in re.split(r"[;,]", value) if a.strip()]


def build_row(raw: bytes, *, source_file: str, custodian: str, docid: str,
              max_body_chars: int) -> dict:
    warnings: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        warnings.append("latin1_fallback")
    headers, body = split_headers(text)
    if not headers:
        warnings.append("no_header_block")
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
        warnings.append("truncated_body")
    subject = headers.get("subject")
    date_raw = headers.get("date") or headers.get("sent")
    to_list, cc_list = _addr_list(headers.get("to")), _addr_list(headers.get("cc"))
    norm = normalize_body(body)
    part = is_part(docid)
    rendered_meta = " ".join(filter(None, [
        custodian, headers.get("from") or "", "; ".join(to_list), "; ".join(cc_list),
        date_raw or "", subject or "",
    ]))
    return {
        "doc_id": make_doc_id("enron", source_file, 0),
        "corpus": "enron",
        "custodian": custodian,
        "source_file": source_file,
        "source_index": 0,
        "message_id_hdr": None,
        "in_reply_to": None,
        "references": [],
        "from_addr": headers.get("from"),
        "from_name": None,
        "to": to_list,
        "cc": cc_list,
        "bcc": _addr_list(headers.get("bcc")),
        "subject": subject,
        "subject_norm": normalize_subject(subject),
        "date_utc": None,
        "date_raw": date_raw,
        "body_text": body,
        "body_norm": norm,
        "body_hash": body_hash(norm),
        "has_attachments": False,
        "attachment_count": 0,
        "attachment_names": [],
        "headers_json": json.dumps(
            {"trec_doc_id": docid, "is_part": part,
             "parent_trec_doc_id": parent_docid(docid) if part else None},
            sort_keys=True,
        ),
        "parse_warnings": warnings,
        "token_estimate": estimate_tokens(f"{rendered_meta}\n{body}"),
    }


def ingest_tarball(tarball: Path, wanted: set[str], max_body_chars: int
                   ) -> tuple[list[dict], list[str]]:
    """Single streaming pass -> (rows, docids) for the wanted docids, sorted by docid."""
    found: dict[str, dict] = {}
    with tarfile.open(tarball, "r|bz2") as tar:
        for member in tar:
            parsed = member_docid(member.name)
            if parsed is None:
                continue
            path, custodian, docid = parsed
            if docid not in wanted:
                continue
            if docid in found:
                raise ValueError(f"duplicate docid {docid!r} in {tarball.name}")
            f = tar.extractfile(member)
            if f is None:
                raise ValueError(f"tarball member for {docid!r} is not a regular file")
            found[docid] = build_row(
                f.read(), source_file=f"{TEXT_TARBALL}/{path}", custodian=custodian,
                docid=docid, max_body_chars=max_body_chars,
            )
    docids = sorted(found)
    return [found[d] for d in docids], docids


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
    report_path = cfg.paths["artifacts"] / "enron_judged_text_report.json"
    messages_path = table_path(store_root, "enron", "judged_messages")
    if messages_path.exists() and report_path.exists() and not args.force:
        report = json.loads(report_path.read_text())
        ok = report["missing"] == 0
        print(f"ingest judged-text: already done ({report['found']}/{report['judged']} "
              f"judged docs, gate {'green' if ok else 'FAILED'}); --force to redo")
        return 0 if ok else 1

    if not table_path(store_root, "enron", "qrels_raw").exists():
        print("ingest judged-text: enron qrels_raw missing; run `python -m pipeline "
              "qrels --corpus enron` first", file=sys.stderr)
        return 2
    tarball = cfg.paths["raw"] / "enron" / TEXT_TARBALL
    if not tarball.exists():
        print(f"ingest judged-text: {tarball} missing; run `python -m pipeline acquire "
              "--corpus enron`", file=sys.stderr)
        return 2

    topics = [str(t) for t in corpus_cfg.chosen_topics]
    qrels = read_table(store_root, "enron", "qrels_raw").to_pylist()
    judged_by_topic = {
        t: sorted({r["trec_doc_id"] for r in qrels if str(r["topic"]) == t}) for t in topics
    }
    wanted = set().union(*judged_by_topic.values()) if judged_by_topic else set()
    if not wanted:
        print(f"ingest judged-text: no judged docs for chosen_topics {topics}",
              file=sys.stderr)
        return 2

    max_chars = int(cfg.ingest.get("judged_text_max_body_chars", DEFAULT_MAX_BODY_CHARS))
    rows, docids = ingest_tarball(tarball, wanted, max_chars)
    found = set(docids)
    missing = sorted(wanted - found)
    report = {
        "corpus": "enron",
        "source": TEXT_TARBALL,
        "topics": {
            t: {"judged": len(ids), "found": sum(1 for d in ids if d in found),
                "parts": sum(1 for d in ids if is_part(d))}
            for t, ids in judged_by_topic.items()
        },
        "judged": len(wanted),
        "found": len(found),
        "missing": len(missing),
        "missing_docids": missing,
        "truncated_body": sum("truncated_body" in r["parse_warnings"] for r in rows),
        "no_header_block": sum("no_header_block" in r["parse_warnings"] for r in rows),
        "max_body_chars": max_chars,
        "token_estimate_total": sum(r["token_estimate"] for r in rows),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    if rows:
        write_table(store_root, "enron", "judged_messages",
                    pa.Table.from_pylist(rows, schema=MESSAGES))
        write_table(store_root, "enron", "judged_doc_id_map", build_idmap(rows, docids))
    print(f"ingest judged-text: {len(found)}/{len(wanted)} judged docs "
          f"(topics {topics}), {report['truncated_body']} truncated, "
          f"~{report['token_estimate_total']:,} tokens -> {messages_path}")
    if missing:
        print(f"  COVERAGE GATE FAILED: {len(missing)} judged docids not in the tarball "
              f"-> {report_path}", file=sys.stderr)
        return 1
    return 0
