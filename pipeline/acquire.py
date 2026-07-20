"""Corpus acquisition: download (when egress allows) or verify manually-dropped files.

Everything downstream keys off checksummed files under data/raw/<corpus>/, so this
step is fully separable: if the container can't reach a corpus host, Dan drops the
files in place and runs `acquire --verify-only`.

Manifest semantics (config/corpora/*.yaml):
  url: null    -> cannot download; verify-only for this entry
  sha256: null -> not yet pinned; recorded into the acquisition report on first
                  successful acquire (then copied into the yaml by hand and committed)
"""

import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import CorpusConfig, load_corpus_config, load_pipeline_config


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _entry_target(raw_root: Path, corpus: str, dest: str) -> Path:
    return raw_root / corpus / dest


def _verify_entry(target: Path, pinned_sha256: str | None) -> dict:
    """Return a status record for one manifest entry already on disk (or not)."""
    if not target.exists():
        return {"status": "missing"}
    if target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.is_file())
        if not files:
            return {"status": "missing"}
        # Directories are verified by aggregate digest over (relpath, file digest).
        agg = hashlib.sha256()
        for p in files:
            agg.update(str(p.relative_to(target)).encode())
            agg.update(bytes.fromhex(sha256_file(p)))
        actual = agg.hexdigest()
    else:
        actual = sha256_file(target)
    if pinned_sha256 is None:
        return {"status": "present_unpinned", "sha256": actual}
    if actual == pinned_sha256:
        return {"status": "verified", "sha256": actual}
    return {"status": "checksum_mismatch", "sha256": actual, "expected": pinned_sha256}


def _download_entry(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    # trec.nist.gov 403s the default Python-urllib agent (2026-07-20).
    req = urllib.request.Request(url, headers={"User-Agent": "ediscovery-pipeline/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.rename(target)


def acquire(corpus: str, verify_only: bool = False) -> dict:
    pipeline_cfg = load_pipeline_config()
    corpus_cfg: CorpusConfig = load_corpus_config(corpus)
    raw_root = pipeline_cfg.paths["raw"]

    report = {
        "corpus": corpus,
        "verify_only": verify_only,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "entries": {},
    }
    for entry in corpus_cfg.files:
        target = _entry_target(raw_root, corpus, entry.dest)
        record = _verify_entry(target, entry.sha256)
        if record["status"] == "missing" and not verify_only and entry.url:
            try:
                _download_entry(entry.url, target)
                record = _verify_entry(target, entry.sha256)
                record["downloaded_from"] = entry.url
            except OSError as e:
                record = {"status": "download_failed", "error": str(e), "url": entry.url}
        report["entries"][entry.name] = record

    artifacts = pipeline_cfg.paths["artifacts"]
    artifacts.mkdir(parents=True, exist_ok=True)
    report_path = artifacts / f"{corpus}_acquisition.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(args) -> int:
    report = acquire(args.corpus, verify_only=args.verify_only)
    bad = {n: r for n, r in report["entries"].items() if r["status"] not in ("verified", "present_unpinned")}
    for name, record in report["entries"].items():
        print(f"  {record['status']:18s} {name}")
    if bad:
        print(f"\n{len(bad)} of {len(report['entries'])} entries not ready "
              f"(see artifacts/{args.corpus}_acquisition.json)", file=sys.stderr)
        return 1
    print(f"\nall {len(report['entries'])} entries ready")
    return 0
