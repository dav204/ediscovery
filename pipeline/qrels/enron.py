"""TREC 2010 Legal Learning-task qrels -> qrels_raw + idmap coverage policies.

Format (defined by the NIST eval toolkit, data/raw/enron/trec/eval/): each
line is `<topic>:<docid> <stratum> <rel>` covering the full candidate universe
per topic, where stratum is the sampling batch label (100 / 1000 / 10000 /
1000000) and rel is -1 (unjudged), 0, or 1. The official estimator (calc1.c)
scales each (topic, stratum) cell's judged-relevant fraction by the cell's
total size — equivalently, each judged doc carries a Horvitz-Thompson
sampling_weight of cell_total / cell_judged. Only judged rows land in
qrels_raw; unjudged rows contribute to the weights and are dropped.

Validation: this parser's estimated-R reproduces calc1.c's published comment
values exactly for topics 200/204/207; the other topics differ (the comment
appears to predate the final post-adjudication qrels — the artifact records
both numbers so the discrepancy is visible, and OUR metrics always derive
from the qrels file itself).

Attachment parts: message docids are `3.<num>.<HASH>`; judged part docids
append `.N`. The idmap is message-level by design (no attachment-content
review), so judged-doc coverage is reported under three policies — raw
(parts can never match), fold_to_parent (a part folds to its message),
exclude_parts — feeding the recorded P5 decision in docs/PLAN.md.
"""

import gzip
import json
import re
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

import pyarrow as pa

from ..config import load_pipeline_config
from ..store import read_table, table_path, write_table

QREL_FILE = "trec/qrels.t10legallearn.gz"
TEXT_TARBALL = "trec/edrmv2txt-v2.tar.bz2"
STRATA = ("100", "1000", "10000", "1000000")


def parent_docid(docid: str) -> str:
    return ".".join(docid.split(".")[:3])


def is_part(docid: str) -> bool:
    return len(docid.split(".")) > 3


def parse_qrels(path: Path) -> tuple[list[dict], dict]:
    """Single streaming pass -> (judged rows without weights, cell counts).

    cells[(topic, stratum)] = {"n": total incl. unjudged, "judged":, "rel": }.
    """
    rows: list[dict] = []
    cells: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"n": 0, "judged": 0, "rel": 0}
    )
    with gzip.open(path, "rt") as f:
        for line in f:
            fields = line.split()
            if len(fields) != 3:
                raise ValueError(f"malformed qrels line: {line!r}")
            topic_doc, stratum, rel = fields
            topic, docid = topic_doc.split(":", 1)
            if stratum not in STRATA:
                raise ValueError(f"unknown stratum {stratum!r} in line {line!r}")
            cell = cells[(topic, stratum)]
            cell["n"] += 1
            if rel == "-1":
                continue
            if rel not in ("0", "1"):
                raise ValueError(f"unknown relevance {rel!r} in line {line!r}")
            cell["judged"] += 1
            cell["rel"] += int(rel)
            rows.append(
                {"topic": topic, "trec_doc_id": docid, "relevance": int(rel),
                 "stratum": stratum}
            )
    return rows, dict(cells)


def build_table(rows: list[dict], cells: dict) -> pa.Table:
    weights = {
        key: cell["n"] / cell["judged"]
        for key, cell in cells.items() if cell["judged"]
    }
    rows = sorted(rows, key=lambda r: (r["topic"], r["trec_doc_id"]))
    return pa.table(
        {
            "corpus": ["enron"] * len(rows),
            "topic": [r["topic"] for r in rows],
            "trec_doc_id": [r["trec_doc_id"] for r in rows],
            "relevance": [r["relevance"] for r in rows],
            "stratum": [r["stratum"] for r in rows],
            "sampling_weight": [
                round(weights[(r["topic"], r["stratum"])], 6) for r in rows
            ],
        }
    )


def estimated_r(cells: dict, topic: str) -> float:
    total = 0.0
    for stratum in STRATA:
        cell = cells.get((topic, stratum))
        if cell and cell["judged"]:
            total += cell["rel"] / cell["judged"] * cell["n"]
    return round(total, 2)


def coverage_policies(rows: list[dict], mapped: set[str]) -> dict:
    """rows: judged rows for ONE topic. mapped: message-level trec docids in
    the idmap. -> {policy: {judged, covered, coverage, relevant, covered_rel,
    coverage_rel}}."""
    out = {}
    for policy in ("raw", "fold_to_parent", "exclude_parts"):
        if policy == "exclude_parts":
            in_scope = [r for r in rows if not is_part(r["trec_doc_id"])]
        else:
            in_scope = rows
        if policy == "fold_to_parent":
            covered = [r for r in in_scope if parent_docid(r["trec_doc_id"]) in mapped]
        else:
            covered = [r for r in in_scope if r["trec_doc_id"] in mapped]
        rel = [r for r in in_scope if r["relevance"] == 1]
        covered_rel = [r for r in covered if r["relevance"] == 1]
        out[policy] = {
            "judged": len(in_scope),
            "covered": len(covered),
            "coverage": round(len(covered) / len(in_scope), 6) if in_scope else 0.0,
            "relevant": len(rel),
            "covered_rel": len(covered_rel),
            "coverage_rel": round(len(covered_rel) / len(rel), 6) if rel else 0.0,
        }
    return out


def per_custodian(rows: list[dict], docid_to_custodian: dict[str, str]) -> dict:
    """Fold-policy custodian breakdown of covered judged docs for one topic."""
    counts: dict[str, dict] = defaultdict(lambda: {"judged": 0, "relevant": 0})
    for r in rows:
        custodian = docid_to_custodian.get(parent_docid(r["trec_doc_id"]))
        if custodian:
            counts[custodian]["judged"] += 1
            counts[custodian]["relevant"] += r["relevance"]
    return {k: v for k, v in sorted(counts.items())}


