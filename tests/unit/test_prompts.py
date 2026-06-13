from pathlib import Path

import pytest

from pipeline.review.prompts import load_protocol, render_messages

pytestmark = pytest.mark.phase2

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_protocol_version_and_hash():
    p = load_protocol(FIXTURES / "fixture_topic.v1.md")
    assert p.version == "fixture_topic.v1"
    assert len(p.content_hash) == 12
    assert "widget procurement" in p.content


def test_bad_filename_rejected(tmp_path):
    f = tmp_path / "noversion.md"
    f.write_text("content")
    with pytest.raises(ValueError, match="must be named"):
        load_protocol(f)


def test_empty_protocol_rejected(tmp_path):
    f = tmp_path / "empty.v1.md"
    f.write_text("   \n")
    with pytest.raises(ValueError, match="empty"):
        load_protocol(f)


def test_render_messages_golden():
    # Golden test: rendered prompt for a fixed doc. If this changes, the prompt
    # the model sees changed — the protocol version must be bumped (CLAUDE.md).
    p = load_protocol(FIXTURES / "fixture_topic.v1.md")
    doc = {
        "custodian": "skilling-j",
        "from_addr": "a@example.com",
        "to": ["b@example.com", "c@example.com"],
        "cc": [],
        "date_raw": "Tue, 1 May 2001 07:00:00 -0500",
        "subject": "widgets",
        "attachment_names": ["spec.doc"],
        "body_text": "synthetic body",
    }
    messages = render_messages(p, doc)
    assert len(messages) == 1 and messages[0]["role"] == "user"
    content = messages[0]["content"]
    expected_doc_block = (
        "<document>\n<metadata>\n"
        "custodian: skilling-j\n"
        "from: a@example.com\n"
        "to: b@example.com; c@example.com\n"
        "cc: \n"
        "date: Tue, 1 May 2001 07:00:00 -0500\n"
        "subject: widgets\n"
        "attachments: spec.doc\n"
        "</metadata>\n<body>\nsynthetic body\n</body>\n</document>"
    )
    assert content == f"{p.content}\n\n{expected_doc_block}"
