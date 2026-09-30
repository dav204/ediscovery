"""Verbatim v1 protocol generation (synthetic topics + the committed golden
copy of the public HiCAL topic list; no document content)."""

from pathlib import Path

import pytest

from pipeline.config import REPO_ROOT
from pipeline.review.prompts import load_protocol
from pipeline.review.protocol_gen import (
    GENERATED_MARKER,
    read_bush_topics,
    read_enron_topics,
    render_v1,
    write_v1,
)
from pipeline.review.run import resolve_protocol

pytestmark = pytest.mark.phase2

GOLDEN_TOPICS = Path(__file__).parent.parent / "golden" / "athome4.topics.sample"


def test_read_bush_topics_splits_title_and_request(tmp_path):
    f = tmp_path / "topics"
    f.write_text("401 1  Widgets -- All documents concerning widgets -- and gadgets.\n\n"
                 "402 1  Gears -- All documents concerning gears.\n")
    assert read_bush_topics(f) == {
        "401": ("Widgets", "All documents concerning widgets -- and gadgets."),
        "402": ("Gears", "All documents concerning gears."),
    }


def test_read_bush_topics_rejects_missing_separator(tmp_path):
    f = tmp_path / "topics"
    f.write_text("401 1  Widgets with no request\n")
    with pytest.raises(ValueError, match="separator"):
        read_bush_topics(f)


def test_render_v1_carries_request_verbatim_and_output_contract():
    text = render_v1("bush", "401", "Widgets", "All documents  concerning\nwidgets.")
    assert '"All documents concerning widgets."' in text
    assert text.startswith(GENERATED_MARKER)
    contract = text[text.index('{"decision"'):]
    for key in ("decision", "confidence", "rationale"):
        assert f'"{key}"' in contract
    for decision in ("responsive", "not_responsive", "borderline"):
        assert f'"{decision}"' in contract


def test_render_v1_rejects_empty_request():
    with pytest.raises(ValueError, match="empty"):
        render_v1("bush", "401", "Widgets", "  ")


def test_write_v1_is_found_by_resolve_protocol(tmp_path):
    path = write_v1(tmp_path, "bush", "401", "Widgets", "All documents concerning widgets.")
    assert path == tmp_path / "bush" / "athome401.v1.md"
    assert resolve_protocol(tmp_path, "bush", "401") == path
    assert load_protocol(path).version == "athome401.v1"
    path = write_v1(tmp_path, "enron", "201", "Gears", "All documents concerning gears.")
    assert resolve_protocol(tmp_path, "enron", "201") == path


@pytest.mark.parametrize("existing", ["athome401.v1.md", "athome401.v2.md"])
def test_write_v1_never_overwrites_an_existing_protocol(tmp_path, existing):
    (tmp_path / "bush").mkdir()
    (tmp_path / "bush" / existing).write_text("hand-written")
    assert write_v1(tmp_path, "bush", "401", "Widgets", "All documents.") is None
    assert (tmp_path / "bush" / existing).read_text() == "hand-written"


def test_enron_topic_statements_parse():
    topics = read_enron_topics()
    title, request = topics["201"]
    assert title == "Prepay transactions"
    assert request.startswith("All documents or communications")


def test_committed_generated_protocols_match_official_topic_text():
    # Drift guard: a generated v1 must stay byte-identical to what the official
    # topic text renders to. Changing the wording means writing a v2, never
    # editing the v1 that decisions already point at.
    sources = {"bush": read_bush_topics(GOLDEN_TOPICS), "enron": read_enron_topics()}
    generated = [p for p in sorted((REPO_ROOT / "protocols").glob("*/*.v1.md"))
                 if p.read_text().startswith(GENERATED_MARKER)]
    if not generated:
        pytest.skip("no generated v1 protocols committed yet")
    for path in generated:
        corpus = path.parent.name
        assert corpus in sources, f"{path}: no official topic source for {corpus}"
        prefix = "athome" if corpus == "bush" else "topic"
        topic = path.stem.removeprefix(prefix).removesuffix(".v1")
        title, request = sources[corpus][topic]
        assert path.read_text() == render_v1(corpus, topic, title, request), path
