"""TREC 2010 Learning qrels parsing + coverage policies (synthetic, phase5)."""

import gzip
import json

import pytest

from pipeline.qrels.enron import (
    build_table,
    coverage_policies,
    estimated_r,
    is_part,
    parent_docid,
    parse_qrels,
    per_custodian,
)
from pipeline.store import QRELS_RAW

pytestmark = pytest.mark.phase5

LINES = [
    # topic 201, stratum 100: 2 of 2 judged, 1 relevant
    "201:3.11.AAA 100 1",
    "201:3.12.BBB 100 0",
    # stratum 1000: 4 docs, 2 judged, 1 relevant (an attachment part)
    "201:3.13.CCC.1 1000 1",
    "201:3.14.DDD 1000 0",
    "201:3.15.EEE 1000 -1",
    "201:3.16.FFF 1000 -1",
    # topic 202 keeps topics separated
    "202:3.11.AAA 100 0",
]


def write_qrels(tmp_path, lines=LINES):
    path = tmp_path / "qrels.gz"
    with gzip.open(path, "wt") as f:
        f.write("\n".join(lines) + "\n")
    return path


def test_parent_and_part_detection():
    assert not is_part("3.157297.OZ4QK4")
    assert is_part("3.157297.OZ4QK4.1")
    assert parent_docid("3.157297.OZ4QK4.1") == "3.157297.OZ4QK4"
    assert parent_docid("3.157297.OZ4QK4.10") == "3.157297.OZ4QK4"
    assert parent_docid("3.157297.OZ4QK4") == "3.157297.OZ4QK4"


def test_parse_qrels_cells_and_rows(tmp_path):
    rows, cells = parse_qrels(write_qrels(tmp_path))
    assert len(rows) == 5  # -1 rows dropped
    assert cells[("201", "100")] == {"n": 2, "judged": 2, "rel": 1}
    assert cells[("201", "1000")] == {"n": 4, "judged": 2, "rel": 1}
    assert cells[("202", "100")] == {"n": 1, "judged": 1, "rel": 0}


def test_parse_qrels_rejects_malformed(tmp_path):
    with pytest.raises(ValueError, match="malformed"):
        parse_qrels(write_qrels(tmp_path, ["201:3.11.AAA 100"]))
    with pytest.raises(ValueError, match="unknown stratum"):
        parse_qrels(write_qrels(tmp_path, ["201:3.11.AAA 555 1"]))
    with pytest.raises(ValueError, match="unknown relevance"):
        parse_qrels(write_qrels(tmp_path, ["201:3.11.AAA 100 7"]))


def test_build_table_weights_and_schema(tmp_path):
    rows, cells = parse_qrels(write_qrels(tmp_path))
    table = build_table(rows, cells).cast(QRELS_RAW)  # assert on the CAST table
    recs = {(r["topic"], r["trec_doc_id"]): r for r in table.to_pylist()}
    # stratum 100: n=2 judged=2 -> weight 1.0; stratum 1000: n=4 judged=2 -> 2.0
    assert recs[("201", "3.11.AAA")]["sampling_weight"] == 1.0
    assert recs[("201", "3.13.CCC.1")]["sampling_weight"] == 2.0
    assert recs[("201", "3.13.CCC.1")]["stratum"] == "1000"


def test_estimated_r_hand_math(tmp_path):
    _rows, cells = parse_qrels(write_qrels(tmp_path))
    # 100: 1/2 * 2 = 1.0 ; 1000: 1/2 * 4 = 2.0
    assert estimated_r(cells, "201") == 3.0
    assert estimated_r(cells, "202") == 0.0


def test_coverage_policies_math():
    rows = [
        {"trec_doc_id": "3.11.AAA", "relevance": 1},     # mapped message
        {"trec_doc_id": "3.99.ZZZ", "relevance": 0},     # unmapped message
        {"trec_doc_id": "3.11.AAA.1", "relevance": 1},   # part of mapped message
        {"trec_doc_id": "3.98.YYY.2", "relevance": 1},   # part of unmapped message
    ]
    mapped = {"3.11.AAA"}
    p = coverage_policies(rows, mapped)
    assert p["raw"] == {"judged": 4, "covered": 1, "coverage": 0.25,
                        "relevant": 3, "covered_rel": 1, "coverage_rel": round(1 / 3, 6)}
    assert p["fold_to_parent"]["covered"] == 2          # message + its part
    assert p["fold_to_parent"]["covered_rel"] == 2
    assert p["exclude_parts"] == {"judged": 2, "covered": 1, "coverage": 0.5,
                                  "relevant": 1, "covered_rel": 1, "coverage_rel": 1.0}


