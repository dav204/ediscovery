import pytest

from pipeline.__main__ import build_parser

pytestmark = pytest.mark.phase0


def test_parser_has_all_commands():
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    expected = {
        "acquire", "status", "spend", "ingest", "preprocess", "qrels", "devset",
        "review", "privilege", "produce", "validate", "ui",
    }
    assert expected <= set(sub.choices)


def test_stub_commands_exit_2(capsys):
    args = build_parser().parse_args(["review", "--corpus", "bush", "--topic", "athome102", "--tier", "1"])
    assert args.func(args) == 2
    assert "not implemented" in capsys.readouterr().err


def test_acquire_requires_corpus():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["acquire"])
