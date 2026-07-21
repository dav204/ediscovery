"""Dedup / threading / inclusive detection on synthetic docs (phase4 gate)."""

import json
from collections import Counter
from datetime import datetime, timezone

import pyarrow as pa
import pytest

from pipeline.preprocess.dedup import dedup_key, exact_clusters, near_clusters, shingles
from pipeline.preprocess.threads import build_threads
from pipeline.store import DEDUP_EXACT, DEDUP_NEAR, MESSAGES, THREADS, write_table

pytestmark = pytest.mark.phase4


def d(doc_id, subject, body, date=None, attachments=None):
    return {
        "doc_id": doc_id,
        "subject_norm": subject,
        "body_norm": body,
        "date_utc": date,
        "attachment_names": attachments or [],
    }


def ts(day, hour=0):
    return datetime(2001, 6, day, hour, tzinfo=timezone.utc)


# -- exact dedup --------------------------------------------------------------

def test_dedup_key_includes_subject():
    # the placeholder-body scenario: same body, different subjects != dups
    assert dedup_key("meeting a", "placeholder") != dedup_key("meeting b", "placeholder")
    assert dedup_key("meeting a", "placeholder") == dedup_key("meeting a", "placeholder")


def test_exact_clusters_canonical_and_ranks():
    rows = [
        d("enron-cc", "s", "same body", ts(3)),
        d("enron-bb", "s", "same body", ts(1)),      # earliest -> canonical
        d("enron-aa", "s", "same body", None),        # null date sorts last
        d("enron-dd", "s", "other body", ts(1)),
    ]
    table = exact_clusters(rows).cast(DEDUP_EXACT)
    by_doc = {r["doc_id"]: r for r in table.to_pylist()}
    assert by_doc["enron-bb"]["dup_rank"] == 0
    assert all(by_doc[x]["canonical_doc_id"] == "enron-bb" for x in ("enron-aa", "enron-bb", "enron-cc"))
    assert by_doc["enron-cc"]["dup_rank"] == 1 and by_doc["enron-aa"]["dup_rank"] == 2
    assert by_doc["enron-dd"]["canonical_doc_id"] == "enron-dd"  # singleton row present


def test_exact_clusters_deterministic():
    rows = [d(f"enron-{i:02d}", "s", "body", ts(1 + i % 3)) for i in range(9)]
    assert exact_clusters(rows).equals(exact_clusters(list(reversed(rows))))


# -- near dedup ---------------------------------------------------------------

WORDS = [f"w{i}" for i in range(60)]
LONG_A = " ".join(WORDS)
LONG_A2 = " ".join(WORDS + ["tail"])          # near-identical
LONG_B = " ".join(f"x{i}" for i in range(60))  # unrelated

NEAR_KW = dict(seed=1, num_perm=128, threshold=0.85, shingle_words=3,
               min_body_words=30, boilerplate_min_repeats=50)


def test_shingles_short_and_empty():
    assert shingles("", 3) == set()
    assert shingles("a b", 3) == {"a b"}


def test_near_clusters_groups_similar_only():
    rows = [
        d("enron-aa", "s", LONG_A, ts(1)),
        d("enron-bb", "s", LONG_A2, ts(2)),
        d("enron-cc", "s", LONG_B, ts(1)),
    ]
    table = near_clusters(rows, **NEAR_KW).cast(DEDUP_NEAR)
    recs = table.to_pylist()
    assert {r["doc_id"] for r in recs} == {"enron-aa", "enron-bb"}
    assert all(r["cluster_id"] == "near-enron-aa" for r in recs)
    by_doc = {r["doc_id"]: r for r in recs}
    assert by_doc["enron-aa"]["jaccard_to_canonical"] == 1.0
    assert by_doc["enron-bb"]["jaccard_to_canonical"] >= 0.85
    assert recs[0]["minhash_seed_version"] == "seed1-p128-w3-t0.85-mw30-b50"