def test_per_custodian_folds_parts():
    rows = [
        {"trec_doc_id": "3.11.AAA", "relevance": 1},
        {"trec_doc_id": "3.11.AAA.1", "relevance": 0},
        {"trec_doc_id": "3.22.BBB", "relevance": 0},
        {"trec_doc_id": "3.99.ZZZ", "relevance": 1},    # not ours
    ]
    mapping = {"3.11.AAA": "skilling-j", "3.22.BBB": "lay-k"}
    assert per_custodian(rows, mapping) == {
        "lay-k": {"judged": 1, "relevant": 0},
        "skilling-j": {"judged": 2, "relevant": 1},
    }


def make_text_tarball(path, docid_by_custodian):
    import io
    import tarfile

    with tarfile.open(path, "w:bz2") as tar:
        for custodian, docids in docid_by_custodian.items():
            for docid in docids:
                name = f"edrm-enron-v2_{custodian}_xml.zip/text_000/{docid}.txt"
                data = b"synthetic rendition\n"
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def test_full_custodian_map_caches_bound_to_tarball_size(tmp_path):
    from pipeline.qrels.enron import full_custodian_map

    tarball = make_text_tarball(
        tmp_path / "t.tar.bz2",
        {"shackleton-s": ["3.11.AAA", "3.11.AAA.1"], "kaminski-v": ["3.22.BBB"]},
    )
    cache = tmp_path / "cache" / "docid_custodian.json"
    mapping = full_custodian_map(tarball, cache)
    assert mapping == {"3.11.AAA": "shackleton-s", "3.11.AAA.1": "shackleton-s",
                       "3.22.BBB": "kaminski-v"}
    # unchanged tarball -> cache hit (poison the cache to prove it's read)
    poisoned = json.loads(cache.read_text())
    poisoned["mapping"]["3.99.ZZZ"] = "sentinel"
    cache.write_text(json.dumps(poisoned))
    assert "3.99.ZZZ" in full_custodian_map(tarball, cache)
    # replaced tarball (different size) -> cache invalidated, map rebuilt
    make_text_tarball(tmp_path / "t.tar.bz2", {"jones-t": ["3.33.CCC"]})
    assert full_custodian_map(tmp_path / "t.tar.bz2", cache) == {"3.33.CCC": "jones-t"}


def test_full_custodian_map_normalizes_dot_prefix_and_rejects_bad_depth(tmp_path):
    import io
    import tarfile as tarmod

    from pipeline.qrels.enron import full_custodian_map

    def raw_tarball(path, names):
        with tarmod.open(path, "w:bz2") as tar:
            for name in names:
                data = b"x"
                info = tarmod.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    ok = raw_tarball(tmp_path / "ok.tar.bz2",
                     ["./edrm-enron-v2_jones-t_xml.zip/text_000/3.33.CCC.txt"])
    assert full_custodian_map(ok, tmp_path / "c1.json") == {"3.33.CCC": "jones-t"}

    bad = raw_tarball(tmp_path / "bad.tar.bz2",
                      ["edrm-enron-v2_jones-t_xml.zip/extra/depth/3.33.CCC.txt"])
    with pytest.raises(ValueError, match="unexpected tarball member path"):
        full_custodian_map(bad, tmp_path / "c2.json")


def test_relevant_by_custodian_ranks_and_counts_unmapped():
    from pipeline.qrels.enron import relevant_by_custodian

    rows = [
        {"trec_doc_id": "3.11.AAA", "relevance": 1},
        {"trec_doc_id": "3.11.AAA.1", "relevance": 1},
        {"trec_doc_id": "3.22.BBB", "relevance": 1},
        {"trec_doc_id": "3.22.BBB", "relevance": 0},   # nonrelevant ignored
        {"trec_doc_id": "3.99.ZZZ", "relevance": 1},   # not in map
    ]
    full_map = {"3.11.AAA": "shackleton-s", "3.11.AAA.1": "shackleton-s",
                "3.22.BBB": "kaminski-v"}
    out = relevant_by_custodian(rows, full_map)
    assert out["top"] == {"shackleton-s": 2, "kaminski-v": 1}
    assert out["custodians_with_relevant"] == 2
    assert out["unmapped_relevant"] == 1


