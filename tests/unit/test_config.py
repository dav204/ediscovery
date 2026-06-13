"""The committed config files in config/ must always load and validate."""

import pytest

from pipeline.config import (
    load_budget_config,
    load_corpus_config,
    load_pipeline_config,
)

pytestmark = pytest.mark.phase0


def test_pipeline_config_loads():
    cfg = load_pipeline_config()
    assert {"raw", "store", "batches", "decisions", "spend", "productions", "artifacts"} <= set(cfg.paths)
    assert cfg.models["tier1"].startswith("claude-")
    assert cfg.batch["max_requests_per_batch"] <= 10000  # Anthropic Batches API hard cap
    assert cfg.normalization_version


def test_budget_config_loads():
    budget = load_budget_config()
    assert budget.total_stop_usd > 0
    assert budget.caps["dev_loop"] > 0
    # Full-run caps ship locked; raising them is an explicit pre-run decision.
    assert budget.caps["bush_sample"] == 0
    for model, price in budget.prices.items():
        assert price["input"] > 0 and price["output"] > 0, model


def test_budget_prices_cover_configured_models():
    cfg = load_pipeline_config()
    budget = load_budget_config()
    for role, model in cfg.models.items():
        assert model in budget.prices, f"no price for {role} model {model}"


@pytest.mark.parametrize("corpus", ["bush", "enron"])
def test_corpus_configs_load(corpus):
    cc = load_corpus_config(corpus)
    assert cc.corpus == corpus
    assert cc.files, "manifest must have at least one entry"
    for f in cc.files:
        assert f.dest, f"{f.name} missing dest"


def test_enron_scope():
    cc = load_corpus_config("enron")
    assert cc.chosen_topics == ["201"]
    assert {c["id"] for c in cc.custodians} == {"skilling-j", "lay-k", "kaminski-v"}