def test_near_clusters_exclusions():
    rows = [
        d("enron-aa", "s", LONG_A, ts(1)),
        d("enron-bb", "s", LONG_A, ts(2)),
        d("enron-cc", "s", "tiny body", ts(1)),
        d("enron-dd", "s", "tiny body", ts(2)),
    ]
    # boilerplate: LONG_A appears 60x corpus-wide -> excluded even though identical
    freq = Counter({LONG_A: 60})
    assert near_clusters(rows, body_freq=freq, **NEAR_KW).num_rows == 0
    # short bodies below min_body_words never cluster
    assert near_clusters(rows[2:], **NEAR_KW).num_rows == 0


def test_near_clusters_deterministic():
    rows = [d("enron-aa", "s", LONG_A, ts(1)), d("enron-bb", "s", LONG_A2, ts(2))]
    assert near_clusters(rows, **NEAR_KW).equals(
        near_clusters(list(reversed(rows)), **NEAR_KW)
    )


def test_near_clusters_skips_shared_canonical_bodies():
    # identical body under different subjects: exact dedup kept them apart on
    # purpose; near-dup must not weld them back at jaccard 1.0
    rows = [
        d("enron-aa", "meeting a", LONG_A, ts(1)),
        d("enron-bb", "meeting b", LONG_A, ts(2)),
        d("enron-cc", "meeting c", LONG_A2, ts(3)),
    ]
    assert near_clusters(rows, **NEAR_KW).num_rows == 0


def test_near_clusters_star_not_transitive_chain():
    # A~B and B~C but A!~C: single-link would chain all three; star keeps the
    # invariant jaccard_to_canonical >= threshold for every member.
    words = [f"w{i}" for i in range(80)]
    a = " ".join(words)
    b = " ".join(words[:70] + [f"y{i}" for i in range(10)])
    c = " ".join(words[:55] + [f"y{i}" for i in range(10)] + [f"z{i}" for i in range(15)])
    rows = [d("enron-aa", "s", a, ts(1)), d("enron-bb", "s", b, ts(2)),
            d("enron-cc", "s", c, ts(3))]
    from datasketch import MinHash

    from pipeline.preprocess.dedup import shingles

    def mh(text):
        m = MinHash(num_perm=128, seed=1)
        for s in shingles(text, 3):
            m.update(s.encode())
        return m

    threshold = 0.6
    assert mh(a).jaccard(mh(b)) >= threshold          # A~B
    assert mh(b).jaccard(mh(c)) >= threshold          # B~C
    assert mh(a).jaccard(mh(c)) < threshold           # A!~C — the chain trap
    recs = near_clusters(rows, **{**NEAR_KW, "threshold": threshold}).to_pylist()
    by_doc = {r["doc_id"]: r for r in recs}
    # star invariant: every member clears the threshold against ITS canonical
    assert all(r["jaccard_to_canonical"] >= threshold for r in recs)
    # A absorbed B; C was NOT chained into A's cluster through B
    assert by_doc["enron-bb"]["canonical_doc_id"] == "enron-aa"
    assert "enron-cc" not in by_doc or by_doc["enron-cc"]["canonical_doc_id"] != "enron-aa"


# -- threading + inclusive ----------------------------------------------------

def test_build_threads_chain_and_methods():
    rows = [
        d("enron-r1", "widget deal", "proposal text", ts(1)),
        d("enron-r2", "widget deal", "reply\n> proposal text", ts(2)),
        d("enron-r3", "widget deal", "fresh counterpoint", ts(3)),
        d("enron-s1", "", "no subject doc", ts(1)),
        d("enron-s2", "lone subject", "alone", ts(1)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows)}
    r1, r2, r3 = by_doc["enron-r1"], by_doc["enron-r2"], by_doc["enron-r3"]
    assert r1["thread_id"] == r2["thread_id"] == r3["thread_id"] == "t-enron-r1"
    assert (r1["parent_doc_id"], r1["depth"], r1["method"]) == (None, 0, "subject_fallback")
    assert (r2["parent_doc_id"], r2["depth"], r2["method"]) == ("enron-r1", 1, "containment")
    assert (r3["parent_doc_id"], r3["depth"], r3["method"]) == ("enron-r2", 2, "subject_fallback")
    assert by_doc["enron-s1"]["method"] == by_doc["enron-s2"]["method"] == "singleton"
    assert by_doc["enron-s1"]["thread_id"] == "t-enron-s1"