def test_main_writes_artifact(tmp_path, monkeypatch, capsys):
    from argparse import Namespace

    import pyarrow as pa

    import pipeline.qrels.enron as enron_mod
    from pipeline.config import PipelineConfig
    from pipeline.store import DOC_ID_MAP, MESSAGES, write_table

    raw = tmp_path / "raw"
    (raw / "enron" / "trec").mkdir(parents=True)
    with gzip.open(raw / "enron" / "trec" / "qrels.t10legallearn.gz", "wt") as f:
        f.write("\n".join(LINES) + "\n")
    paths = {
        "raw": raw, "store": tmp_path / "store", "artifacts": tmp_path / "artifacts",
        "batches": tmp_path / "b", "decisions": tmp_path / "d",
        "spend": tmp_path / "s", "productions": tmp_path / "p",
    }
    cfg = PipelineConfig(paths=paths, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    monkeypatch.setattr(enron_mod, "load_pipeline_config", lambda: cfg)

    idmap = pa.table(
        {
            "corpus": ["enron"], "doc_id": ["enron-aaaa"], "trec_doc_id": ["3.11.AAA"],
            "match_method": ["filename"], "confidence": [1.0], "ambiguous": [False],
        }
    ).cast(DOC_ID_MAP)
    write_table(paths["store"], "enron", "doc_id_map", idmap)
    msg = {c.name: None for c in MESSAGES} | {
        "doc_id": "enron-aaaa", "corpus": "enron", "custodian": "skilling-j",
        "source_file": "z", "source_index": 0, "references": [], "to": [], "cc": [],
        "bcc": [], "attachment_names": [], "parse_warnings": [], "body_text": "x",
        "body_norm": "x", "body_hash": "h", "has_attachments": False,
        "attachment_count": 0, "token_estimate": 1,
    }
    write_table(paths["store"], "enron", "messages",
                pa.Table.from_pylist([msg], schema=MESSAGES))

    make_text_tarball(
        raw / "enron" / "trec" / "edrmv2txt-v2.tar.bz2",
        {"skilling-j": ["3.11.AAA"], "shackleton-s": ["3.13.CCC", "3.13.CCC.1"]},
    )

    args = Namespace(corpus="enron")
    assert enron_mod.main(args) == 0
    report = json.loads((paths["artifacts"] / "enron_idmap_coverage.json").read_text())
    t201 = report["topics"]["201"]
    assert t201["judged"] == 4 and t201["relevant"] == 2
    assert t201["estimated_r"] == 3.0
    assert t201["policies"]["fold_to_parent"]["covered"] == 1
    assert t201["per_custodian_fold"] == {"skilling-j": {"judged": 1, "relevant": 1}}
    assert t201["relevant_by_custodian_full"]["top"] == {
        "shackleton-s": 1, "skilling-j": 1,
    }
    # qrels_raw written and typed
    pa.parquet.read_table(paths["store"] / "enron" / "qrels_raw.parquet").cast(QRELS_RAW)


def test_main_missing_inputs_exit_2(tmp_path, monkeypatch):
    from argparse import Namespace

    import pipeline.qrels.enron as enron_mod
    from pipeline.config import PipelineConfig

    paths = {"raw": tmp_path / "raw", "store": tmp_path / "store",
             "artifacts": tmp_path / "artifacts"}
    cfg = PipelineConfig(paths=paths, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    monkeypatch.setattr(enron_mod, "load_pipeline_config", lambda: cfg)
    assert enron_mod.main(Namespace(corpus="enron")) == 2  # qrels file missing

    # qrels present but idmap missing -> the second guard, still a clean exit 2
    (tmp_path / "raw" / "enron" / "trec").mkdir(parents=True)
    with gzip.open(tmp_path / "raw" / "enron" / "trec" / "qrels.t10legallearn.gz",
                   "wt") as f:
        f.write("201:3.11.AAA 100 1\n")
    assert enron_mod.main(Namespace(corpus="enron")) == 2


def test_main_without_tarball_omits_full_ranking(tmp_path, monkeypatch):
    from argparse import Namespace

    import pyarrow as pa

    import pipeline.qrels.enron as enron_mod
    from pipeline.config import PipelineConfig
    from pipeline.store import DOC_ID_MAP, MESSAGES, write_table

    raw = tmp_path / "raw"
    (raw / "enron" / "trec").mkdir(parents=True)
    with gzip.open(raw / "enron" / "trec" / "qrels.t10legallearn.gz", "wt") as f:
        f.write("\n".join(LINES) + "\n")
    paths = {
        "raw": raw, "store": tmp_path / "store", "artifacts": tmp_path / "artifacts",
    }
    cfg = PipelineConfig(paths=paths, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    monkeypatch.setattr(enron_mod, "load_pipeline_config", lambda: cfg)
    idmap = pa.table(
        {
            "corpus": ["enron"], "doc_id": ["enron-aaaa"], "trec_doc_id": ["3.11.AAA"],
            "match_method": ["filename"], "confidence": [1.0], "ambiguous": [False],
        }
    ).cast(DOC_ID_MAP)
    write_table(paths["store"], "enron", "doc_id_map", idmap)
    msg = {c.name: None for c in MESSAGES} | {
        "doc_id": "enron-aaaa", "corpus": "enron", "custodian": "skilling-j",
        "source_file": "z", "source_index": 0, "references": [], "to": [], "cc": [],
        "bcc": [], "attachment_names": [], "parse_warnings": [], "body_text": "x",
        "body_norm": "x", "body_hash": "h", "has_attachments": False,
        "attachment_count": 0, "token_estimate": 1,
    }
    write_table(paths["store"], "enron", "messages",
                pa.Table.from_pylist([msg], schema=MESSAGES))
    assert enron_mod.main(Namespace(corpus="enron")) == 0
    report = json.loads((paths["artifacts"] / "enron_idmap_coverage.json").read_text())
    assert "relevant_by_custodian_full" not in report["topics"]["201"]


def test_weighted_metrics_reproduce_estimator_through_parquet(tmp_path):
    """The bridge invariant behind the P5 metrics: sum of relevant judged
    weights (after the parquet round-trip) == estimated_r, and a perfect
    classifier through evaluate(weighted=True) reproduces it as tp+fn."""
    import pandas as pd
    import pyarrow.parquet as pq

    from pipeline.store import write_table
    from pipeline.validate.metrics import evaluate

    lines = (
        ["201:3.%d.AAA 100 1" % i for i in range(3)]          # 3/3 judged, all rel
        + ["201:3.%d.BBB 1000 %d" % (i, 1 if i < 2 else 0) for i in range(3)]
        + ["201:3.%d.BBB 1000 -1" % i for i in range(3, 7)]   # w = 7/3, non-dyadic
    )
    rows, cells = parse_qrels(write_qrels(tmp_path, lines))
    table = build_table(rows, cells)
    write_table(tmp_path / "store", "bush", "qrels_raw", table)  # any corpus slot
    back = pq.read_table(tmp_path / "store" / "bush" / "qrels_raw.parquet").to_pandas()
    back["doc_id"] = back["trec_doc_id"]
    perfect = pd.DataFrame({
        "doc_id": back["doc_id"],
        "decision": ["responsive" if r == 1 else "not_responsive"
                     for r in back["relevance"]],
    })
    res = evaluate(perfect, back, topic="201", weighted=True)
    est = estimated_r(cells, "201")
    assert est == round(3 * 1.0 + 2 * (7 / 3), 2)
    assert res.tp + res.fn == pytest.approx(est, abs=0.01)


def test_weighted_refuses_partial_coverage(tmp_path):
    import pandas as pd
    import pyarrow.parquet as pq

    from pipeline.validate.metrics import evaluate

    rows, cells = parse_qrels(write_qrels(tmp_path))
    back = build_table(rows, cells).to_pandas()
    back["doc_id"] = back["trec_doc_id"]
    partial = pd.DataFrame({"doc_id": back["doc_id"][:2], "decision": ["responsive"] * 2})
    with pytest.raises(ValueError, match="every judged doc"):
        evaluate(partial, back, topic="201", weighted=True)


def test_cli_qrels_dispatch_routes_by_corpus(monkeypatch):
    from pipeline.__main__ import build_parser

    monkeypatch.setattr("pipeline.qrels.parse.main", lambda a: 43)
    monkeypatch.setattr("pipeline.qrels.enron.main", lambda a: 44)
    parser = build_parser()
    bush = parser.parse_args(["qrels", "--corpus", "bush"])
    enron = parser.parse_args(["qrels", "--corpus", "enron"])
    assert bush.func(bush) == 43
    assert enron.func(enron) == 44