_TAR_ZIP_RE = re.compile(r"edrm-enron-v2_(?P<custodian>.+?)_xml")


def full_custodian_map(tarball: Path, cache: Path) -> dict[str, str]:
    """docid -> custodian for the ENTIRE corpus, from the official text
    distribution's member paths (`<custodian zip>/text_NNN/<docid>.txt`).
    The listing pass decompresses the whole .bz2 (~1 min), so the result is
    cached under data/store (never committed)."""
    if cache.exists():
        return json.loads(cache.read_text())
    mapping: dict[str, str] = {}
    with tarfile.open(tarball, "r|bz2") as tar:
        for member in tar:
            if not member.name.endswith(".txt"):
                continue
            zip_name, _, fname = member.name.split("/")
            m = _TAR_ZIP_RE.search(zip_name)
            if not m:
                raise ValueError(f"unexpected tarball member path {member.name!r}")
            mapping[fname[: -len(".txt")]] = m.group("custodian")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping))
    return mapping


def relevant_by_custodian(rows: list[dict], full_map: dict[str, str],
                          top_n: int = 15) -> dict:
    """Corpus-wide ranking (counts only) of which custodians hold a topic's
    relevant judged docs — the custodian-swap decision input."""
    counts: dict[str, int] = defaultdict(int)
    unmapped = 0
    for r in rows:
        if r["relevance"] != 1:
            continue
        custodian = full_map.get(r["trec_doc_id"])
        if custodian is None:
            unmapped += 1
        else:
            counts[custodian] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "top": {c: n for c, n in ranked[:top_n]},
        "custodians_with_relevant": len(counts),
        "unmapped_relevant": unmapped,
    }


# calc1.c's published estimates (comment block) — recorded for the visible
# cross-check; 200/204/207 reproduce exactly from the qrels file.
CALC1_PUBLISHED_R = {
    "200": 2543.52, "201": 2366.28, "202": 4615.27, "203": 4944.23,
    "204": 6361.83, "205": 67438.43, "206": 929.09, "207": 20929.17,
}


def main(args) -> int:
    cfg = load_pipeline_config()
    store_root = cfg.paths["store"]
    qrel_path = cfg.paths["raw"] / "enron" / QREL_FILE
    if not qrel_path.exists():
        print(f"qrels: {qrel_path} missing; run `python -m pipeline acquire --corpus enron`",
              file=sys.stderr)
        return 2
    if not table_path(store_root, "enron", "doc_id_map").exists():
        print("qrels: enron doc_id_map missing; run `python -m pipeline ingest "
              "--corpus enron` first", file=sys.stderr)
        return 2

    rows, cells = parse_qrels(qrel_path)
    table = build_table(rows, cells)
    path = write_table(store_root, "enron", "qrels_raw", table)
    topics = sorted({r["topic"] for r in rows})
    print(f"qrels_raw: {table.num_rows} judged rows, {len(topics)} topics -> {path}")

    idmap = read_table(store_root, "enron", "doc_id_map").to_pylist()
    mapped = {r["trec_doc_id"] for r in idmap}
    doc_id_by_trec = {r["trec_doc_id"]: r["doc_id"] for r in idmap}
    custodians = dict(
        zip(*[read_table(store_root, "enron", "messages").select(
            ["doc_id", "custodian"]).column(c).to_pylist()
            for c in ("doc_id", "custodian")])
    )
    docid_to_custodian = {
        trec: custodians[doc_id] for trec, doc_id in doc_id_by_trec.items()
    }

    tarball = cfg.paths["raw"] / "enron" / TEXT_TARBALL
    full_map: dict[str, str] = {}
    if tarball.exists():
        full_map = full_custodian_map(
            tarball, cfg.paths["store"] / "enron" / "docid_custodian.json"
        )

    by_topic: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_topic[r["topic"]].append(r)
    report: dict = {"corpus": "enron", "topics": {}, "notes": {
        "policies": "raw = message docids only; fold_to_parent = part docids "
                    "fold to their message; exclude_parts = part docids dropped. "
                    "Decision recorded in docs/PLAN.md P5 — pick before any "
                    "token spend.",
        "estimated_r": "cell-weighted estimate from THIS qrels file; "
                       "calc1_published is the toolkit's comment value.",
    }}
    for topic in topics:
        trows = by_topic[topic]
        report["topics"][topic] = {
            "judged": len(trows),
            "relevant": sum(r["relevance"] for r in trows),
            "estimated_r": estimated_r(cells, topic),
            "calc1_published_r": CALC1_PUBLISHED_R.get(topic),
            "policies": coverage_policies(trows, mapped),
            "per_custodian_fold": per_custodian(trows, docid_to_custodian),
        }
        if full_map:
            report["topics"][topic]["relevant_by_custodian_full"] = (
                relevant_by_custodian(trows, full_map)
            )
        p = report["topics"][topic]["policies"]
        print(f"  {topic}: judged={len(trows)} rel={report['topics'][topic]['relevant']} "
              f"estR={report['topics'][topic]['estimated_r']} | covered "
              f"raw={p['raw']['covered']} fold={p['fold_to_parent']['covered']} "
              f"excl={p['exclude_parts']['covered']} | rel covered "
              f"fold={p['fold_to_parent']['covered_rel']}/{p['fold_to_parent']['relevant']}")

    artifacts = cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    out = artifacts / "enron_idmap_coverage.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"coverage policies -> {out}")
    return 0