def test_inclusive_truth_table():
    rows = [
        # r1's body is quoted into r2 -> r1 not inclusive
        d("enron-r1", "deal", "original analysis", ts(1)),
        # r2 quoted into r3 BUT carries an attachment r3 lacks -> attachment_unique
        d("enron-r2", "deal", "comment\noriginal analysis", ts(2), ["model.xls"]),
        # r3's own text never re-quoted -> unique_content (r4 exists after it)
        d("enron-r3", "deal", "summary\ncomment\noriginal analysis", ts(3)),
        # r4 is last -> terminal_node
        d("enron-r4", "deal", "short sign-off", ts(4)),
        d("enron-s1", "solo", "alone", ts(1)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows)}
    assert not by_doc["enron-r1"]["is_inclusive"]
    assert by_doc["enron-r2"]["inclusive_reason"] == "attachment_unique"
    assert by_doc["enron-r3"]["inclusive_reason"] == "unique_content"
    assert by_doc["enron-r4"]["inclusive_reason"] == "terminal_node"
    assert by_doc["enron-s1"]["inclusive_reason"] == "terminal_node"


def test_threads_golden():
    rows = [
        d("enron-r1", "golden", "seed text", ts(1)),
        d("enron-r2", "golden", "reply with seed text", ts(2)),
    ]
    assert build_threads(rows) == [
        {"doc_id": "enron-r1", "thread_id": "t-enron-r1", "parent_doc_id": None,
         "depth": 0, "method": "subject_fallback", "is_inclusive": False,
         "inclusive_reason": None},
        {"doc_id": "enron-r2", "thread_id": "t-enron-r1", "parent_doc_id": "enron-r1",
         "depth": 1, "method": "containment", "is_inclusive": True,
         "inclusive_reason": "terminal_node"},
    ]


def test_build_threads_splits_conversations_on_date_gap():
    rows = [
        d("enron-r1", "budget", "first conversation opener", ts(1)),
        d("enron-r2", "budget", "first conversation reply", ts(3)),
        # same normalized subject, five months later: a different conversation
        d("enron-r3", "budget", "unrelated later conversation",
          datetime(2001, 11, 20, tzinfo=timezone.utc)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows, max_gap_days=60)}
    assert by_doc["enron-r1"]["thread_id"] == by_doc["enron-r2"]["thread_id"]
    assert by_doc["enron-r3"]["thread_id"] == "t-enron-r3"
    assert by_doc["enron-r3"]["method"] == "singleton"
    assert by_doc["enron-r3"]["parent_doc_id"] is None


def test_containment_survives_quote_markers_and_rewrapping():
    original = "the widget proposal needs review before friday"
    quoted = "agreed, see below\n\n> the widget proposal\n> needs review\n> before friday"
    rows = [
        d("enron-r1", "widgets", original, ts(1)),
        d("enron-r2", "widgets", quoted, ts(2)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows)}
    assert by_doc["enron-r2"]["method"] == "containment"
    assert not by_doc["enron-r1"]["is_inclusive"]  # fully quoted downstream
    assert by_doc["enron-r2"]["inclusive_reason"] == "terminal_node"


