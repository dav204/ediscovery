import pytest

from pipeline.config import BudgetConfig
from pipeline.review import cost

pytestmark = pytest.mark.phase2


@pytest.fixture
def budget():
    return BudgetConfig(
        prices={"claude-haiku-4-5": {"input": 0.50, "output": 2.50}},
        caps={"dev_loop": 25.0, "bush_sample": 0.0},
        total_stop_usd=200.0,
    )


def test_estimate_hand_computed(budget):
    # 100 requests, 70K doc tokens total, 1K prompt tokens each, 512 max out.
    # input = 70_000 + 100*1_000 = 170_000 tok -> 0.17 MTok * $0.50 = $0.085
    # output = 100*512 = 51_200 tok -> 0.0512 MTok * $2.50 = $0.128
    est = cost.estimate(100, 70_000, 1_000, 512, "claude-haiku-4-5", budget)
    assert est.input_tokens == 170_000
    assert est.output_tokens == 51_200
    assert est.usd == pytest.approx(0.085 + 0.128, abs=1e-4)


def test_unknown_model_rejected(budget):
    with pytest.raises(KeyError):
        cost.estimate(1, 10, 10, 10, "claude-nonexistent", budget)


def test_check_passes_under_cap(tmp_path, budget):
    est = cost.estimate(100, 70_000, 1_000, 512, "claude-haiku-4-5", budget)
    cost.check("dev_loop", est, budget, tmp_path / "spend.jsonl")  # no raise


def test_check_locked_phase_raises(tmp_path, budget):
    est = cost.estimate(1, 10, 10, 10, "claude-haiku-4-5", budget)
    with pytest.raises(cost.BudgetExceeded, match="locked"):
        cost.check("bush_sample", est, budget, tmp_path / "spend.jsonl")


def test_check_counts_prior_spend(tmp_path, budget):
    spend = tmp_path / "spend.jsonl"
    cost.record(spend, batch_id="b1", phase="dev_loop", corpus="bush", topic="t",
                model="claude-haiku-4-5", n_requests=10,
                input_tokens=40_000_000, output_tokens=2_000_000, budget=budget)
    # prior = 40 MTok*$0.5 + 2 MTok*$2.5 = $25.00 -> dev_loop cap exhausted
    est = cost.estimate(1, 1000, 100, 100, "claude-haiku-4-5", budget)
    with pytest.raises(cost.BudgetExceeded, match="dev_loop"):
        cost.check("dev_loop", est, budget, spend)


def test_total_stop(tmp_path, budget):
    # prior spend below = 40 MTok*$0.5 + 2 MTok*$2.5 = $25.00; stop just above it
    budget_high_cap = BudgetConfig(
        prices=budget.prices, caps={"dev_loop": 1000.0}, total_stop_usd=25.10
    )
    spend = tmp_path / "spend.jsonl"
    cost.record(spend, batch_id="b1", phase="dev_loop", corpus="bush", topic="t",
                model="claude-haiku-4-5", n_requests=10,
                input_tokens=40_000_000, output_tokens=2_000_000, budget=budget_high_cap)
    est = cost.estimate(100, 70_000, 1_000, 512, "claude-haiku-4-5", budget_high_cap)
    with pytest.raises(cost.BudgetExceeded, match="total"):
        cost.check("dev_loop", est, budget_high_cap, spend)


def test_record_cumulative(tmp_path, budget):
    spend = tmp_path / "spend.jsonl"
    r1 = cost.record(spend, batch_id="b1", phase="dev_loop", corpus="bush", topic="t",
                     model="claude-haiku-4-5", n_requests=1,
                     input_tokens=1_000_000, output_tokens=0, budget=budget)
    r2 = cost.record(spend, batch_id="b2", phase="dev_loop", corpus="bush", topic="t",
                     model="claude-haiku-4-5", n_requests=1,
                     input_tokens=1_000_000, output_tokens=0, budget=budget)
    assert r1["cost_usd"] == pytest.approx(0.50)
    assert r2["cumulative_usd"] == pytest.approx(1.00)
    total, per_phase = cost.total_spent(spend)
    assert total == pytest.approx(1.00)
    assert per_phase == {"dev_loop": pytest.approx(1.00)}
