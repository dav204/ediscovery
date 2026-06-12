"""Load and validate the YAML configs in config/."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


@dataclass(frozen=True)
class PipelineConfig:
    paths: dict[str, Path]
    models: dict[str, str]
    batch: dict
    seeds: dict[str, int]
    sampling: dict
    normalization_version: str


@dataclass(frozen=True)
class BudgetConfig:
    prices: dict[str, dict[str, float]]  # model -> {input, output} USD/MTok, batch rates
    caps: dict[str, float]  # phase -> USD cap (0 = locked)
    total_stop_usd: float


@dataclass(frozen=True)
class CorpusFile:
    name: str
    url: str | None
    sha256: str | None
    dest: str


@dataclass(frozen=True)
class CorpusConfig:
    corpus: str
    format: str
    files: list[CorpusFile]
    chosen_topics: list[str]
    custodians: list[dict] = field(default_factory=list)
    expected_message_count: int | None = None


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return data


def load_pipeline_config(config_dir: Path = CONFIG_DIR) -> PipelineConfig:
    raw = _load_yaml(config_dir / "pipeline.yaml")
    paths = {k: REPO_ROOT / v for k, v in raw["paths"].items()}
    return PipelineConfig(
        paths=paths,
        models=raw["models"],
        batch=raw["batch"],
        seeds=raw["seeds"],
        sampling=raw["sampling"],
        normalization_version=raw["normalization"]["version"],
    )


def load_budget_config(config_dir: Path = CONFIG_DIR) -> BudgetConfig:
    raw = _load_yaml(config_dir / "budget.yaml")
    prices = raw["prices"]
    for model, p in prices.items():
        if set(p) != {"input", "output"}:
            raise ValueError(f"price for {model} must have exactly input/output keys")
    caps = {phase: float(c["usd"]) for phase, c in raw["caps"].items()}
    return BudgetConfig(prices=prices, caps=caps, total_stop_usd=float(raw["total_stop_usd"]))


def load_corpus_config(corpus: str, config_dir: Path = CONFIG_DIR) -> CorpusConfig:
    raw = _load_yaml(config_dir / "corpora" / f"{corpus}.yaml")
    if raw["corpus"] != corpus:
        raise ValueError(f"corpus field {raw['corpus']!r} != file name {corpus!r}")
    files = [CorpusFile(**f) for f in raw["files"]]
    return CorpusConfig(
        corpus=raw["corpus"],
        format=raw["format"],
        files=files,
        chosen_topics=[str(t) for t in raw.get("chosen_topics", [])],
        custodians=raw.get("custodians", []),
        expected_message_count=raw.get("expected_message_count"),
    )