def test_containment_checks_any_ancestor_not_just_parent():
    rows = [
        d("enron-r1", "deal", "root analysis text", ts(1)),
        d("enron-r2", "deal", "interleaved unrelated note", ts(2)),
        d("enron-r3", "deal", "reply quoting: root analysis text", ts(3)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows)}
    # r3's chain parent is r2, but it quotes r1 — method must still see it
    assert by_doc["enron-r3"]["parent_doc_id"] == "enron-r2"
    assert by_doc["enron-r3"]["method"] == "containment"


def test_none_and_empty_subjects():
    rows = [
        d("enron-r1", None, "some body", ts(1)),
        d("enron-r2", "", "some body", ts(2)),
    ]
    by_doc = {t["doc_id"]: t for t in build_threads(rows)}
    assert all(t["method"] == "singleton" for t in by_doc.values())
    # dedup: None and "" subjects normalize to the same key
    assert dedup_key(None, "some body") == dedup_key("", "some body")
    table = exact_clusters(rows)
    assert set(table.column("canonical_doc_id").to_pylist()) == {"enron-r1"}


# -- main() orchestration -----------------------------------------------------

def make_env(tmp_path, monkeypatch, messages):
    from argparse import Namespace

    import pipeline.preprocess.run as run_mod
    from pipeline.config import PipelineConfig

    paths = {
        "raw": tmp_path / "raw", "store": tmp_path / "store",
        "artifacts": tmp_path / "artifacts", "batches": tmp_path / "b",
        "decisions": tmp_path / "d", "spend": tmp_path / "s",
        "productions": tmp_path / "p",
    }
    cfg = PipelineConfig(
        paths=paths, models={}, batch={}, seeds={"minhash": 1}, sampling={},
        normalization_version="v1",
        preprocess={"minhash_num_perm": 128, "near_dup_threshold": 0.85,
                    "shingle_words": 3, "near_dup_min_body_words": 3,
                    "boilerplate_body_min_repeats": 3, "thread_max_gap_days": 60},
    )
    monkeypatch.setattr(run_mod, "load_pipeline_config", lambda: cfg)
    if messages is not None:
        rows = [
            {c.name: None for c in MESSAGES} | {
                "doc_id": m["doc_id"], "corpus": "enron", "source_file": m["doc_id"],
                "source_index": 0, "references": [], "to": [], "cc": [], "bcc": [],
                "attachment_names": m["attachment_names"], "parse_warnings": [],
                "body_text": m["body_norm"], "body_norm": m["body_norm"],
                "body_hash": "h", "has_attachments": bool(m["attachment_names"]),
                "attachment_count": len(m["attachment_names"]),
                "subject_norm": m["subject_norm"], "date_utc": m["date_utc"],
                "token_estimate": 5,
            }
            for m in messages
        ]
        write_table(paths["store"], "enron", "messages",
                    pa.Table.from_pylist(rows, schema=MESSAGES))
    return run_mod, Namespace(corpus="enron", force=False), paths


def test_main_writes_tables_and_report(tmp_path, monkeypatch, capsys):
    messages = [
        d("enron-aa", "deal", "original analysis", ts(1)),
        d("enron-ab", "deal", "original analysis", ts(2)),   # exact dup of aa
        d("enron-ac", "deal", "reply\noriginal analysis", ts(3)),
        d("enron-ba", "", "standalone", ts(1)),
        # near-dup pair: long bodies through the config->near_clusters wiring
        d("enron-na", "memo one", LONG_A, ts(1)),
        d("enron-nb", "memo two", LONG_A2, ts(2)),
        # boilerplate: repeated >= boilerplate_body_min_repeats (3) pre-dedup,
        # 1 canonical survivor that must NOT enter near-dup
        d("enron-pa", "p", LONG_B, ts(1)),
        d("enron-pb", "p", LONG_B, ts(2)),
        d("enron-pc", "p", LONG_B, ts(3)),
    ]
    run_mod, args, paths = make_env(tmp_path, monkeypatch, messages)
    assert run_mod.main(args) == 0
    report = json.loads((paths["artifacts"] / "enron_preprocess_report.json").read_text())
    assert report["messages"] == 9 and report["canonical_docs"] == 6
    assert report["exact_dup_docs"] == 3
    assert report["near_dup_docs"] == 2 and report["near_clusters"] == 1
    assert report["boilerplate_bodies"] == 1
    assert report["inclusive_docs"] >= 2
    assert set(report) == {
        "corpus", "messages", "canonical_docs", "exact_dup_docs",
        "boilerplate_bodies", "near_dup_docs", "near_clusters", "threads",
        "singleton_threads", "inclusive", "inclusive_docs", "params",
    }
    for t, schema in (("dedup_exact", DEDUP_EXACT), ("dedup_near", DEDUP_NEAR),
                      ("threads", THREADS)):
        pa.parquet.read_table(paths["store"] / "enron" / f"{t}.parquet").cast(schema)
    near = pa.parquet.read_table(paths["store"] / "enron" / "dedup_near.parquet").to_pylist()
    assert {r["doc_id"] for r in near} == {"enron-na", "enron-nb"}
    # resume
    assert run_mod.main(args) == 0
    assert "already done" in capsys.readouterr().out


def test_main_force_rerun_byte_identical(tmp_path, monkeypatch):
    messages = [
        d("enron-aa", "deal", "original analysis", ts(1)),
        d("enron-na", "memo one", LONG_A, ts(1)),
        d("enron-nb", "memo two", LONG_A2, ts(2)),
    ]
    run_mod, args, paths = make_env(tmp_path, monkeypatch, messages)
    assert run_mod.main(args) == 0
    first = {
        name: (paths["store"] / "enron" / name).read_bytes()
        for name in ("dedup_exact.parquet", "dedup_near.parquet", "threads.parquet")
    }
    first["report"] = (paths["artifacts"] / "enron_preprocess_report.json").read_bytes()
    args.force = True
    assert run_mod.main(args) == 0
    assert first == {
        name: (paths["store"] / "enron" / name).read_bytes()
        for name in ("dedup_exact.parquet", "dedup_near.parquet", "threads.parquet")
    } | {"report": (paths["artifacts"] / "enron_preprocess_report.json").read_bytes()}


def test_main_recomputes_when_params_drift(tmp_path, monkeypatch, capsys):
    messages = [d("enron-aa", "deal", "original analysis", ts(1))]
    run_mod, args, paths = make_env(tmp_path, monkeypatch, messages)
    assert run_mod.main(args) == 0
    report_path = paths["artifacts"] / "enron_preprocess_report.json"
    stale = json.loads(report_path.read_text())
    stale["params"]["near_dup_threshold"] = 0.5  # simulate a config edit
    report_path.write_text(json.dumps(stale, indent=2, sort_keys=True) + "\n")
    assert run_mod.main(args) == 0
    out = capsys.readouterr().out
    assert "don't match current config" in out
    assert json.loads(report_path.read_text())["params"]["near_dup_threshold"] == 0.85


def test_main_missing_config_block_exit_2(tmp_path, monkeypatch):
    run_mod, args, _ = make_env(tmp_path, monkeypatch, None)
    import pipeline.preprocess.run as run_module
    from pipeline.config import PipelineConfig
    cfg = PipelineConfig(paths={}, models={}, batch={}, seeds={}, sampling={},
                         normalization_version="v1")
    monkeypatch.setattr(run_module, "load_pipeline_config", lambda: cfg)
    assert run_module.main(args) == 2


def test_main_missing_messages_exit_2(tmp_path, monkeypatch):
    run_mod, args, _ = make_env(tmp_path, monkeypatch, None)
    assert run_mod.main(args) == 2


def test_main_bush_not_implemented(tmp_path, monkeypatch):
    run_mod, args, _ = make_env(tmp_path, monkeypatch, None)
    args.corpus = "bush"
    assert run_mod.main(args) == 2
